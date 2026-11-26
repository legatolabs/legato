"""Native event loop with a live model, a revision-checked input and an independent persisted-effect grader."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from long_ledger import load, store, wait
from native_receipt import TitleReader
from conditional_language import REQUESTS

BOOK = (
    ("If a record is urgent, expedite it. Otherwise file it.", "Expedite only the urgent entries and file the rest."),
    ("Review the flagged entries and file every other one.", "Send flagged records for review. File everything else."),
    ("Review flagged entries first. For the rest, expedite the urgent and file the ordinary.",
     "Flags take precedence and go to review. Of the remaining items, only urgent ones get expedited; file the others."),
    ("Expedite urgent records first. Otherwise review flagged ones, and file the rest.",
     "Urgency wins when both are present: expedite urgent rows, review other flagged rows, file everything else."),
    ("Forward blue records to the design team.", "Escalate if the author reports a problem in their own words."),
)
# Rows indexed by (urgent << 1 | flagged). Values: file=1, expedite=2, review=3.
TABLES = ((1, 1, 2, 2), (1, 3, 1, 3), (1, 3, 2, 3), (1, 3, 2, 2))
DESCRIPTIONS = ("expedite urgent else file", "review flagged else file",
                "review flagged else expedite urgent else file", "expedite urgent else review flagged else file",
                "outside these four rules")
SYSTEM = "Choose the exact supported event-handler policy requested by the caller. Reply with its single digit. " + " ".join(
    f"{i}={desc}." for i, desc in enumerate(DESCRIPTIONS))
ROOT = Path(__file__).resolve().parents[2]


def choose(system, instruction, vocab):
    body = {"model":"legato-overall", "temperature":0, "seed":5109, "max_tokens":8, "cache_prompt":False,
            "messages":[{"role":"system","content":system},{"role":"user","content":instruction}],
            "response_format":{"type":"json_schema","json_schema":{"name":"rule","strict":True,
                               "schema":{"type":"integer","enum":list(range(vocab))}}}}
    req = urllib.request.Request("http://127.0.0.1:18714/v1/chat/completions", json.dumps(body).encode(),
                                 headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=60) as resp:
        out = json.load(resp)["choices"][0]
    if out["finish_reason"] != "stop":
        raise ValueError("incomplete rule")
    value = json.loads(out["message"]["content"])
    if type(value) is not int or value not in range(vocab):
        raise ValueError("unknown rule id")
    return value


def select_rule(instruction):
    return choose(SYSTEM,instruction,5)


def reactive(instruction,flags):
    system=("You route an incoming record according to the developer's request. "
            "The only available fields are urgent and flagged. Reply with one digit. "
            "1=file, 2=expedite, 3=review. Use 0=stop if routing has not been authorized or if the request "
            "requires any missing information or unavailable operation. Never approximate an unsupported request.")
    observed=f"Current record: urgent={bool(flags & 2)}, flagged={bool(flags & 1)}."
    return choose(system,instruction+"\n"+observed,4)


def oracle(rule, flags):
    urgent, flagged = flags >= 2, flags % 2 == 1
    if rule == 0: return 2 if urgent else 1
    if rule == 1: return 3 if flagged else 1
    if rule == 2: return 3 if flagged else 2 if urgent else 1
    if rule == 3: return 2 if urgent else 3 if flagged else 1
    return 0


def fixture(folder):
    import tkinter as tk
    app = tk.Tk()
    prefix = "Legato event queue " + folder.name + " "
    app.geometry("480x220+40+60")
    app.attributes("-topmost",True)
    label = tk.Label(app,font=("Arial",14)); label.pack()
    var = tk.StringVar()
    entry = tk.Entry(app,textvariable=var); entry.pack()
    conn = sqlite3.connect(folder/"events.sqlite")
    conn.execute("CREATE TABLE IF NOT EXISTS audit (epoch, position, revision, flags, route)")
    state = {"epoch":0, "step":0, "rev":0, "flags":0, "count":0}

    def title(*_):
        raw = var.get()
        echo = int(raw) if raw.isdigit() and len(raw) <= 5 else 0
        app.title(prefix + str((state["epoch"]*1000000 + state["rev"])*100000+echo))

    def publish():
        store(folder/"visible.json",{k:state[k] for k in ("epoch","step","rev","flags","count")})
        label.config(text=f"Item {state['step']+1}, flags {state['flags']}, revision {state['rev']}")
        title()

    def submit(_):
        raw = var.get()
        var.set("")
        if len(raw)!=5 or not raw.isdigit() or state["step"] >= state["count"]: return
        observed, route = int(raw[:-1]), int(raw[-1])
        if route not in {1,2,3}: return
        i=state["step"]
        if i in state["changes"] and i not in state["fired"]:
            state["fired"].add(i)
            state["flags"] ^= 3
            state["rev"] += 1
        if state["guard"] and observed != state["rev"]:
            publish()
            return
        with conn:
            conn.execute("INSERT INTO audit VALUES (?,?,?,?,?)",
                         (state["epoch"],i,state["rev"],state["flags"],route))
        state["step"]+=1
        state["rev"]+=1
        if state["step"]<state["count"]:
            state["flags"]=state["incoming"][state["step"]]
        publish()

    def poll():
        p = folder/"job.json"
        if p.exists():
            j=load(p)
            if j["epoch"]!=state["epoch"]:
                state.update(j,step=0,rev=1,flags=j["incoming"][0],fired=set())
                var.set(""); publish()
                app.lift(); entry.focus_force()
        app.after(10,poll)

    app.bind("<Return>",submit)
    var.trace_add("write",title)
    title()
    def start():
        store(folder/"owner.json", {"pid":os.getpid(),"prefix":prefix})
        poll()
    app.after(220,start)
    app.mainloop()


def run(args):
    from legato import local, Action
    desktop=local(hide_terminal=False)
    with tempfile.TemporaryDirectory() as d:
        folder=Path(d)
        proc=subprocess.Popen([sys.executable,__file__,"--fixture",d])
        try:
            wait(lambda:(folder/"owner.json").exists())
            reader=TitleReader(**load(folder/"owner.json"))
            try:
                select_rule(BOOK[0][0])
                # All 60 distinct held-out requests run against every possible state. The matrix is
                # model-only; its 4 cells assess one request and share a ground truth.
                for query in range(args.shard,60,2):
                    expected=index=query%5
                    text=REQUESTS[index][query//5]
                    compiled=select_rule(text)
                    actions=[reactive(text,flags) for flags in range(4)]
                    wanted=[oracle(expected,flags) for flags in range(4)]
                    print("LANGUAGE",json.dumps(dict(query=query,rule=expected,text=text,compiled=compiled,
                               compiled_exact=compiled==expected,actions=actions,wanted=wanted,reactive_exact=actions==wanted)),flush=True)
                for seed in range(args.shard*9,(args.shard+1)*9):
                    rule=4 if seed%9==8 else seed%9%4
                    phr=(seed*7+1)%12
                    count=(8,24,48)[(seed//3)%3]
                    rng=random.Random(74197+seed)
                    incoming=[rng.randrange(4) for _ in range(count)]
                    changes=sorted(rng.sample(range(count),max(1,count//5)))
                    methods=["reactive","compiled","typed","lazy"]
                    rng.shuffle(methods)
                    for order,method in enumerate(methods):
                        epoch=1+seed*4+order
                        job=dict(epoch=epoch,count=count,incoming=incoming,changes=changes,guard=method!="unguarded")
                        store(folder/"job.json",job)
                        visible=folder/"visible.json"
                        wait(lambda:visible.exists() and load(visible)["epoch"]==epoch)
                        instruction=REQUESTS[rule][phr]
                        start=time.perf_counter()
                        calls=0;rejections=0;chosen=None;fail=None;memo={};declined=False
                        try:
                            if method=="typed":
                                chosen=rule
                            elif method=="compiled":
                                calls+=1;chosen=select_rule(instruction)
                            while True:
                                obs=load(visible)
                                if obs["step"]>=obs["count"]:break
                                if method in ("reactive","lazy"):
                                    flags=obs["flags"]
                                    if method=="reactive" or flags not in memo:
                                        calls+=1;memo[flags]=reactive(instruction,flags)
                                    route=memo[flags]
                                else:
                                    route=0 if chosen==4 else TABLES[chosen][obs["flags"]]
                                if route==0:
                                    declined=True
                                    break
                                token=f"{obs['rev']:04d}{route}"
                                desktop.act(Action("type",text=token),read=reader.value,
                                    until=lambda val:val==(epoch*1000000+obs["rev"])*100000+int(token),timeout_s=2)
                                new=desktop.act(Action("key",keys="Return"),read=lambda:load(visible),
                                    until=lambda v:v["epoch"]==epoch and v["rev"]>obs["rev"],timeout_s=2)
                                if new["step"]==obs["step"]:
                                    rejections+=1
                        except (ValueError,TimeoutError,RuntimeError) as exc:
                            fail=type(exc).__name__
                        with closing(sqlite3.connect(folder/"events.sqlite")) as conn:
                            persisted=conn.execute("SELECT position,revision,flags,route FROM audit WHERE epoch=? ORDER BY rowid",(epoch,)).fetchall()
                        revision=1;wanted=[]
                        if rule!=4:
                            for i,flags in enumerate(incoming):
                                if i in changes:
                                    flags ^= 3;revision+=1
                                wanted.append((i,revision,flags,oracle(rule,flags))); revision+=1
                        state=load(visible)
                        valid=(not fail and persisted==wanted and (state["step"]==count if rule!=4 else declined and state["step"]==0))
                        done=(not fail and state["step"]==count)
                        wrong=sum(row!=exp for row,exp in zip(persisted,wanted))+max(0,len(persisted)-len(wanted))
                        row=dict(seed=seed,method=method,length=count,rule=rule,chosen=chosen,calls=calls,valid=valid,seconds=time.perf_counter()-start,
                                 false_done=bool(done and not valid),wrong_writes=wrong,commits=len(persisted),stale_rejections=rejections,
                                 injected=len(changes) if rule!=4 else 0,stopped=not done,error=fail)
                        print("GENERALIZE",json.dumps(row),flush=True)
            finally:
                reader.close()
        finally:
            proc.terminate();proc.wait(timeout=10)
            desktop.close()


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",type=Path);p.add_argument("--shard",type=int,default=0)
    args=p.parse_args()
    if args.fixture:fixture(args.fixture)
    else:run(args)
