"""Read physical monitor geometry and effective DPI; no UI interaction."""
import ctypes as c
from ctypes import wintypes as w
import json
import sys


def monitors():
    if sys.platform != 'win32':
        return []
    u = c.windll.user32
    u.SetProcessDpiAwarenessContext(c.c_void_p(-4))
    class Info(c.Structure):
        _fields_ = [('size', w.DWORD), ('monitor', w.RECT), ('work', w.RECT),
                    ('flags', w.DWORD), ('device', w.WCHAR*32)]
    u.GetMonitorInfoW.argtypes = [w.HMONITOR, c.POINTER(Info)]
    c.windll.shcore.GetDpiForMonitor.argtypes = [w.HMONITOR, c.c_int, c.POINTER(w.UINT), c.POINTER(w.UINT)]
    rows = []
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HMONITOR, w.HDC, c.POINTER(w.RECT), w.LPARAM)
    def visit(handle, dc, rect, data):
        info = Info(); info.size = c.sizeof(info)
        u.GetMonitorInfoW(handle, c.byref(info))
        x, y = w.UINT(), w.UINT()
        c.windll.shcore.GetDpiForMonitor(handle, 0, c.byref(x), c.byref(y))
        r = info.monitor
        rows.append({'device': info.device, 'primary': bool(info.flags & 1),
                     'physicalBounds': [r.left, r.top, r.right, r.bottom], 'dpi': [x.value,y.value],
                     'scalePercent': round(x.value/96*100)})
        return True
    callback = callback_type(visit)
    u.EnumDisplayMonitors(None, None, callback, 0)
    return rows


if __name__ == '__main__':
    print(json.dumps(monitors(), ensure_ascii=False, indent=2))
