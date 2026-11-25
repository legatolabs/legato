"""Your model and your desktop, chosen once with `legato setup` and used by every way of running Legato:
`legato run`, `legato mcp`, and `legato.run()` or `legato.Harness()` in Python. The Mac app starts from it too,
until a model is chosen in the app's own Settings.

    ~/.config/legato/config.json        ($LEGATO_CONFIG, or $XDG_CONFIG_HOME/legato/config.json)
    {"provider": "openrouter", "model": "qwen/qwen3.8-27b", "base_url": "https://openrouter.ai/api/v1",
     "api_key_env": "OPENROUTER_API_KEY", "desktop": "background", "app": ""}

`legato setup` tries the model once and saves what it found when the model needs something other than the usual:
"tool_calls": "json" (it takes no tool calls, so its actions are asked for as JSON in the reply) and "coords":
"pixels" or "norm1000" (where its clicks land). `legato doctor --model` shows the same check.

A key pasted into `legato setup` is kept in the file as "api_key", readable only by you; otherwise it is read from
the environment variable named by "api_key_env". These environment variables win over the file: LEGATO_MODEL,
LEGATO_BASE_URL, LEGATO_API_KEY, LEGATO_DESKTOP, LEGATO_APP.

Desktops:
    background   macOS: one app's window, worked without your mouse or keyboard, so you keep using the Mac.
                 The app is named in the task ("... in Notes") or with `app`; otherwise Safari.
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


def env_name(var: str) -> str:
    """An environment variable as this platform's usual shell writes it: $VAR, or $env:VAR on Windows (PowerShell)."""
    return f"$env:{var}" if sys.platform == "win32" else f"${var}"


def set_env(var: str, value: str = "...") -> str:
    """The line that sets an environment variable in this platform's usual shell: export VAR=..., or
    $env:VAR = "..." on Windows (PowerShell)."""
    return f'$env:{var} = "{value}"' if sys.platform == "win32" else f"export {var}={value}"


def dot() -> str:
    """The separator between the parts of a printed line: a middle dot where stdout is UTF-8, else " - " (a Windows
    pipe or an older console shows the dot as another character)."""
    enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "").replace("_", "")
    return " \u00b7 " if enc.startswith("utf") else " - "


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
    coords: str = ""            # "pixels" or "norm1000" when setup found the model needs it; "": by the model's name
    tool_calls: str = ""        # "json": the model takes no tool calls, so actions are asked for as JSON in the reply
    default_from: str = ""      # not saved: the environment variable whose key picked the default model (no setup yet)
    extra: dict = field(default_factory=dict)       # keys this version does not know, kept as they were

    @property
    def key(self) -> str:
        """The model key: LEGATO_API_KEY, else the one saved by setup, else the variable named by api_key_env."""
        return (os.environ.get("LEGATO_API_KEY") or self.api_key
                or (os.environ.get(self.api_key_env, "") if self.api_key_env else ""))

    @property
    def key_source(self) -> str:
        """Where `key` comes from, by name and never by value: "the key in $LEGATO_API_KEY", "the key legato setup
        saved in <file>", "the key in $OPENROUTER_API_KEY"..., or "" when there is no key."""
        if os.environ.get("LEGATO_API_KEY"):
            return f"the key in {env_name('LEGATO_API_KEY')}"
        if self.api_key:
            return f"the key legato setup saved in {path()}"
        if self.api_key_env and os.environ.get(self.api_key_env):
            return f"the key in {env_name(self.api_key_env)}"
        return ""

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
            return f"no key for {self.base_url}: {set_env(env)}, or run legato setup"
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
    known = {f for f in Config.__dataclass_fields__ if f not in ("extra", "default_from")}
    cfg = Config(**{k: str(v) for k, v in data.items() if k in known and v is not None},
                 extra={k: v for k, v in data.items() if k not in known})
    saved_url = cfg.base_url or (PROVIDERS[cfg.provider].base_url if cfg.provider in PROVIDERS else "")
    other = os.environ.get("LEGATO_BASE_URL", "") if use_env else ""
    if other and saved_url and other.rstrip("/") != saved_url.rstrip("/"):
        # the saved key is the saved provider's: it is never sent to another server. LEGATO_API_KEY is the key for
        # a server named by LEGATO_BASE_URL.
        cfg.api_key, cfg.api_key_env, cfg.provider = "", "", ""
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
                cfg.default_from = pv.key_env
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
    data = {k: v for k, v in asdict(cfg).items() if k not in ("extra", "default_from") and v}
    data.update(cfg.extra)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(p)
    if sys.platform == "win32":       # chmod does not limit who reads a file on Windows: its access list does
        _windows_owner_only(p)
    return p


def _windows_owner_only(p: Path) -> bool:
    """Leave only the current user on the file's access list (no inherited entries). False if icacls failed."""
    import subprocess
    user = os.environ.get("USERNAME") or ""
    if not user:
        return False
    try:
        r = subprocess.run(["icacls", str(p), "/inheritance:r", "/grant:r", f"{user}:F"], capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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


# The browsers background mode works in. Others are refused (legato.background.other_browser): in Firefox a run's
# keys and screenshots reached the person's own window (24 Nov 2026), and from Chrome 158 pressing a web page's
# elements through Accessibility reports success and does nothing.
BROWSERS_TESTED = ("Safari",)


def pick_app(task: str, cfg: Config | None = None) -> str:
    """The app background mode works in for this task: the saved one, else the one the task names, else the default
    browser if background mode works in it (only Safari today), else Safari. A task that names another browser gets
    it, and background mode then refuses it with the reason."""
    cfg = cfg or load()
    browser = default_browser()
    return cfg.app or app_for_task(task) or (browser if browser in BROWSERS_TESTED else "Safari")


# ------------------------------------------------------------ checking a model
TEST_SIZE = (1280, 800)                 # the test image: a red square on grey, where pixels and the 0-1000 grid differ
TEST_TARGET = (960, 200)                # its centre in pixels: [750, 250] on the 0-1000 grid
TEST_TASK = "Click the centre of the red square."
VISION_MODELS = {"openrouter": ("qwen/qwen3.8-27b", "anthropic/claude-sonnet-5.5")}       # two that work (docs/models.md)


def check_image() -> bytes:
    """The check's screenshot as PNG: a 120-pixel red square centred on TEST_TARGET, on light grey."""
    import io

    from PIL import Image, ImageDraw
    im = Image.new("RGB", TEST_SIZE, (236, 236, 236))
    x, y = TEST_TARGET
    ImageDraw.Draw(im).rectangle([x - 60, y - 60, x + 60, y + 60], fill=(220, 30, 30))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def click_convention(x: float, y: float) -> str | None:
    """Which convention puts a click at (x, y) on the test image's red square: "pixels", "norm1000" (the 0-1000
    grid), "fraction" (0-1, which Legato does not read), or None when it is on the square in none of them."""
    (w, h), (tx, ty) = TEST_SIZE, TEST_TARGET
    if abs(x - tx) <= 80 and abs(y - ty) <= 80:
        return "pixels"
    if abs(x - tx * 1000 / w) <= 60 and abs(y - ty * 1000 / h) <= 90:
        return "norm1000"
    if 0 <= x <= 1 and 0 <= y <= 1 and abs(x - tx / w) <= 0.06 and abs(y - ty / h) <= 0.09:
        return "fraction"
    return None


def vision_models(base_url: str) -> str:
    """Two vision models known to work at this provider, as "A or B" ("" when we know none there)."""
    return " or ".join(next((m for p, m in VISION_MODELS.items() if PROVIDERS[p].base_url == base_url.rstrip("/")), ()))


def check_model(model: str, base_url: str, api_key: str = "", timeout_s: float = 60.0) -> dict:
    """Try what a run needs, with the agent's own prompt: one screenshot (the test image) and a tool call that clicks
    its red square. One model call when all is well; up to two more to say what is wrong.

    {"ok": can a run use it, "answers", "images": True | False | None (can't tell), "tool_calls": "native" | "json"
    (it takes none, so Legato asks for JSON in the reply) | None, "coords": "pixels" | "norm1000" | None (where its
    click landed), "coords_default": what Legato uses for this model's name, "click": [x, y] | None, "seconds",
    "reply", "error", "fix": one line, or None}. "sees_images" is "images" under its older name."""
    from legato.harness import Harness, _named_calls
    from legato.rl.policy import PolicyError
    out: dict = {"ok": False, "model": model, "base_url": base_url, "answers": False, "images": None, "tool_calls": None,
                 "coords": None, "coords_default": None, "click": None, "seconds": None, "reply": "", "error": None,
                 "fix": None, "sees_images": None}
    try:
        h = Harness(model, base_url, api_key or "", step_timeout_s=timeout_s, retries=0, max_batch=1)
        out["coords_default"] = h.coords
        ep = h.begin(TEST_TASK, check_image())
        try:
            plan = h.decide(ep)                  # falls back to JSON actions by itself when tool calls are refused
            calls = _named_calls(plan.rec)
            if not calls and h.native_tools:     # it answered in words: ask once more, for JSON in the reply
                h.native_tools = False
                again = h.decide(ep)
                if _named_calls(again.rec):
                    plan, calls = again, _named_calls(again.rec)
                else:
                    h.native_tools = True
        except PolicyError as e:
            return _why_not(h, out, str(e))
    except (KeyError, ValueError) as e:
        return {**out, "error": str(e)[:300], "fix": "check the model name and the URL"}
    rec = plan.rec
    out.update(answers=True, seconds=round(rec.get("s", 0.0), 2), reply=(rec.get("content") or "").strip()[:120])
    if not calls:
        cut = rec.get("finish_reason") == "length"
        out.update(error=("it used its whole reply thinking and made no action" if cut else
                          f"it answered, but with no action Legato can run: {out['reply'] or '(an empty reply)'}"),
                   fix="choose a model that can act on a screenshot" + (f", such as {vision_models(base_url)}"
                                                                         if vision_models(base_url) else ""))
        return out
    out["tool_calls"] = "native" if h.native_tools else "json"
    xy = next((a.get("coordinate") for _, a in calls if isinstance(a.get("coordinate"), (list, tuple))), None)
    try:
        x, y = float(xy[0]), float(xy[1])
    except (TypeError, ValueError, IndexError):
        out.update(ok=True, fix="it acted, but not with a click on the test image, so where its clicks land is unchecked")
        return out
    how = click_convention(x, y)
    out.update(ok=True, click=[round(x, 3), round(y, 3)], images=True if how else None, sees_images=True if how else None)
    tx, ty = TEST_TARGET
    if how in ("pixels", "norm1000"):
        out["coords"] = how
    elif how == "fraction":
        out["fix"] = ("it gives coordinates as 0-1 fractions, which Legato does not read: its clicks will land in the "
                      "top-left corner. Choose another model")
    else:
        out["fix"] = (f"its click at [{x:g}, {y:g}] missed the test square (at [{tx}, {ty}] in pixels, [750, 250] on the "
                      "0-1000 grid): it may not see the screenshot well enough to click. Try a task, or another model")
    return out


def _why_not(h, out: dict, error: str) -> dict:
    """The call with the screenshot failed: does the model answer at all (one text-only call)? Then it is the image."""
    import re

    from legato.rl.policy import PolicyError
    out["error"] = error[:300]
    names = vision_models(out["base_url"])
    if not re.search(r"HTTP (401|403|429)\b|did not answer|time limit", error):
        try:
            rec = h.client.chat([{"role": "user", "content": "Reply with the one word: ok"}], max_tokens=400,
                                temperature=0.0)
            out.update(answers=True, seconds=round(rec.get("s", 0.0), 2), reply=(rec.get("content") or "").strip()[:120])
        except PolicyError:
            pass
    if out["answers"] and re.search(r"image|vision|multi-?modal|text[- ]only|must be a string", error, re.I):
        out.update(images=False, sees_images=False,
                   fix="this model does not accept images; Legato needs a vision model"
                       + (f", for example {names}" if names else ""))
    elif out["answers"]:
        out["fix"] = ("it answers a text question but not one with a screenshot and a tool call (the error above); "
                      "Legato needs a vision model" + (f", for example {names}" if names else ""))
    elif "HTTP 404" in error or "not a valid model" in error:
        out["fix"] = "check the model's name at this provider"
    elif re.search(r"HTTP (401|403)\b", error):
        out["fix"] = "check the key"
    else:
        out["fix"] = "check the key, the model name and the URL"
    return out


def check_lines(r: dict) -> list[str]:
    """A check_model result as lines for a person: answers, images, tool calls, where a click lands; each with its
    fix when it is not right."""
    ok, no, unknown = "ok ", "no ", "?  "
    lines = [f"{ok} answers ({r['seconds']} s a call)" if r["answers"] else f"{no} answers: {r['error']}. Fix: {r['fix']}"]
    if not r["answers"]:
        return lines
    if r["images"] is False:
        return lines + [f"{no} accepts images: {r['error']}. Fix: {r['fix']}"]
    if not r["ok"]:
        return lines + [f"{no} acts on a screenshot: {r['error']}. Fix: {r['fix']}"]
    lines.append(f"{ok} accepts images" if r["images"] else f"{unknown} accepts images: it took the screenshot, but see its click below")
    lines.append(f"{ok} tool calls" if r["tool_calls"] == "native" else
                 f"{ok} actions as JSON in its reply: it takes no tool calls here, so Legato asks for JSON instead "
                 '(legato setup saves this as "tool_calls": "json"; in Python, Harness(native_tools=False))')
    if r["coords"]:
        name = {"pixels": "pixels", "norm1000": "the 0-1000 grid"}[r["coords"]]
        line = f"{ok} clicks in {name}: it clicked {r['click']} on the test square"
        if r["coords"] != r["coords_default"]:
            line += (f"; Legato's default for this model's name is {r['coords_default']}, so run legato setup "
                     f"--model {r['model']} --coords {r['coords']} (or pass --coords {r['coords']} to legato run)")
        lines.append(line)
    else:
        lines.append(f"{unknown} where its clicks land: {r['fix']}")
    return lines


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


NO_LOCAL_SERVER = ("no model server answered on this machine: looked for vLLM at http://localhost:8000/v1, Ollama at "
                   "http://localhost:11434/v1 and LM Studio at http://localhost:1234/v1. Start one (vllm serve "
                   "Qwen/Qwen3.5-9B --enable-auto-tool-choice --tool-call-parser hermes, or ollama serve, or LM "
                   "Studio's Developer tab > Start Server) and run this again, or pass --base-url and --model for a "
                   "server somewhere else")


def find_local_server(urls: tuple[str, ...] = LOCAL_SERVERS) -> tuple[str, list[str]] | None:
    """The first model server answering on this machine (vLLM :8000, Ollama :11434, LM Studio :1234) and its models."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:      # asked together: a closed port can take
        found = list(pool.map(lambda u: list_models(u, timeout_s=1.5), urls))   # seconds to refuse on Windows
    return next(((u, models) for u, models in zip(urls, found) if models), None)
