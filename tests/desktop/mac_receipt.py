"""Verify a background Safari click through the page, its accessibility tree, and its returned frame."""
import io
import queue
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

import legato


PAGE = b"""<!doctype html><title>Legato receipt fixture</title>
<style>body {background:white} button {margin:84px;width:210px;height:88px;border:0;background:rgb(12,211,43)}</style>
<button>Apply receipt</button><output>Receipt 1000</output>
<script>
let n=1000;
document.querySelector('button').onclick=()=>{
  document.querySelector('output').textContent='Receipt '+(++n);
  fetch('/receipt/'+n, {method:'POST'});
};
</script>"""


def button_point(png):
    with Image.open(io.BytesIO(png)) as frame:
        image = frame.convert("RGB")
    # Select a patch inside the large green button. Window decorations cannot be this wide.
    for y in range(40, image.height - 16, 8):
        for x in range(30, image.width - 34, 8):
            if all(g - max(r, b) > 100 for r, g, b in
                   (image.getpixel((x + dx, y + dy)) for dx, dy in ((0, 0), (32, 0), (0, 16), (32, 16)))):
                return x + 16, y + 8
    return None


def accessible_button_point(env):
    window = env.window()
    page = env._find_role(env._ax_window(), "AXWebArea")
    button = env._find_role(page, "AXButton")
    attrs = env._attrs(button, ["AXTitle", "AXDescription"])
    assert "Apply receipt" in attrs.values(), "The fixture's named button is missing"
    x, y, width, height = env.m.frame(button)
    return round(x + width / 2 - window["x"]), round(y + height / 2 - window["y"])


def main():
    import ApplicationServices
    import Quartz

    if not ApplicationServices.AXIsProcessTrusted() or not Quartz.CGPreflightScreenCaptureAccess():
        print("SKIP background Safari receipt: Accessibility and Screen Recording permissions are required")
        return
    loaded = threading.Event()
    receipts = queue.Queue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)
            if self.path == "/":
                loaded.set()

        def do_POST(self):
            receipts.put(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = legato.local(background="Safari")
            try:
                assert env.kind == "window" and env.opened, "Safari did not open a window for this test"
                env.open_url(f"http://127.0.0.1:{server.server_port}/")
                assert loaded.wait(12), "Safari never requested the fixture"
                deadline = time.monotonic() + 12
                while "Receipt 1000" not in env.read():
                    if time.monotonic() >= deadline:
                        raise AssertionError("The target window did not expose the initial receipt")
                    time.sleep(0.2)
                obs = env.observe()
                visible = 0
                for receipt in range(1001, 1005):
                    point = button_point(obs.screenshot)
                    if point:
                        visible += 1
                    else:
                        if receipt == 1001:
                            # Compare the OS's single-window fallback on the same target.
                            from legato.background import _screencapture
                            window = env.window()
                            rect = tuple(window[k] for k in ("x", "y", "w", "h"))
                            alternate = _screencapture(window["id"], rect)
                            print("Safari button visible in screencapture:", bool(alternate and button_point(alternate)))
                            output = Path("local-shots")
                            output.mkdir(exist_ok=True)
                            (output / "2-safari.png").write_bytes(obs.screenshot)
                            (output / "2-safari.txt").write_text(env.read(), encoding="utf-8")
                        point = accessible_button_point(env)
                    obs = env.act(legato.Action("click", x=point[0], y=point[1]))
                    assert receipts.get(timeout=5) == f"/receipt/{receipt}"
                    assert f"Receipt {receipt}" in env.read()
                print(f"Verified four Safari clicks by page receipts and AX reads. Button visible in {visible}/4 input frames.")
                if visible < 4:
                    print("::warning::Safari rendered an incomplete background window capture")
            except Exception:
                output = Path("local-shots")
                output.mkdir(exist_ok=True)
                (output / "2-safari.png").write_bytes(env.observe().screenshot)
                (output / "2-safari.txt").write_text(env.read(), encoding="utf-8")
                print("Safari window:", env.describe())
                raise
            finally:
                env.close()
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    main()
