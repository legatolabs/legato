"""Compare complete-response execution with early read-only preparation on an isolated native form."""
import argparse
import csv
from contextlib import closing
import io
import itertools
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from native_receipt import TitleReader
from verified_readiness import publish, take_json

HERE = Path(__file__).resolve().parent
SLOTS = ["north", "south", "east", "west"]
COLORS = ["#a1243e", "#15549a", "#28824c", "#81449c"]
POINTS = [55, 160, 265, 370]
PREFIX = re.compile(r'^\s*\{\s*"destination"\s*:\s*"(north|south|east|west)"\s*[,}]')
RULES = ("A shipment has a three-digit identifier and exactly one final destination from north, south, east, west. "
         "Use the destination explicitly named by the caller. For source-to-destination instructions use the destination. "
         "If the caller leaves out a value, gives alternatives without a choice, or asks not to submit, clarify: "
         "destination is clarify and shipment is empty. Copy digit strings exactly, including leading zeros. "
         "Return both fields in one object.")
SCHEMA = {"type":"object", "properties":{"destination":{"type":"string","enum":SLOTS+["clarify"]},
          "shipment":{"type":"string","pattern":"^([0-9]{3})?$"}}, "required":["destination","shipment"],
          "additionalProperties":False}


def fixture(folder):
    import tkinter as tk
    import os
    app = tk.Tk()
    prefix = "Legato " + folder.name + " edit "
    app.title(prefix+"0")
    app.geometry("455x190+65+70")
    app.attributes("-topmost", True)
    canvas = tk.Canvas(app, width=435, height=98, highlightthickness=0)
    canvas.pack()
    field = tk.StringVar()
    entry = tk.Entry(app, textvariable=field, font=("Arial",18))
    entry.pack()
    state = dict(trial=0, revision=0, stage=0, destination="", inputs=0)
    journal = folder/"saved.csv"
    journal.touch()
    def title(stage):
        state["stage"] = stage
        app.title(prefix+str(state["trial"]*100+state["revision"]*10+stage))
    def layout():
        canvas.delete("all")
        for i,slot in enumerate(SLOTS):
            x = POINTS[(i+state["revision"])%4]
            canvas.create_rectangle(x-37,10,x+37,72,fill=COLORS[i],outline="",tags=(slot,))
            canvas.create_text(x,40,text=slot,fill="white",tags=(slot,))
            canvas.tag_bind(slot,"<Button-1>", lambda event, s=slot: select(s))
    def select(slot):
        state["inputs"] += 1
        state["destination"] = slot
        entry.focus_force()
        title(1)
    def change(*_):
        if state["stage"] == 1 and len(field.get()) == 3:
            title(2)
    def save(_):
        with journal.open("a",newline="") as f:
            csv.writer(f).writerow([state["trial"],state["destination"],field.get(),state["inputs"]])
        title(3)
    def poll():
        p = folder/"control.json"
        if p.exists():
            cmd = json.loads(p.read_text())
            trial, rev = cmd["trial"],cmd["revision"]
            if (trial,rev) != (state["trial"],state["revision"]):
                new = trial != state["trial"]
                state.update(trial=trial,revision=rev)
                if new:
                    state.update(inputs=0,destination="",stage=0)
                    field.set("")
                layout()
                title(state["stage"])
        app.after(4,poll)
    field.trace_add("write",change)
    entry.bind("<Return>",save)
    def ready():
        app.lift()
        app.focus_force()
        layout()
        publish(folder/"ready.json", {"pid":os.getpid(),"prefix":prefix,
                "xy":[canvas.winfo_rootx(),canvas.winfo_rooty()]})
        poll()
    app.after(300,ready)
    app.mainloop()


def registered(text):
    m = re.fullmatch(r"ship ([0-9]{3}) to (north|south|east|west)",text)
    if m:
        return {"destination":m[2],"shipment":m[1]}
    m = re.fullmatch(r"route (north|south|east|west) shipment ([0-9]{3})",text)
    if m:
        return {"destination":m[1],"shipment":m[2]}
    return None


def events(case,live):
    if not live:
        start = time.perf_counter()
        for offset,part in case["events"]:
            time.sleep(max(0,start+offset-time.perf_counter()))
            yield part,False
        time.sleep(max(0,start+case["total_s"]-time.perf_counter()))
        yield "",case["finish"]=="stop"
        return
    body = {"model":"legato-stream-study","temperature":0,"seed":147,"max_tokens":128,"stream":True,
            "stream_options":{"include_usage":True},"cache_prompt":False,
            "messages":[{"role":"system","content":RULES},{"role":"user","content":case["text"]}],
            "response_format":{"type":"json_schema","json_schema":{"name":"order","strict":True,"schema":SCHEMA}}}
    req = urllib.request.Request("http://127.0.0.1:18691/v1/chat/completions",data=json.dumps(body).encode(),
                                 headers={"Content-Type":"application/json"})
    finish = None
    with urllib.request.urlopen(req,timeout=30) as resp:
        for line in resp:
            if not line.startswith(b"data: "):
                continue
            raw = line[6:].strip()
            if raw == b"[DONE]":
                yield "",finish=="stop"
                return
            for c in json.loads(raw).get("choices",[]):
                finish = c.get("finish_reason") or finish
                yield c.get("delta",{}).get("content") or "",False


def wait_until(read,expected):
    end=time.monotonic()+3
    while read()!=expected:
        if time.monotonic()>=end:
            raise TimeoutError(expected)
        time.sleep(.004)


def prepare(env, reader, xy, slot):
    from PIL import Image, ImageColor
    version=reader.value()
    with Image.open(io.BytesIO(env.observe().screenshot)) as im:
        rgb=im.convert("RGB")
        target=ImageColor.getrgb(COLORS[SLOTS.index(slot)])
        hits=[(xy[0]+x,xy[1]+20) for x in POINTS if max(abs(a-b) for a,b in zip(rgb.getpixel((xy[0]+x,xy[1]+20)),target)) < 28]
    if len(hits)!=1 or reader.value()!=version:
        raise ValueError("no stable target")
    return version,hits[0]


def transact(env,reader,xy,folder,trial,case,arm,live,cancel,move):
    from legato import Action
    read=reader.value
    epoch=trial*100
    publish(folder/"control.json",dict(trial=trial,revision=0))
    wait_until(read,epoch)
    start=time.perf_counter()
    prepared=None
    model=True
    stale=False
    selected=None
    finished=False
    if arm=="registered":
        selected=registered(case["text"])
        model=selected is None
    def move_targets():
        publish(folder/"control.json",dict(trial=trial,revision=1))
        wait_until(read,epoch+10)
    if model:
        output=""
        boundary=False
        with closing(events(case,live)) as stream:
            for part,stop in stream:
                output+=part
                m=PREFIX.match(output)
                if m and not boundary:
                    boundary=True
                    if arm!="buffered" and case["permit_submit"]:
                        prepared=prepare(env,reader,xy,m[1])
                    if move:
                        move_targets()
                    if cancel:
                        break
                if stop:
                    selected=json.loads(output)
                    finished=True
    else:
        if move:
            move_targets()
        finished=not cancel
    committed=False
    if finished and case["permit_submit"] and isinstance(selected,dict) and set(selected)=={"destination","shipment"}:
        slot,code=selected["destination"],selected["shipment"]
        if slot in SLOTS and isinstance(code,str) and re.fullmatch(r"[0-9]{3}",code):
            if prepared and (m[1]!=slot or read()!=prepared[0]):
                stale=True
                prepared=None
            if prepared is None:
                prepared=prepare(env,reader,xy,slot)
            version,(x,y)=prepared
            if version not in (epoch,epoch+10) or read()!=version:
                raise RuntimeError("target changed before dispatch")
            actions=[Action("click",x=x,y=y),Action("type",text=code),Action("key",keys="Return")]
            for index,action in enumerate(actions,1):
                env.act(action,read=read,until=lambda v: v==version+index,timeout_s=3)
            committed=True
    journal=list(csv.reader((folder/"saved.csv").read_text().splitlines()))
    own=[row for row in journal if int(row[0])==trial]
    took=time.perf_counter()-start
    return dict(s=took,model=model,stale=stale,committed=committed,rows=own,
                stage=read()%10,selected=selected,completed=finished)


def run(live,limit):
    import legato
    cases=json.loads((HERE/"streamed_cases.json").read_text())[:limit]
    with tempfile.TemporaryDirectory() as d:
        folder=Path(d)
        ui=subprocess.Popen([sys.executable,__file__,"--fixture",d])
        try:
            ready=take_json(folder/"ready.json")
            reader=TitleReader(ready["pid"],ready["prefix"])
            env=legato.local(hide_terminal=False)
            try:
                trial=0
                for case in cases:
                    i=case["id"]
                    cancel=i%12 in (3,7)
                    move=i%3==0
                    for arm in list(itertools.permutations(("buffered","prepare","registered")))[i%6]:
                        trial+=1
                        result=transact(env,reader,ready["xy"],folder,trial,case,arm,live,cancel,move)
                        # This independent grader sees the requested outcomes only after the transaction ends.
                        expected=case["expected"]
                        should=expected[0]!="clarify" and not cancel
                        valid=(result["rows"]==([[str(trial),expected[0],expected[1],"1"]] if should else [])
                               and result["stage"]==(3 if should else 0))
                        print("STREAMTASK",json.dumps(dict(**result,id=i,arm=arm,live=live,cancel=cancel,move=move,
                                                permitted=case["permit_submit"],valid=valid)),flush=True)
                        if not valid:
                            raise AssertionError((case["id"],arm,result))
            finally:
                env.close()
                reader.close()
        finally:
            ui.terminate()
            ui.wait(timeout=5)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",type=Path)
    p.add_argument("--live",action="store_true")
    p.add_argument("--limit",type=int,default=36)
    args=p.parse_args()
    if args.fixture:
        fixture(args.fixture)
    else:
        run(args.live,args.limit)
