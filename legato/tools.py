"""Tools beside `computer`: give the model a shell (or your own functions) next to the screen.

    agent = legato.Harness(model, base_url, api_key, tools=[legato.tools.bash(env)])
    result = agent.run(task, env)

A tool's result goes back to the model as text in the next turn, ahead of the screenshot.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Union

if TYPE_CHECKING:
    from legato.env import Env

NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")         # what OpenAI-compatible APIs accept as a function name
BASH_HINT = ("Run a shell command on the desktop's computer and get its output (stdout and stderr) and exit code. "
             "Use it to read or write files, check results and run programs; it is often faster than the screen. "
             "Start a GUI app in the background (nohup app >/dev/null 2>&1 &), or the command waits for it to exit.")
POWERSHELL_HINT = ("Run a PowerShell command on this Windows computer and get its output and exit code. Use it to read "
                   "or write files, check results and run programs; it is often faster than the screen. Start a GUI "
                   "app with Start-Process, or the command waits for it to exit.")


@dataclass
class Tool:
    """A tool the model can call beside `computer`. `handler(args)` returns the text the model sees next turn."""
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], str]
    runs: str = ""                       # for a shell tool: what runs its commands ("bash", "PowerShell")

    def __post_init__(self) -> None:
        if not NAME.match(self.name):
            raise ValueError(f"tool name {self.name!r} must match {NAME.pattern}")

    def spec(self) -> dict:
        """The tool in OpenAI function format."""
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


@dataclass
class ToolCall:
    """One call to a tool other than `computer`, in a `Plan`'s actions."""
    name: str
    args: dict = field(default_factory=dict)

    def describe(self) -> str:
        s = json.dumps(self.args, ensure_ascii=False)
        return f"{self.name}({s[:80] + '...' if len(s) > 80 else s})"


def clip(text: str, max_chars: int) -> str:
    """The start and end of a long output, with how much was cut."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n...({len(text) - 2 * half} characters cut)...\n{text[-half:]}"


def wrap_command(cmd: str, timeout_s: float) -> str:
    """A command for a remote Linux shell: bash if it is there, stderr folded in, bounded by `timeout`, exit code last.
    The wrapper itself exits 0, so shells that raise on a failed command (E2B) return the output instead."""
    return (f"S=$(command -v bash || echo sh); timeout {max(1, int(timeout_s))} $S -c {shlex.quote(cmd)} 2>&1; "
            'echo "[exit $?]"')


def local_shell(cmd: str, timeout_s: float = 30.0) -> str:
    """Run `cmd` on this computer. On timeout the whole process group is killed, background children included."""
    unix = sys.platform != "win32"
    if unix:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, executable="/bin/bash" if os.path.exists("/bin/bash") else None)
    else:       # Windows: PowerShell (what its files, settings and apps are scripted in), with no console window
        p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out, _ = p.communicate(timeout=timeout_s)
        code: Any = p.returncode
    except subprocess.TimeoutExpired:
        if unix:
            os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
        out, _ = p.communicate()
        code = f"timeout after {timeout_s:g} s"
    return f"{(out or b'').decode(errors='replace')}[exit {code}]"


def bash(target: Union["Env", Callable[[str], Any], None] = None, *, timeout_s: float = 30.0, max_chars: int = 4000,
         name: str = "bash") -> Tool:
    """A shell tool. `target`: an `Env` (runs in that desktop's shell: E2B, `attach(shell=...)`, `--sandbox docker`,
    `legato.local()`), a Linux `run(cmd) -> output` function, or None for this computer (bash; PowerShell on
    Windows)."""
    run: Callable[[str, float], str]
    if target is None:
        run = local_shell
    elif callable(getattr(target, "shell", None)):
        run = target.shell                        # type: ignore[union-attr]
    elif callable(target):
        from legato.env import _stdout            # here, not at the top: legato.env imports this module
        user = target
        run = lambda cmd, t: _stdout(user(wrap_command(cmd, t)))  # noqa: E731
    else:
        raise TypeError("bash(env), bash(run) or bash() for this computer; this desktop has no shell")
    windows = run is local_shell and sys.platform == "win32"      # this computer, also as bash(legato.local())

    def handler(args: dict) -> str:
        cmd = args.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return "error: give a command"
        return clip(run(cmd, timeout_s), max_chars)

    return Tool(name, POWERSHELL_HINT if windows else BASH_HINT,
                {"type": "object", "required": ["command"],
                 "properties": {"command": {"type": "string", "description": "The shell command"}}},
                handler, runs="PowerShell" if windows else "bash")


APPLESCRIPT_HINT = ("Run AppleScript to read or change an app's data (Calendar, Reminders, Notes, Contacts, Mail, Music, "
                    "Finder and other scriptable apps). The script is compiled against the app's dictionary first and "
                    "does not run if it does not compile. On an error you also get the app's real classes, properties "
                    "and commands: use those names. Give `check`, a read-only script that reads back what you changed, "
                    "and read its output before you say done. After two failed scripts for one app in a task, scripts "
                    "for it are refused: use the screen for that app.")
APP_DIRS = ("/System/Applications", "/Applications", "/System/Applications/Utilities", "/System/Library/CoreServices")
_TELL = re.compile(r'\btell\s+(?:application|app)\s+"([^"]+)"', re.I)
_EXIT = re.compile(r"\[exit (-?\d+|timeout[^\]]*)\]\s*$")


def _exit_of(out: str) -> tuple[str, str]:
    """(output without the exit line, exit code as text) of a shell result."""
    m = _EXIT.search(out)
    return (out[:m.start()].rstrip(), m.group(1)) if m else (out, "?")


def dictionary_summary(sdef_xml: str, max_chars: int = 3000) -> str:
    """The app-specific part of a scripting dictionary (`sdef App.app`): its classes with their properties and
    elements, and its commands with their parameters. The Standard and Text suites are left out."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(sdef_xml)
    except ET.ParseError:
        return ""
    lines = []
    for suite in root.iter("suite"):
        if suite.get("name", "") in ("Standard Suite", "Text Suite", "Type Definitions"):
            continue
        for c in list(suite.iter("class")) + list(suite.iter("class-extension")):
            name = c.get("name") or f"(more of {c.get('extends')})"
            props = [p.get("name") for p in c.iter("property") if p.get("name")]
            elems = [e.get("type") for e in c.iter("element") if e.get("type")]
            bits = []
            if props:
                bits.append("properties: " + ", ".join(props))
            if elems:
                bits.append("elements: " + ", ".join(elems))
            lines.append(f"class {name}" + (f" ({'; '.join(bits)})" if bits else ""))
        for cmd in suite.iter("command"):
            params = [q.get("name") for q in cmd.iter("parameter") if q.get("name")]
            lines.append(f"command {cmd.get('name')}" + (f" ({', '.join(params)})" if params else ""))
    return clip("\n".join(lines), max_chars)


def applescript(target: Union["Env", Callable[[str], Any], None] = None, *, timeout_s: float = 30.0,
                max_chars: int = 4000, max_failures: int = 2, name: str = "applescript") -> Tool:
    """AppleScript, checked: compiled first (osacompile; nothing runs if it does not compile), the app's dictionary
    with any error, an optional read-only `check` run after, and no more scripts for an app after `max_failures`
    failed ones in this tool's life (one task). macOS only; `target` as for bash()."""
    if target is None:
        run = local_shell
    elif callable(getattr(target, "shell", None)):
        run = target.shell                        # type: ignore[union-attr]
    else:
        raise TypeError("applescript(env) or applescript() for this computer (macOS)")
    failures: dict[str, int] = {}
    summaries: dict[str, str] = {}
    n = [0]

    def write(script: str, tag: str) -> str:
        n[0] += 1
        path = f"/tmp/legato-as-{os.getpid()}-{n[0]}-{tag}.applescript"
        # as base64, which no shell reads anything into: in a heredoc, a script with the closing word on a line of
        # its own ended the heredoc there and ran the lines after it as shell commands
        data = base64.b64encode(script.encode("utf-8")).decode("ascii")
        return path, f"printf %s {shlex.quote(data)} | base64 --decode > {shlex.quote(path)} && "

    def summary(app: str) -> str:
        if app not in summaries:
            paths = " ".join(shlex.quote(f"{d}/{app}.app") for d in APP_DIRS)
            out, code = _exit_of(run(f'for p in {paths}; do [ -d "$p" ] && {{ sdef "$p"; exit 0; }}; done; exit 1',
                                     timeout_s))
            summaries[app] = dictionary_summary(out) if code == "0" else ""
        s = summaries[app]
        return f"\n\n{app}'s dictionary:\n{s}" if s else ""

    def failed(apps: list[str], text: str) -> str:
        for a in apps or ["?"]:
            failures[a] = failures.get(a, 0) + 1
        return clip(text + "".join(summary(a) for a in apps), max_chars)

    def handler(args: dict) -> str:
        script = args.get("script")
        if not isinstance(script, str) or not script.strip():
            return "error: give a script"
        apps = list(dict.fromkeys(_TELL.findall(script)))
        out_of = [a for a in apps if failures.get(a, 0) >= max_failures]
        if out_of:
            return (f"not run: AppleScript for {out_of[0]} already failed {max_failures} times in this task. "
                    "Use the screen for that app.")
        path, cmd = write(script, "main")
        out, code = _exit_of(run(cmd + f"osacompile -o {shlex.quote(path + '.scpt')} {shlex.quote(path)}", timeout_s))
        if code != "0":
            return failed(apps, f"did not compile, so nothing ran:\n{out}")
        out, code = _exit_of(run(f"osascript {shlex.quote(path + '.scpt')}", timeout_s))
        if code != "0":
            return failed(apps, f"compiled, then failed when it ran (exit {code}):\n{out}")
        text = f"ran (exit 0). Result:\n{out or '(nothing)'}"
        check = args.get("check")
        if isinstance(check, str) and check.strip():
            cpath, ccmd = write(check, "check")
            cout, ccode = _exit_of(run(ccmd + f"osascript {shlex.quote(cpath)}", timeout_s))
            text += f"\n\ncheck (exit {ccode}):\n{cout or '(nothing)'}"
        return clip(text, max_chars)

    return Tool(name, APPLESCRIPT_HINT, {"type": "object", "required": ["script"], "properties": {
        "script": {"type": "string", "description": "The AppleScript to run"},
        "check": {"type": "string", "description": "A read-only AppleScript that reads back what the script changed"}}},
        handler)


__all__ = ["Tool", "ToolCall", "applescript", "bash", "clip", "dictionary_summary", "local_shell", "wrap_command"]
