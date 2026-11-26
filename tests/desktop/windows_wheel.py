"""On a disposable Windows desktop, verify delivered wheel events in a form owned by this test.

Opens a temporary topmost window, moves the cursor inside it
and scrolls. It checks the target window's process before each event and terminates only its own form.
"""
import ctypes
import ctypes.wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


FORM = r"""
using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

public sealed class WheelProbe : Form {
    private readonly string folder;
    public WheelProbe(string folder) {
        this.folder = folder;
        Text = "Legato wheel acceptance";
        Size = new Size(420, 220);
        StartPosition = FormStartPosition.CenterScreen;
        TopMost = true;
        Shown += delegate {
            Activate();
            Focus();
            File.WriteAllText(Path.Combine(folder, "ready.tmp"), Handle.ToInt64().ToString());
            File.Move(Path.Combine(folder, "ready.tmp"), Path.Combine(folder, "ready.txt"));
        };
    }
    protected override void WndProc(ref Message m) {
        if (m.Msg == 0x20A || m.Msg == 0x20E) {
            short delta = unchecked((short)((m.WParam.ToInt64() >> 16) & 65535));
            File.AppendAllText(Path.Combine(folder, "wheel.txt"), m.Msg + "," + delta + Environment.NewLine);
        }
        base.WndProc(ref m);
    }
}
"""


def main():
    if os.name != "nt":
        sys.exit("This probe needs a disposable interactive Windows desktop.")
    import legato
    from legato import winread
    assert Path(legato.__file__).resolve().is_relative_to(Path(os.environ["BENCH_CHECKOUT"]).resolve())

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = w.HWND
    user32.WindowFromPoint.argtypes = [w.POINT]
    user32.WindowFromPoint.restype = w.HWND
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user32.ClientToScreen.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]

    def owner(hwnd):
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    with tempfile.TemporaryDirectory() as folder:
        dest = Path(folder)
        (dest / "WheelProbe.cs").write_text(FORM, encoding="utf-8")
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing "
            "-Path (Join-Path $env:LEGATO_WHEEL_TEST_DIR WheelProbe.cs)\n"
            "[System.Windows.Forms.Application]::Run([WheelProbe]::new($env:LEGATO_WHEEL_TEST_DIR))"
        )
        proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                env={**os.environ, "LEGATO_WHEEL_TEST_DIR": folder},
                                creationflags=subprocess.CREATE_NO_WINDOW)
        env = None
        try:
            until = time.monotonic() + 15
            while not (dest / "ready.txt").exists() and time.monotonic() < until:
                if proc.poll() is not None:
                    raise RuntimeError("The test form exited before opening")
                time.sleep(0.05)
            if not (dest / "ready.txt").exists():
                raise RuntimeError("The test form did not report a window handle")
            hwnd = int((dest / "ready.txt").read_text())
            env = legato.local(hide_terminal=False, fixed_wait_s=0.1)
            point = w.POINT(50, 50)
            if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
                raise ctypes.WinError(ctypes.get_last_error())
            x, y = point.x, point.y
            for direction in ("up", "down", "left", "right"):
                foreground = owner(user32.GetForegroundWindow())
                target = owner(user32.WindowFromPoint(w.POINT(x, y)))
                if foreground != proc.pid or target != proc.pid:
                    raise RuntimeError("The fixture does not own the foreground and target point")
                env.act(legato.Action("scroll", x=x, y=y, direction=direction, amount=3))
            log = dest / "wheel.txt"
            rows = [tuple(map(int, row.split(","))) for row in log.read_text().splitlines()] if log.exists() else []
            # A driver can deliver three notches in one message or several.
            totals = []
            for axis, delta in rows:
                if totals and totals[-1][0] == axis and (totals[-1][1] > 0) == (delta > 0):
                    totals[-1] = (axis, totals[-1][1] + delta)
                else:
                    totals.append((axis, delta))
            expected = [(0x20A, 360), (0x20A, -360), (0x20E, -360), (0x20E, 360)]
            print(json.dumps({"expected": expected, "received": rows, "matches": totals == expected}))
            if os.environ["BENCH_ARM"] == "candidate":
                assert totals == expected, "Incorrect wheel axis or delta"
            times = []
            from legato.tools import local_shell
            for i in range(5):
                assert owner(user32.GetForegroundWindow()) == proc.pid
                begin=time.perf_counter()
                outline = winread.read(local_shell, max_chars=8000, timeout_s=15)
                elapsed=(time.perf_counter()-begin)*1000
                assert "Legato wheel acceptance" in outline, outline[:500]
                times.append({"read_ms":elapsed,"warmup":i==0})
            data = {"received":rows,"matches":totals==expected,"readings":times}
            Path(os.environ["BENCH_OUT"]).write_text(json.dumps(data))
            print(os.environ["BENCH_ARM"],json.dumps(data))
        finally:
            if callable(close := getattr(env, "close", None)):
                close()
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
