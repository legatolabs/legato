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
    raise AssertionError("The fixture's green button was missing from the target window")


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
                deadline = time.monotonic() + 12
                while True:
                    obs = env.observe()
                    outline = env.read()
                    if loaded.is_set() and "Receipt 1000" in outline:
                        try:
                            button_point(obs.screenshot)
                            break
                        except AssertionError:
                            pass  # First paint may follow accessibility readiness.
                    if time.monotonic() >= deadline:
                        raise AssertionError(f"Safari fixture was not ready. Page requested: {loaded.is_set()}")
                    time.sleep(0.2)
                for receipt in range(1001, 1005):
                    x, y = button_point(obs.screenshot)
                    obs = env.act(legato.Action("click", x=x, y=y))
                    assert receipts.get(timeout=5) == f"/receipt/{receipt}"
                    assert f"Receipt {receipt}" in env.read()
                print("Four Safari clicks verified by window captures, accessibility reads, and page receipts")
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
