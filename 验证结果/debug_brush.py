import ctypes
import sys

sys.path.insert(0, r"D:\Project\Json解析器")

import main as app_main  # noqa: E402
from desktop_ui import BACKGROUND  # noqa: E402

root = app_main.create_root()
root.update_idletasks()
root.update()

child = root.winfo_id()
hwnd = ctypes.windll.user32.GetParent(child)
print("child:", hex(child), "wrapper:", hex(hwnd or 0))
print("_CLASS_BRUSH:", app_main._CLASS_BRUSH)

if hwnd:
    value = (int(BACKGROUND[1:3], 16) | int(BACKGROUND[3:5], 16) << 8
             | int(BACKGROUND[5:7], 16) << 16)
    brush = ctypes.windll.gdi32.CreateSolidBrush(value)
    print("new brush:", hex(brush))

    setter = ctypes.windll.user32.SetClassLongPtrW
    setter.restype = ctypes.c_void_p
    setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    old = setter(hwnd, -10, brush)
    print("SetClassLongPtrW returned old value:", hex(old or 0),
          "last_error:", ctypes.get_last_error())

    getter = ctypes.windll.user32.GetClassLongPtrW
    getter.restype = ctypes.c_void_p
    getter.argtypes = [ctypes.c_void_p, ctypes.c_int]
    now = getter(hwnd, -10)
    print("read back GCLP_HBRBACKGROUND:", hex(now or 0))

    # ClassName
    name = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, name, 64)
    print("class name:", name.value)

root.destroy()
