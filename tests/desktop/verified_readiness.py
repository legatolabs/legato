"""Measure physical input to a verified receipt in an owned native window on a disposable desktop."""
import io
import json
import os
from pathlib import Path
import platform
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
        if not (40 <= event.x <= 270 and 120 <= event.y <= 195):
            return
        clicks += 1
        plan = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        if plan["serial"] != sequence + 1:
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


def benchmark(out):
    import legato
    from PIL import Image
    from legato.actions import Action

    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        child = subprocess.Popen([sys.executable, __file__, "--fixture", temp])
        regular = fast = None
        try:
            x, y = take_json(folder / "geometry.json")
            def receipt(obs):
                with Image.open(io.BytesIO(obs.screenshot)) as im:
                    strip = im.crop((x+10, y+70, x+245, y+71)).convert("L")
                    def is_black(px):
                        return strip.getpixel((px-10, 0)) < 56
                    if not (is_black(16) and is_black(224) and not is_black(239)):
                        return None
                    return sum(int(is_black(37+i*15)) << i for i in range(12))

            regular = legato.local(hide_terminal=False)
            fast = legato.local(hide_terminal=False, fixed_wait_s=0)
            action = Action("click", x=x+150, y=y+155)
            deadline = time.monotonic() + 5
            while receipt(regular.observe()) != 0:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Owned fixture markers absent on {platform.platform()} at {(x,y)}")
                time.sleep(.05)
            rows = []
            serial = 0

            def trial(arm, delay, pair, within):
                nonlocal serial
                serial += 1
                publish(folder / "request.json", {"serial":serial, "delay_ms":delay})
                env = regular if arm == "default" else fast
                start = time.perf_counter()
                obs = env.act(action)
                first = receipt(obs)
                value, reads = first, 1
                limit = start + (4 if delay >= 0 else 0.8)
                while value != serial and time.perf_counter() < limit:
                    time.sleep(.01)
                    obs = env.observe()
                    value, reads = receipt(obs), reads+1
                elapsed = (time.perf_counter()-start)*1000
                accepted = value == serial
                grade = take_json(folder / "grade.json")
                valid = ((accepted and grade == {"serial":serial,"clicks":serial}) if delay >= 0
                         else (not accepted and take_json(folder / "withheld.json") ==
                               {"serial":serial,"clicks":serial} and grade["serial"] != serial))
                row = dict(pair=pair, arm=arm, action_in_task=within, delay_ms=delay,
                           total_ms=elapsed, accepted=accepted, valid=valid, first=first,
                           serial=serial, reads=reads)
                rows.append(row)
                if not valid or (folder / "error.json").exists():
                    raise RuntimeError(f"Wrong or duplicated action: {row}, application receipt {grade}")
            for pair in range(10):
                arms = ["default","checked"] if pair % 2 == 0 else ["checked","default"]
                for arm in arms:
                    for within, delay in enumerate([0, 0, 0] if pair % 2 == 0 else [0, 70, 350]):
                        trial(arm, delay, pair, within)
            for arm in ["checked","default"]:
                trial(arm, -1, 10, 0)
            out.write_text(json.dumps({"os":platform.platform(), "python":sys.version,
                                      "screen":regular.size, "rows":rows}, indent=2))
            for key in ("default","checked"):
                values = [r["total_ms"] for r in rows if r["arm"]==key and r["delay_ms"]==0]
                print(key, len(values), "immediate median ms", statistics.median(values), flush=True)
        finally:
            if fast is not None:
                fast.close()
            if regular is not None:
                regular.close()
            child.terminate()
            child.wait(timeout=10)


if __name__ == "__main__":
    if sys.argv[1] == "--fixture":
        fixture(Path(sys.argv[2]))
    else:
        benchmark(Path(sys.argv[1]))
