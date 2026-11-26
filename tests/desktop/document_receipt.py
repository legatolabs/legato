"""Time real edits and saves in an unmodified platform editor on a disposable native desktop."""
import codecs
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def scoped_text(path):
    if sys.platform == "darwin":
        from AppKit import NSRunningApplication
        import ApplicationServices as ax
        apps=NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.TextEdit")
        if not apps:
            raise LookupError("TextEdit not running")
        app=ax.AXUIElementCreateApplication(apps[0].processIdentifier())
        def get(el,key):
            status,value=ax.AXUIElementCopyAttributeValue(el,key,None)
            return value if not status else None
        for window in get(app,"AXWindows") or []:
            if path.stem not in str(get(window,"AXTitle")):
                continue
            queue=[window]
            while queue:
                element=queue.pop()
                if get(element,"AXRole")=="AXTextArea":
                    return lambda: str(get(element,"AXValue"))
                queue.extend(get(element,"AXChildren") or [])
        raise LookupError("document text area unavailable")
    if sys.platform == "win32":
        import ctypes as c
        from ctypes import wintypes as w
        u=c.WinDLL("user32",use_last_error=True)
        callback=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        u.EnumWindows.argtypes=[callback,w.LPARAM]
        u.EnumChildWindows.argtypes=[w.HWND,callback,w.LPARAM]
        u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
        u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
        u.GetForegroundWindow.restype=w.HWND
        u.SendMessageTimeoutW.argtypes=[w.HWND,w.UINT,w.WPARAM,c.c_void_p,w.UINT,w.UINT,c.POINTER(c.c_size_t)]
        def attr(fn,hwnd):
            value=c.create_unicode_buffer(512)
            fn(hwnd,value,len(value))
            return value.value
        windows=[]
        @callback
        def top(hwnd,_):
            if path.stem in attr(u.GetWindowTextW,hwnd):
                windows.append(hwnd)
            return True
        u.EnumWindows(top,0)
        if len(windows)!=1 or windows[0]!=u.GetForegroundWindow():
            raise LookupError("the owned editor window is still opening")
        children=[]
        @callback
        def visit(hwnd,_):
            if attr(u.GetClassNameW,hwnd).lower() in ("edit","richeditd2dpt","richedit50w"):
                children.append(hwnd)
            return True
        u.EnumChildWindows(windows[0],visit,0)
        if len(children)!=1:
            raise LookupError("the editor text control is still opening")
        def read():
            buf=c.create_unicode_buffer(4096)
            result=c.c_size_t()
            if not u.SendMessageTimeoutW(children[0],0x000D,len(buf),c.cast(buf,c.c_void_p),2,500,c.byref(result)):
                raise OSError("owned Notepad edit control unavailable")
            return buf.value
        return read
    sys.path.append("/usr/lib/python3/dist-packages")
    import pyatspi as at
    for app in at.Registry.getDesktop(0):
        if "mousepad" not in app.name.lower():
            continue
        for win in app:
            if path.stem not in (win.name or ""):
                continue
            queue=[win]
            while queue:
                node=queue.pop()
                if (node.getRole()==at.ROLE_TEXT and node.getState().contains(at.STATE_EDITABLE)
                        and node.getState().contains(at.STATE_MULTI_LINE) and node.getState().contains(at.STATE_SHOWING)):
                    node.queryComponent().grabFocus()
                    iface=node.queryText()
                    return lambda:iface.getText(0,-1)
                queue.extend(child for child in node if child is not None)
    raise LookupError("Mousepad document text unavailable")


def disk(path):
    b=path.read_bytes()
    text=b.decode("utf-16" if b.startswith((codecs.BOM_UTF16_LE,codecs.BOM_UTF16_BE)) else "utf-8-sig")
    return text.removesuffix("\n") if sys.platform.startswith("linux") else text


def get_reader(path):
    deadline=time.monotonic()+25
    while True:
        try:
            return scoped_text(path)
        except LookupError:
            if time.monotonic()>=deadline:
                raise
            time.sleep(.2)


def worker(path,arm):
    import legato
    from legato import Action
    assert Path(legato.__file__).resolve().is_relative_to(Path(os.environ["SDK_UNDER_TEST"]).resolve())
    env=legato.local(hide_terminal=False,fixed_wait_s=0 if arm=="manual" else None)
    try:
        text=get_reader(path)
        print(json.dumps({"initial":text()}),flush=True)
        for line in sys.stdin:
            req=json.loads(line)
            start=time.perf_counter()
            target=req["target"]
            stages=[]
            for action,read in ((Action("type",text=req["append"]),text),(Action("key",keys="ctrl+s"),lambda:disk(path))):
                began=time.perf_counter()
                if arm=="sdk":
                    observed=env.act(action,read=read,until=lambda s:s==target,timeout_s=5)
                else:
                    env.act(action)
                    while (observed:=read())!=target:
                        if time.perf_counter()-began>5:
                            raise TimeoutError((arm,action.type,observed,target))
                        time.sleep(.01)
                stages.append((time.perf_counter()-began)*1000)
            print(json.dumps(dict(ms=(time.perf_counter()-start)*1000,phase_ms=stages,readback=observed)),flush=True)
    finally:
        env.close()


def run():
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/"legato-receipt-73914.txt"
        path.write_text("",encoding="utf-8")
        cmds=(["open","-a","TextEdit",str(path)] if sys.platform=="darwin" else
              ["notepad.exe",str(path)] if sys.platform=="win32" else ["mousepad","--disable-server",str(path)])
        app=subprocess.Popen(cmds)
        workers={}
        try:
            get_reader(path)
            if sys.platform.startswith("linux"):
                windows=subprocess.check_output(["xdotool","search","--onlyvisible","--name",path.stem],text=True).splitlines()
                if len(windows)!=1:
                    raise RuntimeError("expected one owned Mousepad window")
                subprocess.run(["xdotool","windowactivate","--sync",windows[0]],check=True,timeout=5)
            for arm in ("default","manual","sdk"):
                repo=root if arm=="sdk" else root/"baseline"
                proc=subprocess.Popen([sys.executable,"-u",__file__,str(path),arm],cwd=repo,
                    env={**os.environ,"PYTHONPATH":str(repo),"SDK_UNDER_TEST":str(repo)},text=True,
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                workers[arm]=proc
                state=json.loads(proc.stdout.readline())
                if state["initial"]!="":
                    raise AssertionError(state)
            expected=""
            for pair in range(18):
                for arm in list(itertools.permutations(workers))[pair%6]:
                    token="R"+str(1000+pair*31+list(workers).index(arm)*7)
                    expected+=token
                    w=workers[arm]
                    w.stdin.write(json.dumps(dict(append=token,target=expected))+"\n")
                    w.stdin.flush()
                    r=json.loads(w.stdout.readline())
                    data=path.read_bytes()
                    valid=r.pop("readback")==expected and disk(path)==expected
                    print("DOCUMENT",json.dumps(dict(arm=arm,pair=pair,valid=valid,bytes=len(data),
                                                  sha256=hashlib.sha256(data).hexdigest(),**r)),flush=True)
                    if not valid:
                        raise AssertionError((arm,pair))
        finally:
            for w in workers.values():
                w.stdin.close()
                w.wait(timeout=5)
            if app.poll() is None:
                app.terminate()
                app.wait(timeout=5)


if __name__=="__main__":
    if len(sys.argv)==1:
        run()
    else:
        worker(Path(sys.argv[1]),sys.argv[2])
