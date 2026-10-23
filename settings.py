"""轻量本地配置：记住窗口大小 / 位置 / 最大化状态等。

配置写在 %APPDATA%\\JSON万能解析器\\settings.json，
打包成 exe 后同样生效，不污染程序目录。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "JSON万能解析器"


def config_directory() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


class Settings:
    def __init__(self, name: str = "settings.json"):
        self.path = config_directory() / name
        self.data: dict[str, object] = {}
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except OSError:
            pass


def dpi_scaling(root) -> float:
    """当前 Tk 缩放系数（高 DPI 屏上通常是 1.5 / 2.0）。"""
    try:
        value = float(root.tk.call("tk", "scaling"))
    except Exception:
        return 1.0
    return max(1.0, min(value, 3.0))


def default_geometry(root, logical_width=1320, logical_height=900) -> str:
    """按屏幕 DPI 换算默认窗口尺寸，并居中；高 DPI 下不会开出小窗口。"""
    factor = dpi_scaling(root)
    screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
    width = min(int(logical_width * factor), max(int(screen_width * .92), 700))
    height = min(int(logical_height * factor), max(int(screen_height * .88), 520))
    left = max((screen_width - width) // 2, 0)
    top = max((screen_height - height) // 2, 0)
    return f"{width}x{height}+{left}+{top}"


def minimum_size(root, width=1060, height=800) -> tuple[int, int]:
    """最小窗口尺寸：故意不随 DPI 放大，否则高 DPI 屏上窗口会几乎无法缩小。"""
    screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
    return min(width, int(screen_width * .7)), min(height, int(screen_height * .7))


def parse_geometry(text) -> tuple[int, int, int, int] | None:
    """解析 Tk geometry 字符串，失败返回 None。"""
    if not isinstance(text, str):
        return None
    try:
        size, _, position = text.partition("+")
        width, _, height = size.partition("x")
        left, _, top = position.partition("+")
        width, height, left, top = int(width), int(height), int(left), int(top)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height, left, top


def clamp_geometry(root, text, margin=120) -> str | None:
    """把历史几何收敛到当前屏幕内，避免窗口跑到显示器外面。"""
    parsed = parse_geometry(text)
    if parsed is None:
        return None
    width, height, left, top = parsed
    floor_width, floor_height = minimum_size(root)
    width, height = max(width, floor_width), max(height, floor_height)
    screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
    width = min(width, screen_width)
    height = min(height, screen_height - 40)
    left = min(max(left, -width + margin), screen_width - margin)
    top = min(max(top, 0), max(screen_height - margin, 0))
    return f"{width}x{height}+{left}+{top}"


def remember_window_now(root, settings: Settings, geometry=None, key: str = "window") -> None:
    """保存当前窗口几何。geometry 为 None 时用实时值（最大化时会失真）。"""
    try:
        current = root.geometry()
        zoomed = root.state() == "zoomed"
    except Exception:
        return
    settings.set(key, {"geometry": geometry or current, "zoomed": zoomed})
    settings.save()


def restore_window(root, settings: Settings, key: str = "window",
                   default: str = "1320x900") -> bool:
    """恢复上次的窗口大小与位置，返回是否应用成功。"""
    state = settings.get(key)
    if not isinstance(state, dict):
        return False
    geometry = clamp_geometry(root, state.get("geometry"))
    if geometry is None:
        return False
    try:
        root.geometry(geometry)
    except Exception:
        return False
    if state.get("zoomed"):
        root.after(60, lambda: _zoom(root))
    return True


def _zoom(root) -> None:
    try:
        root.state("zoomed")
    except Exception:
        pass
