import argparse
import re
import sys
from pathlib import Path


def version_parts(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError("版本号必须采用 0.0.1 格式。")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 65535 for part in parts):
        raise ValueError("每段版本号不能超过 65535。")
    return parts


def next_version(value: str, level: str) -> str:
    parts = list(version_parts(value))
    position = ("major", "minor", "patch").index(level)
    parts[position] += 1
    for index in range(position + 1, 3):
        parts[index] = 0
    updated = ".".join(str(part) for part in parts)
    version_parts(updated)
    return updated


VERSION_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "VERSION"
VERSION = VERSION_PATH.read_text(encoding="utf-8").strip()
version_parts(VERSION)


if __name__ == "__main__":
    arguments = argparse.ArgumentParser(description="管理三段式版本号")
    arguments.add_argument("--bump", choices=("patch", "minor", "major"))
    options = arguments.parse_args()
    if options.bump:
        updated = next_version(VERSION, options.bump)
        VERSION_PATH.write_text(updated + "\n", encoding="utf-8")
        print(updated)
    else:
        print(VERSION)
