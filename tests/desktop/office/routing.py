"""Time four-stage requests over real workbook/deck pairs, with the same registered operations in every arm."""
import argparse,json,random,tempfile,time
from pathlib import Path
from itertools import permutations
from contracts import NAMES,KINDS,plan,model
from effects import OfficeTool,canonical
from study import grade,make,OUT

def request(x,p,lines,mode,library):
    tool=OfficeTool(x.read_bytes(),p.read_bytes())
    try:
        args=[canonical(line,x.name,p.name,tool.regions) for line in lines]
        names=[library.get((tool.signature,txt)) if mode=="reuse" else None for txt,_ in args]
        misses=[i for i,v in enumerate(names) if v is None]
        calls=len(misses) if mode=="serial" else int(bool(misses))
        if mode=="serial":
            for i in misses:names[i]=model([args[i][0]])[0]
        elif misses:
            for i,v in zip(misses,model([args[i][0] for i in misses])):names[i]=v
        plans=[plan(n) for n in names]
        for i,effect in enumerate(plans):tool.apply(effect,args[i][1])
        return tool.save(),len(lines)-len(misses),calls,names
    finally:tool.db.close()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--shard',type=int,default=0);args=ap.parse_args()
    (OUT/"inputs").mkdir(exist_ok=True);library={};admitted=0
    start=time.perf_counter()
    for j,(kind,name) in enumerate(zip(KINDS,NAMES)):
        for i in range(32):
            x,p,reg,lines=make(341741+i*81+j,[kind],[False])
            book,deck=x.read_bytes(),p.read_bytes();tool=OfficeTool(book,deck)
            try:
                txt,region=canonical(lines[0],x.name,p.name,tool.regions)
                tool.apply(plan(name),region)
                if not grade(tool.save(),deck,book,[kind],reg):raise AssertionError((kind,i))
                library[(tool.signature,txt)]=name;admitted+=1
            finally:tool.db.close()
    (OUT/"contracts-validation.json").write_text(json.dumps(dict(tool_cases=admitted,tools=len(library),validation_s=time.perf_counter()-start)))
    print('OFFICEVALIDATION', (OUT/'contracts-validation.json').read_text(), flush=True)
    with (OUT/'contracts-results.jsonl').open('w') as f:
        for i in range(args.shard*12,(args.shard+1)*12):
            seed=923009+79*i
            profile=('exact','mixed','unseen')[i%3]
            kinds=['copy_title','copy_total',['region_table','top_region','compute_total','top3_list'][(i//3+i%3)%4],
                   ('paste_chart','next_steps')[(i//3)%2]]
            flags=([False]*4 if profile=='exact' else ([False,True,False,False] if profile=='mixed' else [True]*4))
            x,p,region,lines=make(seed,kinds,flags);x0,p0=x.read_bytes(),p.read_bytes()
            for method in list(permutations(('batch','serial','reuse')))[i%6]:
                t=time.perf_counter();error=None;names=None;hits=None;calls=None
                try:
                    output,hits,calls,names=request(x,p,lines,method,library)
                    dest=x.parent/(method+'.pptx');dest.write_bytes(output)
                    correct=grade(dest.read_bytes(),p0,x0,kinds,region) and x.read_bytes()==x0 and p.read_bytes()==p0
                except (ValueError,KeyError,IndexError,RuntimeError) as e:
                    error=str(e);correct=False
                q=dict(seed=seed,profile=profile,method=method,valid=correct,seconds=time.perf_counter()-t,hits=hits,calls=calls,names=names,error=error)
                f.write(json.dumps(q)+'\n');f.flush();print('OFFICE',json.dumps(q),flush=True)

if __name__=='__main__':main()
