"""Prepare real document changes privately while an independent CPU model evaluates the proposed effects."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse,hashlib,json,random,tempfile,time,urllib.request
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
from legato.rl.bench import office
from effects import OfficeTool,canonical
from contracts import NAMES,KINDS,plan
from study import OUT,grade,make,ALT
from retrieval import vectors,setup,named,NOT_SUPPORTED,DEV_NEG,normalize

MODEL="https://huggingface.co/cross-encoder/nli-MiniLM2-L6-H768/resolve/b95119ce93d3e065de6214e38cd4a97b0f2f2c6d/"
ARGS=dict(x="SOURCE.xlsx",p="TARGET.pptx",extra="REQUESTED_REGION")
DECLARED={n:office.KINDS[k][1].format(**ARGS) for n,k in zip(NAMES,KINDS)}


class SemanticCheck:
    def __init__(self,root):
        for src,dest in (("tokenizer.json","nli-tokenizer.json"),("onnx/model_quint8_avx2.onnx","nli.onnx")):
            with urllib.request.urlopen(MODEL+src,timeout=100) as url:(root/dest).write_bytes(url.read())
        blob=root/"nli.onnx"
        if hashlib.sha256(blob.read_bytes()).hexdigest()!="44391a5241a62e0083c1a8899a71e69a092b95aea5ba89e14062925468eceac7":
            raise ValueError("NLI digest")
        opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(blob),opts,providers=["CPUExecutionProvider"])
        self.tokenizer=Tokenizer.from_file(str(root/"nli-tokenizer.json"));self.tokenizer.enable_padding()
        self.tokenizer.enable_truncation(max_length=256)
        pos=[(ALT[k].format(**ARGS),n) for k,n in zip(KINDS,NAMES)]
        neg=[(text,NAMES[(j+1)%len(NAMES)]) for j,(text,n) in enumerate(pos)]
        neg+=list(zip(DEV_NEG,NAMES))
        good=self.score(pos);bad=self.score(neg)
        # Frozen before task evaluation. Defer all below the most confident wrong dev pair.
        self.threshold=max(.5,max(bad)+1e-5)
        print("SHADOWCAL",json.dumps(dict(threshold=self.threshold,positive_accepted=sum(v>=self.threshold for v in good),
                           positives=len(good),negatives=len(bad))),flush=True)

    def score(self,pairs):
        query=[]
        for text,op in pairs:
            a,b=normalize(text),normalize(DECLARED[op]);query.extend([(a,b),(b,a)])
        encoded=self.tokenizer.encode_batch(query)
        feed={"input_ids":np.array([t.ids for t in encoded],dtype=np.int64),
              "attention_mask":np.array([t.attention_mask for t in encoded],dtype=np.int64),
              "token_type_ids":np.array([t.type_ids for t in encoded],dtype=np.int64)}
        values=self.session.run(None,{e.name:feed[e.name] for e in self.session.get_inputs()})[0]
        probs=np.exp(values-values.max(axis=1,keepdims=True));probs/=probs.sum(axis=1,keepdims=True)
        return [float(min(probs[i,1],probs[i+1,1])) for i in range(0,len(query),2)]


def stage(book,deck,names,args):
    tool=OfficeTool(book,deck)
    try:
        for n,(_,region) in zip(names,args):tool.apply(plan(n),region)
        return tool.save()
    finally:tool.db.close()


def main(shard):
    from shadow_cases import NEW,NEG
    (OUT/"inputs").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td, ThreadPoolExecutor(max_workers=1) as pool:
        root=Path(td); lookup=setup(vectors(root)); verifier=SemanticCheck(root)
        named(["Describe the workbook and leave it unchanged."])
        for i in range(shard*12,(shard+1)*12):
            seed=9552793+151*i;rng=random.Random(seed)
            profile=("registered","unseen","unsupported")[i%3]
            kinds=["copy_title","copy_total",("top_region","region_table","top3_list","compute_total")[(i//3+i%3)%4],
                   ("paste_chart","next_steps")[(i//3+i%3)%2]]
            rng.shuffle(kinds)
            x,p,region,original=make(seed,kinds,[False]*4);params=dict(x=x.name,p=p.name,extra=region)
            lines=original if profile=="registered" else [NEW[k][(i//3)%2].format(**params) for k in kinds]
            if profile=="unsupported":lines[i//3%4]=NEG[i//3].format(**params)
            # The request addresses one entire batch of changes. Withhold the output file if any part is unsupported.
            book=x.read_bytes();deck=p.read_bytes()
            reader=OfficeTool(book,deck);regions=reader.regions;reader.db.close()
            methods=["batch","hybrid","verify_then_stage","shadow"]
            rng.shuffle(methods)
            for method in methods:
                start=time.perf_counter();valid=False;written=False;prepared=False;error=None;names=[];scores=[]
                try:
                    args=[canonical(t,x.name,p.name,regions) for t in lines];text=[v[0] for v in args]
                    if method=="batch":names=named(text)
                    else:
                        names=[lookup(t) for t in text];miss=[j for j,v in enumerate(names) if v is None]
                        if miss:
                            for j,n in zip(miss,named([text[j] for j in miss])):names[j]=n
                    if any(n not in NAMES for n in names):raise ValueError("unresolved")
                    if method=="shadow":
                        future=pool.submit(stage,book,deck,names,args)
                        try:scores=verifier.score(list(zip(text,names)))
                        finally:output=future.result();prepared=True
                    elif method=="verify_then_stage":
                        scores=verifier.score(list(zip(text,names)))
                    if scores and min(scores)<verifier.threshold:raise ValueError("unresolved")
                    if not prepared:output=stage(book,deck,names,args);prepared=True
                    dest=x.parent/(method+".pptx");dest.write_bytes(output);written=True
                    valid=profile!="unsupported" and grade(dest.read_bytes(),deck,book,kinds,region)
                except (KeyError,ValueError,IndexError,RuntimeError) as exc:
                    error=str(exc);valid=profile=="unsupported" and error=="unresolved" and not written
                valid=valid and x.read_bytes()==book and p.read_bytes()==deck
                print("SHADOW",json.dumps(dict(seed=seed,profile=profile,method=method,names=names,scores=scores,
                      prepared=prepared,written=written,valid=valid,false_done=written and not valid,error=error,
                      seconds=time.perf_counter()-start)),flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--shard",type=int,default=0);main(ap.parse_args().shard)
