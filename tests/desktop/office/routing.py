"""Time four-stage requests over real workbook/deck pairs, with the same registered operations in every arm."""
import argparse,json,random,tempfile,time
from pathlib import Path
from itertools import permutations
from contracts import NAMES,KINDS,plan,model,compact
from effects import OfficeTool,canonical
from study import grade,make,OUT

def request(x,p,lines,mode,library,decode):
    tool=OfficeTool(x.read_bytes(),p.read_bytes())
    try:
        args=[canonical(line,x.name,p.name,tool.regions) for line in lines]
        names=[library.get((tool.signature,txt)) if mode=="reuse" else None for txt,_ in args]
        misses=[i for i,v in enumerate(names) if v is None]
        calls=len(misses) if mode=="serial" else int(bool(misses))
        if mode=="serial":
            for i in misses:names[i]=decode([args[i][0]])[0]
        elif misses:
            for i,v in zip(misses,decode([args[i][0] for i in misses])):names[i]=v
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
        for i in range(args.shard*16,(args.shard+1)*16):
            seed=1364251+83*i
            profile=('exact','mixed','unseen','unsupported')[i%4]
            kinds=['copy_title','copy_total',['region_table','top_region','compute_total','top3_list'][(i//4+i%4)%4],
                   ('paste_chart','next_steps')[(i//4)%2]]
            random.Random(seed+18).shuffle(kinds)
            flags=([False]*4 if profile=='exact' else ([False,True,False,False] if profile=='mixed' else [True]*4))
            x,p,region,lines=make(seed,kinds,flags);x0,p0=x.read_bytes(),p.read_bytes()
            if profile=='unsupported':
                lines[i//4%4]=('Send the slides to the finance mailing list.',
                                'Translate speaker notes into Italian.',
                                'Archive the workbook in the company document repository.')[i//4%3]
            arms=[(method,wire) for method in ('serial','batch','reuse') for wire in ('names','codes')]
            random.Random(seed+71).shuffle(arms)
            for method,wire in arms:
                t=time.perf_counter();error=None;names=None;hits=None;calls=None;correct=False;written=False
                dest=x.parent/(method+'-'+wire+'.pptx')
                try:
                    output,hits,calls,names=request(x,p,lines,method,library,compact if wire=='codes' else model)
                    dest.write_bytes(output);written=True
                    correct=(profile!='unsupported' and grade(dest.read_bytes(),p0,x0,kinds,region))
                except (ValueError,KeyError,IndexError,RuntimeError) as exc:
                    error=str(exc)
                    correct=(profile=='unsupported' and error=='clarification required' and not dest.exists())
                correct=correct and x.read_bytes()==x0 and p.read_bytes()==p0
                q=dict(seed=seed,profile=profile,method=method,wire=wire,valid=correct,seconds=time.perf_counter()-t,
                       false_done=written and not correct,hits=hits,calls=calls,names=names,error=error)
                f.write(json.dumps(q)+'\n');f.flush();print('WIREDOC',json.dumps(q),flush=True)

if __name__=='__main__':main()
