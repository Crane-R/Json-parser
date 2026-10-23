"""无界面验证：窗口类背景刷是否真正写入（黑闪修复）。

不弹任何窗口（root 保持 withdrawn），只做句柄级验证：
1. 读取 Tk 顶级窗口类当前的背景刷；
2. 调用 main._set_class_background 写入界面底色刷；
3. 重新读取类背景刷，必须与写入的刷子句柄一致；
4. 验证刷子真实存在（GetObject 能取回 LOGBRUSH 且颜色正确）。

历史上的「读回值不匹配」是 GetClassLongPtrW 未设置 restype，
默认 c_int 把 64 位句柄截断成 32 位导致，本脚本用正确声明复核。
"""
import ctypes
import sys
import tkinter as tk

sys.path.insert(0, r"D:\Project\Json解析器")

GCLP_HBRBACKGROUND = -10


def main():
    import main as app_main
    root = app_main.create_root(hidden=True)  # alpha=0，无视觉影响
    root.update_idletasks()
    root.update()
    root.deiconify()  # 触发 <Map> -> polish_window（此时 wrapper 必然存在）
    root.update()
    root.withdraw()
    root.update()
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        assert hwnd, "拿不到 Tk 顶级窗口句柄"

        getter = ctypes.windll.user32.GetClassLongPtrW
        getter.restype = ctypes.c_void_p
        getter.argtypes = [ctypes.c_void_p, ctypes.c_int]
        brush = getter(hwnd, GCLP_HBRBACKGROUND)
        assert brush, "类背景刷为空：_set_class_background 未生效或未执行"

        assert brush == app_main._CLASS_BRUSH, (
            f"类刷 {hex(brush)} != 模块刷 {hex(app_main._CLASS_BRUSH or 0)}")

        # 刷子必须真实可用：取回 LOGBRUSH 验证颜色
        logbrush = ctypes.create_string_buffer(24)
        size = ctypes.windll.gdi32.GetObjectW(
            ctypes.c_void_p(brush), 24, logbrush)
        assert size >= 12, "GetObject 取不回刷子信息"
        lb_style = int.from_bytes(logbrush.raw[0:4], "little")
        lb_color = int.from_bytes(logbrush.raw[4:8], "little") & 0xFFFFFF
        assert lb_style == 0, f"不是实心刷（style={lb_style}）"

        from desktop_ui import BACKGROUND
        expected = (int(BACKGROUND[1:3], 16)
                    | int(BACKGROUND[3:5], 16) << 8
                    | int(BACKGROUND[5:7], 16) << 16)
        assert lb_color == expected, f"刷色 {hex(lb_color)} != 界面底色 {hex(expected)}"

        # 再来一次幂等调用，确认不会重复建刷
        before = app_main._CLASS_BRUSH
        app_main._set_class_background(hwnd)
        assert app_main._CLASS_BRUSH == before, "幂等调用却新建了刷子"

        print("PASS class-brush:", hex(brush), "color:", hex(lb_color))
        return 0
    finally:
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
