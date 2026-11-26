"""Read the user-visible title of exactly one fixture-owned native window."""
import ctypes
import os
import sys


class TitleReader:
    def __init__(self, pid, prefix):
        self.prefix=prefix
        self.close=lambda: None
        if sys.platform=="win32":
            from ctypes import wintypes as w
            u=ctypes.WinDLL("user32",use_last_error=True)
            u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
            u.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
            callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
            u.EnumWindows.argtypes=[callback_type,w.LPARAM]
            def title(hwnd):
                found=w.DWORD()
                u.GetWindowThreadProcessId(hwnd,ctypes.byref(found))
                if found.value!=pid:return ""
                value=ctypes.create_unicode_buffer(300)
                u.GetWindowTextW(hwnd,value,len(value))
                return value.value
            windows=[]
            @callback_type
            def add(hwnd,param):
                if title(hwnd).startswith(prefix):windows.append(hwnd)
                return True
            u.EnumWindows(add,0)
            if len(windows)!=1:raise RuntimeError(f"{len(windows)} matching Win32 windows")
            self._read=lambda:title(windows[0])
        elif sys.platform=="darwin":
            import ApplicationServices as ax
            app=ax.AXUIElementCreateApplication(pid)
            def attr(el,k):
                err,value=ax.AXUIElementCopyAttributeValue(el,k,None)
                if err:raise RuntimeError(f"AX read failed ({err})")
                return value
            wins=[w for w in attr(app,"AXWindows") if str(attr(w,"AXTitle")).startswith(prefix)]
            if len(wins)!=1:raise RuntimeError(f"{len(wins)} matching AX windows")
            self._read=lambda:str(attr(wins[0],"AXTitle"))
        else:
            from Xlib import display
            conn=display.Display()
            self.close=conn.close
            windows=[]; queue=[conn.screen().root]; checked=0
            while queue and checked<400:
                w=queue.pop();checked+=1
                try:
                    if (w.get_wm_name() or "").startswith(prefix):windows.append(w)
                    queue.extend(w.query_tree().children)
                except Exception:pass
            if len(windows)!=1:raise RuntimeError(f"{len(windows)} matching X11 windows")
            self._read=lambda:windows[0].get_wm_name() or ""

    def value(self):
        title=self._read()
        if not title.startswith(self.prefix):raise RuntimeError("The owned window changed identity")
        return int(title[len(self.prefix):])
