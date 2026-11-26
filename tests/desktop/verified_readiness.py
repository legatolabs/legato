"""Measure physical input to a verified receipt in an owned native window on a disposable desktop."""
import io
import json
import os
from pathlib import Path
import platform
import itertools
import statistics
import subprocess
import sys
import tempfile
import time


def publish(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def fixture(folder):
    import tkinter as tk

    root = tk.Tk()
    root.title("Legato order entry")
    root.geometry("460x250+65+70")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=460, height=250, bg="white", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_rectangle(40, 120, 270, 195, fill="#224488", outline="")
    canvas.create_text(150, 155, text="Submit order", fill="white")
    canvas.create_rectangle(285, 120, 425, 195, fill="#225522", outline="")
    canvas.create_text(355, 155, text="Update", fill="white")
    title = canvas.create_text(200, 35, text="Order 0", fill="black")
    bits = [canvas.create_rectangle(30+i*15, 65, 44+i*15, 87, fill="white", outline="") for i in range(12)]
    marks = [canvas.create_rectangle(x, 65, x+10, 87, fill=color, outline="")
             for x, color in [(12, "black"), (220, "black"), (235, "white")]]
    sequence = 0
    clicks = 0

    def draw(serial):
        canvas.itemconfigure(title, text=f"Order {serial}")
        for i, block in enumerate(bits):
            canvas.itemconfigure(block, fill="black" if serial & (1 << i) else "white")
        canvas.update_idletasks()
        publish(folder / "grade.json", {"serial":serial, "clicks":clicks})

    def click(event):
        nonlocal sequence, clicks
        if not (40 <= event.x <= 425 and 120 <= event.y <= 195):
            return
        clicks += 1
        plan = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        if plan["serial"] != sequence + 1 or int(event.x > 275) != plan["target"]:
            publish(folder / "error.json", {"serial":sequence, "clicks":clicks})
            return
        sequence += 1
        if plan["delay_ms"] >= 0:
            root.after(plan["delay_ms"], draw, sequence)
        else:
            publish(folder / "withheld.json", {"serial":sequence, "clicks":clicks})

    canvas.bind("<Button-1>", click)
    def ready():
        root.lift()
        root.focus_force()
        draw(0)
        publish(folder / "geometry.json", [canvas.winfo_rootx(), canvas.winfo_rooty()])
    root.after(500, ready)
    root.mainloop()


def take_json(path, timeout=8):
    end = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < end:
        time.sleep(.02)
    return json.loads(path.read_text(encoding="utf-8"))


def worker(folder, arm):
    import legato
    from PIL import Image
    from legato.actions import Action
    sdk_root = Path(os.environ["BENCH_SDK"]).resolve()
    assert Path(legato.__file__).resolve().is_relative_to(sdk_root), legato.__file__
    x, y = take_json(folder / "geometry.json")
    def receipt(obs):
        with Image.open(io.BytesIO(obs.screenshot)) as im:
            strip = im.crop((x+10, y+70, x+245, y+71)).convert("L")
            def dark(px):
                return strip.getpixel((px-10, 0)) < 56
            if not (dark(16) and dark(224) and not dark(239)):
                return None
            return sum(int(dark(37+i*15)) << i for i in range(12))
    env = legato.local(hide_terminal=False, fixed_wait_s=0 if arm == "checked" else None)
    try:
        end = time.monotonic() + 8
        while receipt(env.observe()) != 0:
            if time.monotonic() >= end:
                raise RuntimeError("Could not read owned window before trial")
            time.sleep(.04)
        print(json.dumps({"screen":env.size}), flush=True)
        for line in sys.stdin:
            spec = json.loads(line)
            serial, delay = spec["serial"], spec["delay_ms"]
            action = Action("click", x=x+(355 if spec["target"] else 150), y=y+155)
            seen = []
            def check(obs):
                value = receipt(obs)
                seen.append(value)
                return value == serial
            cap = 4 if delay >= 0 else .8
            start = time.perf_counter()
            returned = None
            if arm == "sdk":
                try:
                    returned = env.act(action, until=check, timeout_s=cap)
                except TimeoutError:
                    pass
                timing = dict(env.last)
            else:
                obs = env.act(action)
                timing = dict(env.last)
                while True:
                    if check(obs):
                        returned = obs
                        break
                    if time.perf_counter()-start >= cap:
                        break
                    time.sleep(.01)
                    obs = env.observe()
            elapsed = (time.perf_counter()-start)*1000
            # All arms get the same independent final-frame check, outside the timer.
            result = receipt(returned) if returned is not None else None
            print(json.dumps(dict(total_ms=elapsed, accepted=result==serial, first=seen[0],
                                  reads=len(seen), timing=timing)), flush=True)
    finally:
        env.close()


def benchmark(out):
    candidate = Path(__file__).resolve().parents[2]
    baseline = (candidate / "baseline").resolve()
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        child = subprocess.Popen([sys.executable, __file__, "--fixture", temp])
        workers = {}
        try:
            take_json(folder / "geometry.json")
            for arm in ["default", "checked", "sdk"]:
                root = candidate if arm=="sdk" else baseline
                assert (root / "legato" / "env.py").exists(), root
                workers[arm] = subprocess.Popen([sys.executable,"-u",str(Path(__file__).resolve()),"--worker",temp,arm],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=root,
                    env={**os.environ, "BENCH_SDK":str(root), "PYTHONPATH":str(root)},
                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                print(arm, "setup", json.loads(workers[arm].stdout.readline()), flush=True)
            rows, serial = [], 0
            def trial(arm, delay, pair, within):
                nonlocal serial
                serial += 1
                spec = {"serial":serial, "delay_ms":delay, "target":serial % 2}
                publish(folder / "request.json", spec)
                proc = workers[arm]
                proc.stdin.write(json.dumps(spec)+"\n")
                proc.stdin.flush()
                row = json.loads(proc.stdout.readline())
                grade = take_json(folder / "grade.json")
                valid = ((row["accepted"] and grade == {"serial":serial,"clicks":serial}) if delay >= 0
                         else (not row["accepted"] and take_json(folder / "withheld.json") ==
                               {"serial":serial,"clicks":serial} and grade["serial"] != serial))
                row.update(spec, pair=pair, arm=arm, action_in_task=within, valid=valid)
                print("MEASURE", json.dumps(row,separators=(",",":")), flush=True)
                rows.append(row)
                if not valid or (folder / "error.json").exists():
                    raise AssertionError(f"Incorrect receipt, target or input count: {row} {grade}")
            for pair in range(18):
                arms = list(itertools.permutations(["default","checked","sdk"]))[(pair//2 + pair%2)%6]
                for arm in arms:
                    for within, delay in enumerate([0,0,0] if pair%2==0 else [0,70,350]):
                        trial(arm, delay, pair, within)
            for arm in ["sdk","checked","default"]:
                trial(arm,-1,18,0)
            out.write_text(json.dumps({"os":platform.platform(), "python":sys.version, "rows":rows}, indent=2))
        finally:
            for w in workers.values():
                w.stdin.close()
                w.wait(timeout=5)
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    if sys.argv[1] == "--fixture":
        fixture(Path(sys.argv[2]))
    elif sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), sys.argv[3])
    else:
        benchmark(Path(sys.argv[1]))
