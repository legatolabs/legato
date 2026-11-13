"""Tools beside `computer`: give the model a shell (or your own functions) next to the screen.

    agent = legato.Harness(model, base_url, api_key, tools=[legato.tools.bash(env)])
    result = agent.run(task, env)

A tool's result goes back to the model as text in the next turn, ahead of the screenshot.
"""
from __future__ import annotations

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


@dataclass
class Tool:
    """A tool the model can call beside `computer`. `handler(args)` returns the text the model sees next turn."""
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], str]

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


def as_text(out: Any) -> str:
    """What a `run(cmd)` function returned, as text: str, bytes, or an object with `.stdout`."""
    if isinstance(out, (bytes, bytearray)):
        return out.decode(errors="replace")
    return str(getattr(out, "stdout", out) or "")


def wrap_command(cmd: str, timeout_s: float) -> str:
    """A command for a remote Linux shell: bash if it is there, stderr folded in, bounded by `timeout`, exit code last.
    The wrapper itself exits 0, so shells that raise on a failed command (E2B) return the output instead."""
    return (f"S=$(command -v bash || echo sh); timeout {max(1, int(timeout_s))} $S -c {shlex.quote(cmd)} 2>&1; "
            'echo "[exit $?]"')


def local_shell(cmd: str, timeout_s: float = 30.0) -> str:
    """Run `cmd` on this computer. On timeout the whole process group is killed, background children included."""
    unix = sys.platform != "win32"
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=unix, executable="/bin/bash" if unix and os.path.exists("/bin/bash") else None)
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
    return f"{as_text(out)}[exit {code}]"


def bash(target: Union["Env", Callable[[str], Any], None] = None, *, timeout_s: float = 30.0, max_chars: int = 4000,
         name: str = "bash") -> Tool:
    """A shell tool. `target`: an `Env` (runs in that desktop's shell: E2B, `attach(shell=...)`, `--sandbox docker`,
    `legato.local()`), a Linux `run(cmd) -> output` function, or None for this computer."""
    run: Callable[[str, float], str]
    if target is None:
        run = local_shell
    elif callable(getattr(target, "shell", None)):
        run = target.shell                        # type: ignore[union-attr]
    elif callable(target):
        user = target
        run = lambda cmd, t: as_text(user(wrap_command(cmd, t)))  # noqa: E731
    else:
        raise TypeError("bash(env), bash(run) or bash() for this computer; this desktop has no shell")

    def handler(args: dict) -> str:
        cmd = args.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return "error: give a command"
        return clip(run(cmd, timeout_s), max_chars)

    return Tool(name, BASH_HINT, {"type": "object", "required": ["command"],
                                  "properties": {"command": {"type": "string", "description": "The shell command"}}},
                handler)


__all__ = ["Tool", "ToolCall", "as_text", "bash", "clip", "local_shell", "wrap_command"]
