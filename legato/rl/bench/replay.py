"""Side-by-side replay of two rollouts of the same task, built from what each agent actually saw.

Every frame is a screenshot the policy received, shown from the moment it was taken (step time minus model time,
relative to the rollout's start); between screenshots the panel shows what the loop was doing (resetting, waiting
for the desktop, the model thinking, acting and waiting). Clocks are real elapsed time; playback is sped up by
`speed` and says so on screen. Nothing is re-rendered or staged.
"""
from __future__ import annotations

import json
from pathlib import Path

from legato.rl.trajectory import IMG_PREFIX, read_rollouts

W, H, BAR, FOOT = 960, 540, 64, 56


def _font(size: int):
    from PIL import ImageFont
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
              "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def pick(run_dir: str | Path, task_id: str) -> dict | None:
    """The best rollout of a task in a run: success first, then highest reward, then fastest."""
    rs = [r for r in read_rollouts(run_dir) if r["task_id"] == task_id and r.get("status") in ("ok", "truncated")]
    rs.sort(key=lambda r: (-(r.get("reward") or 0), r["timing"]["total_s"]))
    return rs[0] if rs else None


def timeline(rec: dict, run_dir: Path) -> dict:
    """Frames (time, image path) and phases (start, end, label) on the rollout's own clock."""
    t0, tm = rec["t_start"], rec["timing"]
    frames, phases = [], []
    reset_end = tm.get("reset_s", 0.0)
    ready_end = reset_end + tm.get("wait_after_reset_s", 0.0)
    phases.append((0.0, reset_end, "resetting the desktop"))
    phases.append((reset_end, ready_end, "waiting for the desktop"))
    for st in rec.get("steps", []):
        if not st.get("request"):
            continue
        shot = None
        for h in reversed(st["request"]["messages"]):
            m = json.loads((run_dir / "messages" / f"{h}.json").read_text())
            parts = m.get("content") if isinstance(m.get("content"), list) else []
            urls = [p["image_url"]["url"] for p in parts if p.get("type") == "image_url"]
            if urls and urls[-1].startswith(IMG_PREFIX):
                shot = run_dir / "images" / f"{urls[-1][len(IMG_PREFIX):]}.png"
                break
        t_obs = st["t"] - st["predict_s"] - t0
        if shot is not None:
            frames.append((max(0.0, t_obs), shot))
        phases.append((t_obs, t_obs + st["predict_s"], f"step {st['i'] + 1}: model thinking"))
        act = sum(a.get("step_s") or 0.0 for a in st.get("actions", []))
        waits = [a.get("wait_s") or 0.0 for a in st.get("actions", [])]
        phases.append((t_obs + st["predict_s"], t_obs + st["predict_s"] + act,
                       f"step {st['i'] + 1}: acting, waiting {sum(waits):.1f} s"))
    end = tm["total_s"]
    phases.append((end - tm.get("eval_s", 0.0) - tm.get("wait_before_eval_s", 0.0), end, "grading"))
    return {"frames": frames, "phases": phases, "end": end, "reward": rec.get("reward") or 0.0,
            "steps": rec.get("n_steps", 0)}


def _panel(tl: dict, tau: float, label: str, sub: str, accent):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H + BAR + FOOT), (17, 22, 29))
    shot = None
    for t, p in tl["frames"]:
        if t <= tau:
            shot = p
    d = ImageDraw.Draw(img)
    if shot is not None:
        im = Image.open(shot).convert("RGB").resize((W, H))
        img.paste(im, (0, BAR))
    else:
        d.text((W // 2, BAR + H // 2), "desktop starting…", fill=(160, 170, 180), font=_font(28), anchor="mm")
    done = tau >= tl["end"]
    clock = min(tau, tl["end"])
    d.rectangle([0, 0, W, BAR], fill=(28, 34, 43))
    d.text((16, 10), label, fill=accent, font=_font(26))
    d.text((16, 40), sub, fill=(170, 178, 188), font=_font(16))
    d.text((W - 16, 32), f"{clock:6.1f} s", fill=(235, 238, 242), font=_font(30), anchor="rm")
    phase = next((p for a, b, p in tl["phases"] if a <= tau < b), "")
    if done:
        ok = tl["reward"] >= 1.0
        verdict = "task passed" if ok else f"grader score {tl['reward']:.2f}"
        phase = f"done in {tl['end']:.0f} s · {verdict}"
    d.rectangle([0, BAR + H, W, BAR + H + FOOT], fill=(28, 34, 43))
    d.text((16, BAR + H + FOOT // 2), phase, fill=(235, 238, 242) if not done else accent, font=_font(22), anchor="lm")
    if done:
        d.rectangle([0, BAR, W, BAR + H], outline=accent, width=6)
    return img


def render(left: dict, right: dict, out: str | Path, titles: tuple, subtitles: tuple, task_text: str,
           speed: float = 4.0, fps: int = 10, hold_s: float = 3.0) -> dict:
    """Write an MP4 with `left` and `right` timelines side by side. Returns basic facts about the video."""
    import imageio.v2 as imageio
    from PIL import Image, ImageDraw
    total = max(left["end"], right["end"])
    n = int((total / speed + hold_s) * fps)
    writer = imageio.get_writer(str(out), fps=fps, codec="libx264", quality=7, macro_block_size=8)
    head = 72
    try:
        for k in range(n):
            tau = min(total, k / fps * speed)
            frame = Image.new("RGB", (2 * W + 16, H + BAR + FOOT + head), (10, 13, 18))
            d = ImageDraw.Draw(frame)
            d.text((16, 12), task_text[:150], fill=(220, 224, 230), font=_font(20))
            d.text((16, 42), f"same model, same task, same desktop image · real elapsed time, played at {speed:g}× speed",
                   fill=(140, 150, 160), font=_font(16))
            frame.paste(_panel(left, tau, titles[0], subtitles[0], (231, 140, 92)), (0, head))
            frame.paste(_panel(right, tau, titles[1], subtitles[1], (120, 200, 140)), (W + 16, head))
            writer.append_data(__import__("numpy").asarray(frame))
    finally:
        writer.close()
    return {"video": str(out), "seconds": round(n / fps, 1), "left_s": round(left["end"], 1),
            "right_s": round(right["end"], 1), "speed": speed}
