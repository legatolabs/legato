"""Your model and your desktop, chosen once with `legato setup` and used by every way of running Legato:
`legato run`, `legato mcp`, and `legato.run()` or `legato.Harness()` in Python. The Mac app starts from it too,
until a model is chosen in the app's own Settings.

    ~/.config/legato/config.json        ($LEGATO_CONFIG, or $XDG_CONFIG_HOME/legato/config.json)
    {"provider": "openrouter", "model": "qwen/qwen3.8-27b", "base_url": "https://openrouter.ai/api/v1",
     "api_key_env": "OPENROUTER_API_KEY", "desktop": "background", "app": ""}

A key pasted into `legato setup` is kept in the file as "api_key", readable only by you; otherwise it is read from
the environment variable named by "api_key_env". These environment variables win over the file: LEGATO_MODEL,
LEGATO_BASE_URL, LEGATO_API_KEY, LEGATO_DESKTOP, LEGATO_APP.

Desktops:
    background   macOS: one app's window, worked without your mouse or keyboard, so you keep using the Mac.
                 The app is named in the task ("... in Notes") or with `app`; otherwise your default browser.
    screen       this computer's whole screen (macOS, Windows, Linux): it moves your real mouse.
    docker       a fresh Linux desktop container on this machine: it can't touch your screen or files.
    e2b          a fresh cloud desktop on E2B (E2B_API_KEY).
    your own     path/to/file.py:function or module:function: a function (width, height) that starts your sandbox and
                 returns legato.attach(...), or (handle, env). Each task gets a fresh one (docs/sandboxes.md).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import NamedTuple


class Provider(NamedTuple):
    name: str
    base_url: str
    key_env: str            # the usual environment variable for its key ("" for none)
    model: str              # the model a new setup starts with ("" to ask)
    keys_url: str           # where to get a key ("" for none)


# The same providers, in the same order, as the Mac app's Settings (app/macos/Sources/Legato/Settings.swift).
PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider("OpenRouter: one key for Qwen, Claude, GPT, Gemini and more", "https://openrouter.ai/api/v1",
                           "OPENROUTER_API_KEY", "qwen/qwen3.8-27b", "https://openrouter.ai/keys"),
    "anthropic": Provider("Claude (Anthropic)", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
                          "claude-sonnet-5-5", "https://console.anthropic.com/settings/keys"),
    "openai": Provider("OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY", "",
                       "https://platform.openai.com/api-keys"),
    "gemini": Provider("Gemini (Google)", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
                       "", "https://aistudio.google.com/apikey"),
    "xai": Provider("Grok (xAI)", "https://api.x.ai/v1", "XAI_API_KEY", "", "https://console.x.ai"),
    "together": Provider("Together AI", "https://api.together.xyz/v1", "TOGETHER_API_KEY", "",
                         "https://api.together.ai/settings/api-keys"),
    "fireworks": Provider("Fireworks AI", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", "",
                          "https://fireworks.ai/account/api-keys"),
    "groq": Provider("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "", "https://console.groq.com/keys"),
    "cerebras": Provider("Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "", "https://cloud.cerebras.ai"),
    "local": Provider("A model on this machine (vLLM, Ollama, LM Studio): no key, nothing leaves the machine",
                      "http://localhost:8000/v1", "", "", ""),
    "custom": Provider("Any other OpenAI-compatible server", "", "", "", ""),
}
LOCAL_SERVERS = ("http://localhost:8000/v1", "http://localhost:11434/v1", "http://localhost:1234/v1")  # vLLM, Ollama, LM Studio

DESKTOPS: dict[str, str] = {
    "background": "one app's window on this Mac, worked without your mouse, so you keep using the Mac",
    "screen": "this whole screen: it moves your real mouse, so leave the computer alone while it runs",
    "docker": "a throwaway Linux desktop in Docker: it can't touch your screen or files",
    "e2b": "a fresh cloud desktop on E2B (needs E2B_API_KEY)",
}
ALIASES = {"local": "screen", "bg": "background"}
OWN = "your own sandbox or VM: a Python function that starts it and returns a desktop"


def desktop_spec(value: str) -> str:
    """A desktop as setup saves it: a name from DESKTOPS (or an alias), docker:IMAGE, module:function, or
    path/to/file.py:function with the path made absolute, so it works from any directory. ValueError otherwise."""
    value = ALIASES.get(value.strip(), value.strip())
    if value in DESKTOPS or (value.startswith("docker:") and len(value) > len("docker:")):
        return value
    where, sep, name = value.rpartition(":")
    if not sep or not where or not name.isidentifier():
        raise ValueError(f"{value!r}: use {', '.join(DESKTOPS)}, docker:IMAGE, or your own sandbox as "
                         "path/to/file.py:function or module:function")
    if where.endswith(".py"):
        path = Path(where).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"{value!r}: there is no file {path}")
        return f"{path}:{name}"
    return value


def is_own(desktop: str) -> bool:
    """True for a sandbox of your own (a function), not one of the named desktops or docker:IMAGE."""
    return bool(desktop) and desktop not in DESKTOPS and desktop not in ALIASES and not desktop.startswith("docker:")


@dataclass
class Config:
    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    api_key: str = ""
    desktop: str = ""
    app: str = ""
    extra: dict = field(default_factory=dict)       # keys this version does not know, kept as they were

    @property
    def key(self) -> str:
        """The model key: LEGATO_API_KEY, else the one saved by setup, else the variable named by api_key_env."""
        return (os.environ.get("LEGATO_API_KEY") or self.api_key
                or (os.environ.get(self.api_key_env, "") if self.api_key_env else ""))

    @property
    def ready(self) -> bool:
        """A model is chosen and, unless it runs on this machine, has a key."""
        return bool(self.model and self.base_url and (self.key or is_local_url(self.base_url)))

    def missing(self) -> str | None:
        """What is missing before a model call can work, as one line with the fix, or None."""
        if not self.model or not self.base_url:
            return "no model is set up yet. Run: legato setup"
        if not self.key and not is_local_url(self.base_url):
            env = self.api_key_env or "LEGATO_API_KEY"
            return f"no key for {self.base_url}: export {env}=..., or run legato setup"
        return None


def path() -> Path:
    if os.environ.get("LEGATO_CONFIG"):
        return Path(os.environ["LEGATO_CONFIG"]).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "legato" / "config.json"


def is_local_url(url: str) -> bool:
    return any(h in (url or "") for h in ("://localhost", "://127.0.0.1", "://0.0.0.0", "://[::1]"))


def load(use_env: bool = True) -> Config:
    """The saved choice, with the LEGATO_* environment variables on top. With no file and no variables, a model key
    already in the environment (OPENROUTER_API_KEY, ANTHROPIC_API_KEY) picks that provider's defaults, so
    `legato run` works before `legato setup` has been run. use_env=False: the file alone."""
    data: dict = {}
    p = path()
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
    known = {f for f in Config.__dataclass_fields__ if f != "extra"}
    cfg = Config(**{k: str(v) for k, v in data.items() if k in known and v is not None},
                 extra={k: v for k, v in data.items() if k not in known})
    if use_env:
        for name, attr in (("LEGATO_MODEL", "model"), ("LEGATO_BASE_URL", "base_url"), ("LEGATO_DESKTOP", "desktop"),
                           ("LEGATO_APP", "app")):
            if os.environ.get(name):
                setattr(cfg, attr, os.environ[name])
    if use_env and not cfg.model and not cfg.base_url:
        for pid in ("openrouter", "anthropic"):            # a key in the environment, and a model we know works
            pv = PROVIDERS[pid]
            if os.environ.get(pv.key_env):
                cfg.provider, cfg.model, cfg.base_url, cfg.api_key_env = pid, pv.model, pv.base_url, pv.key_env
                break
    if cfg.provider in PROVIDERS and not cfg.base_url:
        cfg.base_url = PROVIDERS[cfg.provider].base_url
    if cfg.provider in PROVIDERS and not cfg.api_key_env:
        cfg.api_key_env = PROVIDERS[cfg.provider].key_env
    cfg.desktop = ALIASES.get(cfg.desktop, cfg.desktop)
    return cfg


def save(cfg: Config) -> Path:
    """Write the choice (the file is readable by you only, as it may hold a key)."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in asdict(cfg).items() if k != "extra" and v}
    data.update(cfg.extra)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(p)
    return p


def default_desktop() -> str:
    """Where a task runs when nothing says otherwise: in the background on a Mac, else the screen."""
    return "background" if sys.platform == "darwin" else "screen"


# ------------------------------------------------------------ which app a task means (background mode)
# Apps whose names are everyday words more often than they mean the app ("go home", "a preview of").
_NOT_APP_WORDS = {"home", "preview", "tips", "clock", "shortcuts", "books", "automator", "console", "passwords",
                  "image playground", "games", "journal", "phone", "freeform", "stickies", "chess", "news", "photo booth"}
_APP_DIRS = ("/Applications", "/System/Applications", "/System/Applications/Utilities", "/Applications/Utilities",
             str(Path.home() / "Applications"))
_SAYS = {"chrome": "Google Chrome", "word": "Microsoft Word", "excel": "Microsoft Excel",
         "powerpoint": "Microsoft PowerPoint", "outlook": "Microsoft Outlook", "teams": "Microsoft Teams",
         "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "imessage": "Messages",
         "calendar": "Calendar", "finder": "Finder"}


def installed_apps(dirs: tuple[str, ...] = _APP_DIRS) -> list[str]:
    """The names of the .app bundles in the usual folders (macOS), without ".app"."""
    names: list[str] = []
    for d in dirs:
        try:
            names += [n[:-4] for n in os.listdir(d) if n.endswith(".app")]
        except OSError:
            continue
    return sorted(set(names))


def app_for_task(task: str, apps: list[str] | None = None) -> str | None:
    """The app a task names ("... in Notes", "play X on Spotify", "open Excel"), among the installed ones; the longest
    name wins. None when it names none, or only names that are everyday words (Home, Preview, Clock...)."""
    apps = installed_apps() if apps is None else apps
    have = {a.lower(): a for a in apps}
    found: list[tuple[int, str]] = []
    for low, real in have.items():
        if low in _NOT_APP_WORDS or len(low) < 3:
            continue
        if re.search(r"(?<![\w.])(?<!google )" + re.escape(low) + r"(?![\w])", task, re.I):
            found.append((len(low), real))
    for said, real in _SAYS.items():
        if real.lower() in have or real == "Finder":
            if re.search(r"(?<![\w.])(?<!google )" + re.escape(said) + r"(?![\w])", task, re.I):
                found.append((len(said), real))
    return max(found)[1] if found else None


def default_browser() -> str | None:
    """The app macOS opens web links with, by name ("Safari", "Google Chrome"), or None."""
    if sys.platform != "darwin":
        return None
    import subprocess
    js = ("ObjC.import('AppKit'); var u = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL("
          "$.NSURL.URLWithString('https://example.com')); u ? u.path.js : ''")
    try:
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out).stem or None


BROWSERS_TESTED = ("Safari", "Google Chrome")    # background mode is tested on these (tests/test_background_live.py)


def pick_app(task: str, cfg: Config | None = None) -> str:
    """The app background mode works in for this task: the saved one, else the one the task names, else the default
    browser if background mode is tested on it, else Safari. (Firefox works in the background but slowly: 83 s
    against Safari's 11 s on the first task, 24 Nov.)"""
    cfg = cfg or load()
    browser = default_browser()
    return cfg.app or app_for_task(task) or (browser if browser in BROWSERS_TESTED else "Safari")


# ------------------------------------------------------------ checking a model
def check_model(model: str, base_url: str, api_key: str = "", timeout_s: float = 30.0) -> dict:
    """One small vision call: does the endpoint answer, does the model take images, and how long a call takes.
    {"ok": True, "reply", "seconds", "sees_images": True | False | None} or {"ok": False, "error"}."""
    import base64
    import io

    from PIL import Image

    from legato.rl.policy import PolicyClient, PolicyConfig, PolicyError
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 30, 30)).save(buf, format="PNG")
    try:
        client = PolicyClient(PolicyConfig(base_url=base_url, model=model, api_key=api_key or "", timeout_s=timeout_s,
                                           retries=0, logprobs=False, token_ids=False))
        img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        rec = client.chat([{"role": "user", "content": [
            {"type": "text", "text": "What colour is this image? Answer with one word."},
            {"type": "image_url", "image_url": {"url": img}}]}], max_tokens=400, temperature=0.0)
        reply = (rec.get("content") or "").strip()
        # None: it answered nothing readable (a thinking model may spend the call thinking), so it can't be told
        return {"ok": True, "reply": reply[:80], "seconds": round(rec.get("s", 0.0), 2),
                "sees_images": ("red" in reply.lower()) if reply else None}
    except (PolicyError, KeyError, ValueError) as e:
        return {"ok": False, "error": str(e)[:300]}


def list_models(base_url: str, api_key: str = "", timeout_s: float = 10.0) -> list[str]:
    """The model ids an OpenAI-compatible server lists at /models ([] when it lists none or can't be reached)."""
    import httpx
    try:
        r = httpx.get(base_url.rstrip("/") + "/models", timeout=timeout_s,
                      headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    items = data.get("data", data) if isinstance(data, dict) else data
    return [m["id"] for m in items if isinstance(m, dict) and isinstance(m.get("id"), str)] if isinstance(items, list) else []


def find_local_server(urls: tuple[str, ...] = LOCAL_SERVERS) -> tuple[str, list[str]] | None:
    """The first model server answering on this machine (vLLM :8000, Ollama :11434, LM Studio :1234) and its models."""
    for u in urls:
        models = list_models(u, timeout_s=1.5)
        if models:
            return u, models
    return None
