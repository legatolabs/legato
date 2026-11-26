"""Held-out task. A native form edits two fields and saves an on-disk receipt. Only the grader reads the file."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from verified_readiness import publish, take_json


def fixture(folder):
    import tkinter as tk
    root=tk.Tk()
    root.title("Legato shipment editor")
    root.geometry("520x300+45+80")
    root.attributes("-topmost", True)
    canvas=tk.Canvas(root,width=510,height=110,highlightthickness=0,bg="white")
    canvas.pack()
    entry=tk.Entry(root,font=("Arial",18))
    entry.pack(padx=30,fill="x")
    button=tk.Button(root,text="Save shipment",takefocus=False)
    button.pack(pady=14)
    text=canvas.create_text(240,25,text="Shipment",fill="black")
    rects=[canvas.create_rectangle(20+i*9,59,27+i*9,78,fill="white",outline="") for i in range(20)]
    canvas.create_rectangle(4,59,12,78,fill="black",outline="")
    canvas.create_rectangle(210,59,218,78,fill="black",outline="")
    session={"serial":0,"stage":0,"saved":0,"entry_changes":0,"busy":False}
    field=tk.StringVar()
    entry.configure(textvariable=field)

    def draw(stage):
        session["stage"]=stage
        val=session["serial"]*16+stage
        canvas.itemconfigure(text,text=f"Shipment {session['serial']} · stage {stage}")
        for i,rect in enumerate(rects):
            canvas.itemconfigure(rect,fill="black" if val & 1<<i else "white")
    def prepare():
        if (folder/"arm.json").exists():
            req=json.loads((folder/"arm.json").read_text())
            if req["serial"]!=session["serial"] and not session["busy"]:
                session.update(serial=req["serial"],expected=req["value"],delay=req["delay_ms"],entry_changes=0)
                field.set("")
                button.focus_force()
                draw(0)
                publish(folder/"armed.json",{"serial":session["serial"]})
        root.after(10,prepare)
    def focus(_):
        if session["stage"]==0: draw(1)
    def typed(*_):
        if field.get() and session["stage"] in (1,2):
            session["entry_changes"]+=1
            if field.get()==session.get("expected"):
                draw(2)
    def save():
        if session["busy"]: return
        value=field.get()
        sid=session["serial"]
        session["saved"]+=1
        session["busy"]=True
        def finish():
            with (folder/"shipments.csv").open("a",newline="",encoding="utf-8") as stream:
                csv.writer(stream).writerow([sid,value,session["saved"]])
            draw(3)
            session["busy"]=False
        root.after(session["delay"],finish)
    field.trace_add("write",typed)
    entry.bind("<FocusIn>",focus)
    entry.bind("<Return>",lambda _:save())
    button.configure(command=save)
    def ready():
        root.lift()
        root.focus_force()
        publish(folder/"geometry.json",[canvas.winfo_rootx(),canvas.winfo_rooty(),
                  entry.winfo_rootx()+25,entry.winfo_rooty()+10,button.winfo_rootx()+20,button.winfo_rooty()+10])
        prepare()
    root.after(500,ready)
    root.mainloop()


def worker(folder,arm):
    from PIL import Image
    import legato
    from legato import Action
    assert Path(legato.__file__).resolve().is_relative_to(Path(os.environ["BENCH_SDK"]).resolve())
    x,y,ex,ey,bx,by=take_json(folder/"geometry.json")
    def read(im):
        a=im.crop((x,y+65,x+225,y+66)).convert("L")
        if a.getpixel((8,0))>=55 or a.getpixel((214,0))>=55: return -1
        return sum(int(a.getpixel((23+i*9,0))<55)<<i for i in range(20))
    def decode(obs):
        with Image.open(io.BytesIO(obs.screenshot)) as im: return read(im)
    env=legato.local(hide_terminal=False,fixed_wait_s=0 if arm=="manual" else None)
    try:
        print(json.dumps({"screen":env.size}),flush=True)
        for line in sys.stdin:
            spec=json.loads(line);serial=spec["serial"];stage_values=[];read_counts=[]
            click=Action("click",x=ex,y=ey)
            save=Action("key",keys="Return") if spec["by_key"] else Action("click",x=bx,y=by)
            end=time.monotonic()+5
            while decode(env.observe())!=serial*16:
                if time.monotonic()>end: raise RuntimeError("reset not visible")
                time.sleep(.01)
            start=time.perf_counter()
            for stage,action in enumerate((click,Action("type",text=spec["value"]),save),1):
                expect=serial*16+stage; reads=0
                def matches(im):
                    nonlocal reads
                    reads+=1
                    return read(im)==expect
                if arm=="sdk":
                    obs=env.act(action,until=matches,timeout_s=3)
                else:
                    obs=env.act(action); deadline=time.monotonic()+3
                    while True:
                        reads+=1
                        if decode(obs)==expect:break
                        if time.monotonic()>=deadline:raise TimeoutError((serial,stage))
                        time.sleep(.01);obs=env.observe()
                stage_values.append(decode(obs))
                read_counts.append(reads)
            print(json.dumps({"ms":1000*(time.perf_counter()-start),"frames":stage_values,"reads":read_counts}),flush=True)
    finally:
        env.close()


def run():
    from itertools import permutations
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as t:
        folder=Path(t);workers={}
        gui=subprocess.Popen([sys.executable,__file__,"fixture",t])
        try:
            take_json(folder/"geometry.json")
            for arm in ("main","manual","sdk"):
                repo=root if arm=="sdk" else root/"baseline"
                proc=subprocess.Popen([sys.executable,"-u",str(Path(__file__).resolve()),"worker",t,arm],
                    cwd=repo,env={**os.environ,"PYTHONPATH":str(repo),"BENCH_SDK":str(repo)},text=True,
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                workers[arm]=proc;json.loads(proc.stdout.readline())
            serial=0
            for pair in range(12):
                for arm in list(permutations(workers))[(pair//2+pair%2)%6]:
                    serial+=1
                    value=str(81+(pair*47)%900)
                    spec={"serial":serial,"value":value,"by_key":pair%2==0,"delay_ms":[0,55,180,550][pair%4]}
                    publish(folder/"arm.json",spec); deadline=time.monotonic()+5
                    while take_json(folder/"armed.json")["serial"]!=serial:
                        if time.monotonic()>deadline: raise TimeoutError("arm")
                        time.sleep(.01)
                    w=workers[arm];w.stdin.write(json.dumps(spec)+"\n");w.stdin.flush()
                    row=json.loads(w.stdout.readline())
                    stored=list(csv.reader((folder/"shipments.csv").read_text(encoding="utf-8").splitlines()))
                    valid=stored[-1]==[str(serial),value,str(serial)] and len(stored)==serial and row["frames"]==[serial*16+i for i in (1,2,3)]
                    print("FILETASK",json.dumps({**spec,**row,"pair":pair,"arm":arm,"valid":valid}),flush=True)
                    if not valid:raise AssertionError((row,stored[-1]))
        finally:
            for w in workers.values():
                w.stdin.close();w.wait(timeout=5)
            gui.terminate();gui.wait(timeout=10)


if __name__=="__main__":
    if len(sys.argv)==1:run()
    elif sys.argv[1]=="fixture":fixture(Path(sys.argv[2]))
    else:worker(Path(sys.argv[2]),sys.argv[3])
