"""Compare a fixed semantic tool schema with a local static-embedding index on four-instruction deck requests."""
from pathlib import Path
from itertools import product
import argparse, hashlib, json, random, re, tempfile, time, urllib.request
import numpy as np
from safetensors.numpy import load_file
from tokenizers import Tokenizer
from legato.rl.bench import office
from effects import OfficeTool,canonical
from contracts import NAMES,KINDS,TEXT,plan
from study import OUT,grade,make,ALT

BASE="https://huggingface.co/minishlab/potion-base-8M/resolve/bf8b056651a2c21b8d2565580b8569da283cab23/"
NOT_SUPPORTED="clarify"
DEV_NEG=("Send the presentation to the board for signature.","Translate every slide into French.",
         "Save a PDF under another name.","Delete the lowest performing region from the workbook.",
         "Fill speaker notes with the workbook history.","Write the revenue details to an email.",
         "Change the theme on every slide.","Prepare a Word document containing the entire workbook.")


def normalize(t):
    return t.lower().replace("source.xlsx","workbook").replace("target.pptx","presentation").replace("requested_region","named")


def vectors(work):
    for name in ("tokenizer.json","model.safetensors"):
        with urllib.request.urlopen(BASE+name,timeout=60) as src:(work/name).write_bytes(src.read())
    weights=work/"model.safetensors"
    if hashlib.sha256(weights.read_bytes()).hexdigest()!="f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2":
        raise ValueError("embedding digest")
    v=load_file(str(weights))["embeddings"]
    tok=Tokenizer.from_file(str(work/"tokenizer.json"))
    def embed(text):
        ids=tok.encode(normalize(text),add_special_tokens=False).ids
        if not ids:return np.zeros(v.shape[1])
        result=v[ids].mean(axis=0)
        return result/max(1e-8,np.linalg.norm(result))
    return embed


def named(lines):
    enum=NAMES+[NOT_SUPPORTED];keys=[f"instruction_{i+1}" for i in range(len(lines))]
    schema={"type":"object","properties":{k:{"type":"string","enum":enum} for k in keys},"required":keys,"additionalProperties":False}
    system=("Choose one registered operation for each instruction. If the requested effect is different, cannot be "
            "supported, is negated, or must not be applied, choose clarify. Definitions:\n"+
            "\n".join(k+" = "+v for k,v in TEXT.items()))
    body={"model":"legato-overall","messages":[{"role":"system","content":system},
          {"role":"user","content":"\n".join(k+": "+line for k,line in zip(keys,lines))}],
          "temperature":0,"seed":3571,"max_tokens":140,"cache_prompt":False,
          "response_format":{"type":"json_schema","json_schema":{"name":"effects","strict":True,"schema":schema}}}
    req=urllib.request.Request("http://127.0.0.1:18714/v1/chat/completions",json.dumps(body).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=150) as res:c=json.load(res)["choices"][0]
    if c["finish_reason"]!="stop":raise ValueError("incomplete decision")
    obj=json.loads(c["message"]["content"])
    return [obj[k] for k in keys]


def setup(embed):
    params=dict(x="SOURCE.xlsx",p="TARGET.pptx",extra="REQUESTED_REGION")
    references=[(n,embed(t)) for n,k in zip(NAMES,KINDS) for t in (TEXT[n],office.KINDS[k][1].format(**params))]
    def rank(line):
        query=embed(line)
        scores={n:max(float(np.dot(v,query)) for k,v in references if k==n) for n in NAMES}
        best,runner=sorted(scores,key=scores.get,reverse=True)[:2]
        return best,scores[best],scores[best]-scores[runner]
    dev=[(rank(ALT[k].format(**params)),n) for k,n in zip(KINDS,NAMES)]+[(rank(t),NOT_SUPPORTED) for t in DEV_NEG]
    options=[]
    for threshold,margin in product((.4,.5,.6,.7,.8,.9),(.02,.05,.1,.15,.2)):
        admitted=[(guess,want) for (guess,cos,delta),want in dev if cos>=threshold and delta>=margin]
        if all(g==w for g,w in admitted):
            options.append((len(admitted),threshold,margin))
    if not options:raise ValueError("no valid dev threshold")
    hits,threshold,margin=max(options)
    print("RETRIEVALCAL",json.dumps(dict(admitted=hits,calibration=len(dev),threshold=threshold,margin=margin)),flush=True)
    def lookup(line):
        name,cos,delta=rank(line)
        return name if cos>=threshold and delta>=margin else None
    return lookup


def run(args):
    from retrieval_cases import PHRASES,NEGATIVES
    (OUT/"inputs").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        lookup=setup(vectors(Path(td)))
        named(["File the presentation under a new name."])
        for i in range(args.shard*16,(args.shard+1)*16):
            seed=7851391+127*i;rng=random.Random(seed)
            profile=("registered","paraphrase","paraphrase","unsupported")[i%4]
            kinds=["copy_title","copy_total",("top_region","top3_list","compute_total","region_table")[(i//4+i%4)%4],
                   ("next_steps","paste_chart")[(i//4+i%4)%2]]
            rng.shuffle(kinds)
            x,p,region,initial=make(seed,kinds,[False]*4)
            params=dict(x=x.name,p=p.name,extra=region)
            lines=initial if profile=="registered" else [rng.choice(PHRASES[k]).format(**params) for k in kinds]
            if profile=="unsupported":lines[i//4%4]=NEGATIVES[i//4%len(NEGATIVES)].format(**params)
            source=x.read_bytes();deck=p.read_bytes()
            strategies=["serial","batch","retrieval","retrieve_then_model"]
            rng.shuffle(strategies)
            for method in strategies:
                start=time.perf_counter(); tool=OfficeTool(x.read_bytes(),p.read_bytes())
                calls=hits=0;names=[];error=None;written=False;ok=False
                try:
                    argslist=[canonical(t,x.name,p.name,tool.regions) for t in lines]
                    text=[a for a,b in argslist]
                    if method=="serial":
                        for line in text:calls+=1;names+=named([line])
                    elif method=="batch":calls=1;names=named(text)
                    else:
                        names=[lookup(line) for line in text]
                        absent=[j for j,v in enumerate(names) if v is None];hits=len(text)-len(absent)
                        if absent and method=="retrieve_then_model":
                            calls=1
                            for j,op in zip(absent,named([text[j] for j in absent])):names[j]=op
                    if any(v is None or v==NOT_SUPPORTED for v in names):raise ValueError("clarification required")
                    for n,(_,reg) in zip(names,argslist):tool.apply(plan(n),reg)
                    dest=x.parent/(method+".pptx");dest.write_bytes(tool.save());written=True
                    ok=profile!="unsupported" and grade(dest.read_bytes(),deck,source,kinds,region)
                except (KeyError,ValueError,IndexError,RuntimeError) as ex:
                    error=str(ex);ok=profile=="unsupported" and error=="clarification required" and not written
                finally:tool.db.close()
                ok=ok and x.read_bytes()==source and p.read_bytes()==deck
                print("RETRIEVED",json.dumps(dict(seed=seed,profile=profile,method=method,valid=ok,seconds=time.perf_counter()-start,
                             false_done=written and not ok,hits=hits,calls=calls,names=names,error=error)),flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--shard",type=int,default=0);run(parser.parse_args())
