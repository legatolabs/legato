"""The two web pages the form tasks use, on localhost:8765. Each submission is saved to ~/LegatoBench/site/<page>.json.

    python benchmarks/mcp_h2h/site.py
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

STATE = Path.home() / "LegatoBench" / "site"
PORT = 8765

SIGNUP = """<!doctype html><title>Sign up</title><style>body{font:18px system-ui;margin:60px}label{display:block;
margin:14px 0}input,select,button{font:inherit;padding:6px}</style><h1>Sign up</h1><form method=post action=/signup>
<label>Name <input name=name></label><label>Email <input name=email type=email></label><label>Plan <select name=plan>
<option value=free>Free</option><option value=team>Team</option><option value=pro>Pro</option></select></label>
<button>Create account</button></form>"""
DONE = "<!doctype html><title>Done</title><h1 style='font:24px system-ui;margin:60px'>Thanks, you're signed up.</h1>"
TODO = """<!doctype html><title>To do</title><style>body{font:18px system-ui;margin:60px}input,button{font:inherit;
padding:6px}</style><h1>To do</h1><ul id=l></ul><input id=i placeholder="New item"> <button id=b>Add</button><script>
const l=document.getElementById('l'),i=document.getElementById('i');
function draw(items){l.innerHTML='';items.forEach(t=>{const li=document.createElement('li');li.textContent=t;
l.appendChild(li)})}
fetch('/todo/items').then(r=>r.json()).then(draw);
function add(){if(!i.value.trim())return;fetch('/todo/items',{method:'POST',body:i.value}).then(r=>r.json()).then(draw);
i.value='';i.focus()}
document.getElementById('b').onclick=add;i.onkeydown=e=>{if(e.key==='Enter')add()};</script>"""


def load(name: str) -> dict:
    f = STATE / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save(name: str, data: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"{name}.json").write_text(json.dumps(data))


class Handler(BaseHTTPRequestHandler):
    def send(self, body: str, ctype: str = "text/html") -> None:
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/signup":
            self.send(SIGNUP)
        elif self.path == "/todo":
            self.send(TODO)
        elif self.path == "/todo/items":
            self.send(json.dumps(load("todo").get("items", [])), "application/json")
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if self.path == "/signup":
            f = {k: v[0].strip() for k, v in parse_qs(body).items()}
            save("signup", {"name": f.get("name", ""), "email": f.get("email", ""), "plan": f.get("plan", "")})
            self.send(DONE)
        elif self.path == "/todo/items":
            items = load("todo").get("items", []) + [body.strip()]
            save("todo", {"items": items})
            self.send(json.dumps(items), "application/json")
        else:
            self.send_error(404)

    def log_message(self, *a) -> None:
        pass


def serve() -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", PORT), Handler)


if __name__ == "__main__":
    serve().serve_forever()
