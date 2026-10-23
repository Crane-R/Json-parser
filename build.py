import io
import itertools
import struct
import subprocess
import sys
import shutil
from importlib import import_module
from pathlib import Path

from versioning import VERSION, version_parts


# 只在开发期用到、运行时不需要的模块，剔除后包体更小、加载更快
EXCLUDED_MODULES = ("customtkinter", "PIL", "tkinter.test", "unittest", "pytest",
                    "pydoc", "doctest", "lib2to3", "setuptools", "pip", "distutils",
                    "pyinstaller", "PyInstaller")

# 解析器按需动态导入的模块，PyInstaller 静态分析不到，必须显式声明
HIDDEN_IMPORTS = tuple(import_module("parser_engine").LAZY_MODULES)


def publish_bundle(source: Path, output: Path):
    """把 onedir 产物目录发布到 output 下；只认「JSON万能解析器-」前缀，其余文件一律不碰。"""
    output.mkdir(parents=True, exist_ok=True)
    for entry in output.iterdir():
        if not entry.name.startswith("JSON万能解析器-"):
            raise ValueError(f"output 中存在非工具产物，请先移出：{entry.name}")
    target = output / source.name
    shutil.copytree(source, target, dirs_exist_ok=True)
    return target


def main():
    # 打包期才需要的重依赖，放到函数内导入，避免被测试进程顺带加载
    from PIL import Image, ImageDraw, ImageFont
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct, VSVersionInfo)

    root = Path(__file__).resolve().parent
    artifact_name = f"JSON万能解析器-{VERSION}"
    build_dir = root / "build"
    build_dir.mkdir(exist_ok=True)
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    # ---- 图标：逐尺寸超采样渲染 + PNG 多帧 ICO ----
    # 旧做法 image.save(ico, sizes=[...]) 在 Pillow 12 下只会嵌入 256 一帧，
    # 任务栏/标题栏拿不到原生小尺寸，只能拿 256 硬缩，这就是图标发糊的根因。
    def render_icon(size: int):
        ss = 8 if size <= 64 else 4          # 超采样倍数，小尺寸也保持锐利
        S = size * ss
        top, bot = (124, 92, 240), (91, 63, 214)
        grad = Image.new("RGBA", (1, S))
        px = grad.load()
        for y in range(S):
            t = y / (S - 1)
            px[0, y] = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,)
        grad = grad.resize((S, S))
        m, r = round(S * 0.05), round(S * 0.22)
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], radius=r, fill=255)
        bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        bg.paste(grad, (0, 0), mask)
        draw = ImageDraw.Draw(bg)
        hi = Image.new("L", (S, S), 0)
        ImageDraw.Draw(hi).rounded_rectangle([m, m, S - m, (S - m + m) // 2], radius=r, fill=38)
        bg.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), (0, 0), hi)
        font = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", round(S * 0.56))
        bbox = draw.textbbox((0, 0), "{}", font=font)
        tx = (S - (bbox[2] - bbox[0])) / 2 - bbox[0]
        ty = (S - (bbox[3] - bbox[1])) / 2 - bbox[1]
        draw.text((tx, ty), "{}", font=font, fill=(255, 255, 255, 255))
        return bg.resize((size, size), Image.LANCZOS)

    def write_png_ico(images, path):
        # PIL 的 ICO 保存写不出多尺寸帧，这里手工组装 PNG 条目式 ICO（Vista+ 原生支持）
        blobs = []
        for im in images:
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            blobs.append(buf.getvalue())
        raw = bytearray(struct.pack("<HHH", 0, 1, len(blobs)))
        ptr = 6 + 16 * len(blobs)
        for im, blob in zip(images, blobs):
            w, h = im.size
            raw += struct.pack("BBBB", w % 256, h % 256, 0, 0)
            raw += struct.pack("<HH", 1, 32)
            raw += struct.pack("<II", len(blob), ptr)
            ptr += len(blob)
        for blob in blobs:
            raw += blob
        path.write_bytes(bytes(raw))

    icon = assets / "app.ico"
    write_png_ico([render_icon(s) for s in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)], icon)
    version_file = build_dir / "version-info.txt"
    version_tuple = (*version_parts(VERSION), 0)
    metadata = VSVersionInfo(
        ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple, mask=0x3f, flags=0,
                          OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("080404B0", [
            StringStruct("FileDescription", "JSON 万能解析器"),
            StringStruct("FileVersion", VERSION),
            StringStruct("ProductName", "JSON 万能解析器"),
            StringStruct("ProductVersion", VERSION),
            StringStruct("OriginalFilename", artifact_name + ".exe"),
        ])]), VarFileInfo([VarStruct("Translation", [2052, 1200])])],
    )
    version_file.write_text(str(metadata), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
    # onedir：启动时不再把整包解压到临时目录（原来那一步就要 3 秒左右），
    # 代价是产物是一个目录而不是单文件 exe。
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
                    "--noupx", "--name", artifact_name, "--icon", str(icon), "--version-file", str(version_file),
                    "--distpath", str(build_dir / "package"), "--specpath", str(build_dir),
                    "--workpath", str(build_dir / "pyinstaller"),
                    "--collect-all", "tkinterdnd2",
                    "--add-data", str(icon) + ";assets", "--add-data", str(root / "VERSION") + ";.",
                    *itertools.chain.from_iterable(("--exclude-module", name) for name in EXCLUDED_MODULES),
                    *itertools.chain.from_iterable(("--hidden-import", name) for name in HIDDEN_IMPORTS),
                    str(root / "main.py")], cwd=root, check=True)
    bundle = build_dir / "package" / artifact_name
    executable = bundle / (artifact_name + ".exe")
    report = build_dir / "packaged-self-test.json"
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    subprocess.run([str(executable), "--self-test", str(report)], cwd=root, check=True,
                   timeout=90, creationflags=creation_flags)
    print(publish_bundle(bundle, root / "output"))


if __name__ == "__main__":
    main()
