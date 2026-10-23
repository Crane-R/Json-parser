import ctypes
import sys

sys.path.insert(0, r"D:\Project\Json解析器")

import tkinter as tk  # noqa: E402

trace = []


def probe(tag):
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    trace.append((tag, hwnd))
    print(tag, "-> wrapper:", hex(hwnd or 0))


root = tk.Tk()
root.withdraw()
probe("after withdraw")

root.after_idle(lambda: probe("after_idle (withdrawn)"))
root.update_idletasks()
root.update()
probe("after update()")

root.deiconify()
root.update()
probe("after deiconify+update")

root.destroy()
