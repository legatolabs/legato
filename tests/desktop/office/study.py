"""Cold generation, approved-plan reuse and an independent full-artifact grade on fresh workbook/deck pairs."""
import hashlib
import io
import json
from pathlib import Path
import random
import re
import sqlite3
import statistics
import tempfile
import time
from itertools import permutations
from openpyxl import load_workbook
from pptx import Presentation
from legato.rl.bench import office
from effects import OfficeTool,canonical,model

OUT=Path(__file__).resolve().parent
ALT={
 "copy_title":"Set slide 1's heading in {p} to the contents of the Summary sheet's A1 in {x}.",
 "copy_total":"Read Summary!B2 in {x} and put the number by itself in the subtitle of slide 1 in {p}.",
 "top_region":"On slide 2 in {p}, make the heading the region with the greatest summed Revenue in {x}.",
 "top3_list":"Set the body of slide 2 in {p} to the three leading regions in {x} by summed revenue, descending, each on a separate line.",
 "compute_total":"Sum every revenue amount on Data in {x}. Put 'Total: ' and that sum in the body on slide 2 of {p}.",
 "region_table":"Insert a Region / Total table into slide 2 of {p}. Group the Data rows of {x} by region and total their revenue.",
 "next_steps":"Extend {p} by one slide titled 'Next Steps'. Its body must read 'Grow ' followed by the lowest-revenue region across the Data rows of {x}.",
 "paste_chart":"Add a chart on slide 3 of {p} for the {extra} region from {x}, showing Revenue by month in chronological order."}
def snapshot(blob):
    p=Presentation(io.BytesIO(blob));ss=[]
    for s in p.slides:
        tbl=[];charts=[]
        for sh in s.shapes:
            if sh.has_table:tbl.append([[c.text for c in row.cells] for row in sh.table.rows])
            if sh.has_chart:
                ch=sh.chart
                charts.append(([c.label for c in ch.plots[0].categories],[list(v.values) for v in ch.series]))
        ss.append(dict(title=s.shapes.title.text,body=s.placeholders[1].text if 1 in [q.placeholder_format.idx for q in s.placeholders] else "",
                       tables=tbl,charts=charts,shapes=len(s.shapes)))
    return ss


def grade(doc,initial,book,kinds,region):
    # Read source and output separately from the action tool. Compare all slides, including unaffected objects.
    wb=load_workbook(io.BytesIO(book),read_only=True,data_only=True)
    rows=list(wb["Data"].values)[1:];summary=wb["Summary"]
    totals={};months=[];revenue=[]
    for reg,mon,val in rows:
        totals[reg]=totals.get(reg,0)+val
        if reg==region:months.append(mon);revenue.append(val)
    ranked=sorted(totals,key=totals.get,reverse=True)
    want=snapshot(initial)
    for kind in kinds:
        if kind=="copy_title":want[0]["title"]=summary["A1"].value
        elif kind=="copy_total":want[0]["body"]=str(summary["B2"].value)
        elif kind=="top_region":want[1]["title"]=ranked[0]
        elif kind=="top3_list":want[1]["body"]="\n".join(ranked[:3])
        elif kind=="compute_total":want[1]["body"]="Total: "+str(sum(totals.values()))
        elif kind=="region_table":
            want[1]["tables"].append([["Region","Total"]]+[[r,str(totals[r])] for r in totals])
            want[1]["shapes"]+=1
        elif kind=="paste_chart":
            want[2]["charts"].append((months,[revenue]));want[2]["shapes"]+=1
        elif kind=="next_steps":
            want.append(dict(title="Next Steps",body="Grow "+ranked[-1],tables=[],charts=[],shapes=2))
    wb.close()
    have=snapshot(doc)
    for row in have+want:
        for table in row["tables"]:
            if table:table[:]=[table[0]]+sorted(table[1:])
    return have==want


def make(seed,kinds,wordings):
    d=office._data(random.Random(seed))
    root=Path(tempfile.mkdtemp(dir=OUT/"inputs"));x=root/f"source{seed}.xlsx";p=root/f"target{seed}.pptx"
    office.write_xlsx(d,x,True);office.write_pptx(p)
    region=d["regions"][seed%len(d["regions"])]
    lines=[(ALT[k] if alt else office.KINDS[k][1]).format(x=x.name,p=p.name,extra=region) for k,alt in zip(kinds,wordings)]
    return x,p,region,lines


def run_task(x,p,lines,method,library):
    tool=OfficeTool(x.read_bytes(),p.read_bytes());hits=0;calls=0
    try:
        items=[canonical(line,x.name,p.name,tool.regions) for line in lines]
        keys=[(tool.signature,t) for t,_ in items]
        plans=[library.get(k) if method=="reuse" else None for k in keys]
        hits=sum(v is not None for v in plans)
        misses=[i for i,v in enumerate(plans) if v is None]
        if method=="serial":
            for i in misses:
                calls+=1;plans[i]=model([items[i][0]])[0]
        elif misses:
            calls+=1
            for i,plan in zip(misses,model([items[i][0] for i in misses])):plans[i]=plan
        for plan,(_,region) in zip(plans,items):tool.apply(plan,region)
        return tool.save(),hits,calls
    finally:tool.db.close()


def main():
    (OUT/"inputs").mkdir(exist_ok=True)
    library={}
    dev=list(office.KINDS)
    with (OUT/"enrollment.jsonl").open("w") as log:
        for j,k in enumerate(dev):
            x,p,reg,lines=make(752351+j,[k],[False])
            tool=OfficeTool(x.read_bytes(),p.read_bytes())
            text,region=canonical(lines[0],x.name,p.name,tool.regions);key=(tool.signature,text);tool.db.close()
            plan=None;good=False;error=None;start=time.perf_counter()
            try:
                plan=model([text])[0]
                # Admission requires exact values and unaffected content on two different documents.
                for n in (444711+j,229001+j):
                    xx,pp,rr,ll=make(n,[k],[False]);tool=OfficeTool(xx.read_bytes(),pp.read_bytes())
                    try:
                        txt,rg=canonical(ll[0],xx.name,pp.name,tool.regions)
                        if (tool.signature,txt)!=key:raise RuntimeError("layout or command differs")
                        tool.apply(plan,rg);b=tool.save()
                        if not grade(b,pp.read_bytes(),xx.read_bytes(),[k],rr):raise ValueError("failed training grade")
                    finally:tool.db.close()
                library[key]=plan;good=True
            except (ValueError,IndexError,KeyError,RuntimeError,sqlite3.Error) as e:error=str(e)
            log.write(json.dumps(dict(kind=k,plan=plan,admitted=good,error=error,seconds=time.perf_counter()-start))+"\n");log.flush()
    print("enrolled",len(library),flush=True)
    with (OUT/"results.jsonl").open("w") as log:
        for i in range(24):
            seed=237029+i*117
            profile=("exact","mixed","unseen")[i%3]
            kinds=["copy_title","copy_total",["top_region","top3_list","compute_total","region_table"][(i//3+i%3)%4],
                   ("next_steps","paste_chart")[(i//3)%2]]
            wd=[False]*4 if profile=="exact" else ([False,False,True,False] if profile=="mixed" else [True]*4)
            x,p,region,lines=make(seed,kinds,wd)
            x0=x.read_bytes();p0=p.read_bytes()
            for method in list(permutations(("serial","batch","reuse")))[i%6]:
                start=time.perf_counter();error=None;hits=0;calls=0
                try:
                    output,hits,calls=run_task(x,p,lines,method,library)
                    # Persist and reopen the produced deck. A fresh independent reader grades it.
                    saved=x.parent/(method+".pptx");saved.write_bytes(output)
                    valid=grade(saved.read_bytes(),p0,x0,kinds,region) and x.read_bytes()==x0 and p.read_bytes()==p0
                except (ValueError,KeyError,IndexError,RuntimeError,sqlite3.Error) as e:
                    error=type(e).__name__+": "+str(e);valid=False
                row=dict(seed=seed,profile=profile,method=method,valid=valid,seconds=time.perf_counter()-start,hits=hits,calls=calls,
                         error=error,source_sha256=hashlib.sha256(x0).hexdigest(),kinds=kinds)
                log.write(json.dumps(row)+"\n");log.flush();print(i,method,valid,round(row["seconds"],2),flush=True)


if __name__=="__main__":main()
