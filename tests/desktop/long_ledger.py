"""Paired native UI test. The controller and the independent grader own separate views of each ledger."""
import argparse
import json
import itertools
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time

from native_receipt import TitleReader

N = 12
COUNT = 8
ROOT = Path(__file__).resolve().parents[2]


def load(p):
    end = time.monotonic() + .6
    while True:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except PermissionError:
            if time.monotonic() >= end:
                raise
            time.sleep(.004)


def store(p, value):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    end = time.monotonic() + 3
    while True:
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if time.monotonic() >= end:
                raise
            time.sleep(.004)


def wait(fn, timeout=10):
    end = time.monotonic() + timeout
    while not fn():
        if time.monotonic() >= end:
            raise TimeoutError("fixture readiness")
        time.sleep(.01)


def case(seed):
    rng = random.Random(seed + 731339)
    original = [str(rng.randrange(100, 800)) for _ in range(N)]
    rows = rng.sample(range(N - 1), 5)
    targets = rows + [rows[1], rows[0], rows[3]]
    steps = []
    for i, row in enumerate(targets):
        if i in (2, 6):
            steps.append(["copy",row,targets[i-1]])
        elif i in (4, 7):
            steps.append(["add",row,rng.randrange(1,14)])
        else:
            steps.append(["set",row,rng.randrange(100,800)])
    return original, steps


def number(epoch,rev,phase,offset,row=0,value=0):
    return (((((epoch*16+rev)*4+phase)*N+offset)*N+row)*1000+value)


def parts(raw):
    raw,value = divmod(raw,1000)
    raw,row = divmod(raw,N)
    raw,offset = divmod(raw,N)
    raw,phase = divmod(raw,4)
    epoch,rev = divmod(raw,16)
    return epoch,rev,phase,offset,row,value


def fixture(folder):
    import tkinter as tk
    win=tk.Tk()
    prefix="Legato ledger "+folder.name+" "
    win.title(prefix+"0")
    win.geometry("540x350+70+80")
    win.attributes("-topmost",True)
    canvas=tk.Canvas(win,width=520,height=215,highlightthickness=0,bg="white")
    canvas.pack()
    field=tk.StringVar()
    entry=tk.Entry(win,textvariable=field,font=("Arial",18))
    entry.pack()
    state={"epoch":0,"rev":0,"offset":0,"phase":0,"row":0,"locked":False}
    def title(phase):
        state["phase"]=phase
        val=field.get()
        value=int(val) if val.isdigit() and len(val)==3 and phase==2 else 0
        win.title(prefix+str(number(state["epoch"],state["rev"],phase,state["offset"],state["row"],value)))
    def draw():
        canvas.delete("all")
        for pos in range(N):
            row=(pos+state["offset"])%N
            x,y=(pos%4)*128,(pos//4)*70
            canvas.create_rectangle(x+3,y+3,x+125,y+65,fill="#e1edff",outline="#374f70")
            canvas.create_text(x+64,y+33,text=f"Account {row+1:02d}")
        canvas.update_idletasks()
    def log(kind,row,value):
        with (folder/"audit.jsonl").open("a",encoding="utf-8") as f:
            f.write(json.dumps([state["epoch"],kind,row,value])+"\n")
    def data(status,rev,records):
        store(folder/"document.json",dict(epoch=state["epoch"],revision=rev,status=status,records=records))
    def select(event):
        if state["locked"]:return
        pos=event.x//128+4*(event.y//70)
        if pos>=N:return
        state["row"]=(pos+state["offset"])%N
        field.set("")
        entry.focus_force()
        title(1)
        log("select",state["row"],None)
    def typed(*args):
        if not state["locked"] and len(field.get())==3 and field.get().isdigit():
            title(2)
    def submit(_):
        if state["locked"] or state["phase"]!=2:return
        ix=state["rev"]; epoch=state["epoch"]; row=state["row"]; value=field.get()
        state["locked"]=True
        log("submit",row,value)
        old=state["records"][:]; proposed=old[:]; proposed[row]=value
        fault=state["fault"] if ix==state["index"] else "ok"
        if fault=="dropped":return
        def current():return epoch==state["epoch"]
        def advance():
            state.update(rev=ix+1,row=0,offset=(state["offset"]+3+ix)%N)
            draw();title(0)
        def finish():
            if not current():return
            if fault=="rollback":
                data("committed",ix,old)
                state.update(rev=ix,row=0,offset=(state["seed"]+3*ix+ix*(ix-1)//2)%N)
                draw();title(0)
                return
            state["records"]=proposed
            data("committed",ix+1,proposed)
            log("commit",row,value)
            if fault!="lost":
                state["locked"]=False
        def stage():
            if not current():return
            data("pending",ix+1,proposed)
            if fault!="lost":advance()
            gap=180 if fault=="rollback" else state["gaps"][ix]
            win.after(gap,finish)
        win.after(state["delays"][ix],stage)
    def poll():
        p=folder/"control.json"
        if p.exists():
            spec=load(p)
            if spec["epoch"]!=state["epoch"]:
                state.update(spec,rev=0,phase=0,row=0,offset=spec["seed"]%N,locked=False)
                field.set("");draw();title(0)
                data("committed",0,state["records"])
                store(folder/"ready.json",dict(epoch=state["epoch"],x=canvas.winfo_rootx(),y=canvas.winfo_rooty()))
        win.after(18,poll)
    canvas.bind("<Button-1>",select)
    entry.bind("<Return>",submit)
    field.trace_add("write",typed)
    def start():
        win.lift();win.focus_force()
        store(folder/"owner.json",dict(pid=os.getpid(),prefix=prefix))
        poll()
    win.after(300,start)
    win.mainloop()


def worker(folder,arm):
    from legato import Action,local
    import legato
    assert Path(legato.__file__).resolve().is_relative_to(Path(os.environ["TEST_SDK"]).resolve())
    reader=TitleReader(**load(folder/"owner.json"))
    desktop=local(hide_terminal=False,fixed_wait_s=0 if arm=="tuned" else None)
    def act(action,read,accept):
        if arm=="candidate":
            return desktop.act(action,read=read,until=accept,timeout_s=1.8)
        desktop.act(action)
        end=time.monotonic()+1.8
        while True:
            observed=read()
            if accept(observed):return observed
            if time.monotonic()>=end:raise TimeoutError()
            time.sleep(.01)
    print("ready",flush=True)
    try:
        for line in sys.stdin:
            job=json.loads(line)
            epoch=job["epoch"]; known=load(folder/"document.json")["records"]; reads=0; done=0; model_s=0
            start=time.perf_counter(); stop="complete"; attempted=0
            try:
                for ix,step in enumerate(job["steps"]):
                    mode,row,arg=step
                    if job.get("live"):
                        from ledger_model import infer
                        t=time.perf_counter()
                        mode,row,arg=infer(step,ix,known)
                        model_s+=time.perf_counter()-t
                    if mode=="set":value=f"{arg:03}"
                    elif mode=="copy":value=known[arg]
                    elif mode=="add":value=f"{int(known[row])+arg:03}"
                    else:raise ValueError(mode)
                    ep,rev,phase,offset,_,_=parts(reader.value())
                    if (ep,rev,phase)!=(epoch,ix,0):raise RuntimeError("unexpected document revision")
                    pos=(row-offset)%N
                    attempted+=1
                    act(Action("click",x=job["x"]+64+128*(pos%4),y=job["y"]+33+70*(pos//4)),
                        reader.value,lambda v:parts(v)==(epoch,ix,1,offset,row,0))
                    attempted+=1
                    act(Action("type",text=value),reader.value,lambda v:parts(v)==(epoch,ix,2,offset,row,int(value)))
                    def read():
                        nonlocal reads
                        reads+=1
                        return parts(reader.value()),load(folder/"document.json")
                    def matches(result):
                        (ep,r,ph,_,_,_),doc=result
                        return ((ep,r,ph)==(epoch,ix+1,0) and doc["epoch"]==epoch and doc["revision"]==ix+1
                                and (job["weak"] or doc["status"]=="committed") and doc["records"][row]==value)
                    attempted+=1
                    _,doc=act(Action("key",keys="Return"),read,matches)
                    known=doc["records"]
                    done+=1
            except (TimeoutError,ValueError,RuntimeError,IndexError) as exc:
                stop=type(exc).__name__
            print(json.dumps(dict(status=stop,completed=done,actions=attempted,ms=(time.perf_counter()-start)*1000,
                                  model_ms=model_s*1000,reads=reads)),flush=True)
    finally:
        reader.close();desktop.close()


def grade(initial,steps,raw,doc,fault,index,out):
    # Reference interpreter and independent append-only input/commit journal, invisible to the controller.
    expected=list(initial); intended=[]; selected=[]; submitted=[]; committed=[]
    for op,row,n in steps:
        if op=="copy":expected[row]=expected[n]
        if op=="set":expected[row]=str(n).zfill(3)
        if op=="add":expected[row]=str(n+int(expected[row])).zfill(3)
        intended.append((row,expected[row]))
    for _,kind,row,val in raw:
        if kind=="select":selected.append(row)
        if kind=="submit":submitted.append((row,val))
        if kind=="commit":committed.append((row,val))
    count=COUNT if fault=="ok" else index+int(fault=="lost")
    submits=COUNT if fault=="ok" else index+1
    wanted=intended[:count]
    wanted_doc=initial[:]
    for row,val in wanted:wanted_doc[row]=val
    correct_doc=(doc["records"]==wanted_doc and doc["revision"]==count and doc["status"]=="committed")
    wrong_write=committed!=intended[:len(committed)] or len(committed)>count
    wrong_effect=(wrong_write or submitted!=intended[:len(submitted)]
                  or len(submitted)>submits or selected!=[r for r,v in intended[:len(selected)]] or len(selected)>submits)
    exact=correct_doc and committed==wanted and submitted==intended[:submits] and selected==[r for r,v in intended[:submits]]
    success=out["status"]=="complete"
    false_done=success and (fault!="ok" or not exact)
    correct_signal=success if fault=="ok" else (out["status"]=="TimeoutError" and out["completed"]==index)
    return dict(valid=bool(exact and correct_signal),file_matches=correct_doc,false_done=false_done,
                wrong_effect=wrong_effect,wrong_write=wrong_write,expected_signal=correct_signal,selections=len(selected),
                commits=len(committed),submits=len(submitted))


def run(args):
    with tempfile.TemporaryDirectory() as td:
        folder=Path(td);fixture_proc=subprocess.Popen([sys.executable,__file__,"--fixture",td])
        workers={}
        try:
            wait(lambda:(folder/"owner.json").exists())
            for arm,checkout in [("baseline",args.baseline),("tuned",args.baseline),("candidate",args.candidate)]:
                checkout=str(Path(checkout).resolve())
                proc=subprocess.Popen([sys.executable,"-u",__file__,"--worker",td,arm],cwd=checkout,
                    env={**os.environ,"PYTHONPATH":checkout,"TEST_SDK":checkout},
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,
                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                workers[arm]=proc
                if proc.stdout.readline().strip()!="ready":raise RuntimeError(arm)
            for slot in range(args.cases):
                seed=12097+args.shard*101+slot
                if slot<12 or slot>=20:fault,weak="ok",False
                elif slot<18:fault,weak=["dropped","lost","rollback"][(slot-12)%3],False
                else:fault,weak="rollback",True
                initial,steps=case(seed)
                rng=random.Random(seed);index=(COUNT-1 if weak else rng.randrange(3,COUNT))
                delays=[rng.choice([0,7,38,81]) for _ in steps]
                gaps=[rng.choice([0,5,18,52]) for _ in steps]
                if slot<20 and not args.live:
                    arms=list(itertools.permutations(("baseline","tuned","candidate")))[(slot+args.shard)%6]
                else:
                    ref="baseline" if args.live else "tuned"
                    arms=(ref,"candidate") if (args.shard+slot)%2==0 else ("candidate",ref)
                for order,arm in enumerate(arms):
                    epoch=10+3*slot+order
                    store(folder/"control.json",dict(epoch=epoch,records=initial,seed=seed,
                          fault=fault,index=index,delays=delays,gaps=gaps))
                    wait(lambda:(folder/"ready.json").exists() and load(folder/"ready.json")["epoch"]==epoch)
                    msg=dict(**load(folder/"ready.json"),steps=steps,weak=weak,live=args.live)
                    proc=workers[arm]
                    proc.stdin.write(json.dumps(msg)+"\n");proc.stdin.flush()
                    result=json.loads(proc.stdout.readline())
                    # Check independently after any scheduled rollback or acknowledgement has had time to arrive.
                    time.sleep(.35)
                    audit=folder/"audit.jsonl"
                    raw=[r for l in audit.read_text().splitlines() if (r:=json.loads(l))[0]==epoch] if audit.exists() else []
                    doc=load(folder/"document.json")
                    checked=grade(initial,steps,raw,doc,fault,index,result)
                    print("LONGTASK",json.dumps(dict(arm=arm,seed=seed,shard=args.shard,slot=slot,live=args.live,
                                      fault=fault,weak=weak,index=index,**result,**checked)),flush=True)
        finally:
            for proc in workers.values():
                proc.stdin.close()
                proc.wait(timeout=6)
            fixture_proc.terminate();fixture_proc.wait(timeout=6)


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--fixture",type=Path);ap.add_argument("--worker",nargs=2)
    ap.add_argument("--shard",type=int,default=0);ap.add_argument("--cases",type=int,default=76)
    ap.add_argument("--live",action="store_true")
    ap.add_argument("--baseline",default="baseline");ap.add_argument("--candidate",default="candidate")
    a=ap.parse_args()
    if a.fixture:fixture(a.fixture)
    elif a.worker:worker(Path(a.worker[0]),a.worker[1])
    else:run(a)
