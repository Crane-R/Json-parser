import tempfile
import unittest
from pathlib import Path

from build import HIDDEN_IMPORTS, publish_bundle
from parser_engine import LAZY_MODULES
from versioning import VERSION, next_version, version_parts


class ReleaseTests(unittest.TestCase):
    def test_version_is_valid(self):
        self.assertEqual(3, len(version_parts(VERSION)))
        for value in ("1.2", "01.2.3", "v1.2.3", "1.2.3.4", "1.2.65536", "1.2.-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_parts(value)

    def test_version_increments(self):
        self.assertEqual("0.0.2", next_version("0.0.1", "patch"))
        self.assertEqual("0.1.0", next_version("0.0.1", "minor"))
        self.assertEqual("1.0.0", next_version("0.0.1", "major"))

    def test_lazy_parser_modules_are_declared_to_the_packager(self):
        # 动态 importlib 导入不会被静态分析发现，漏一个打包后的程序就会在运行时炸
        self.assertEqual(set(LAZY_MODULES), set(HIDDEN_IMPORTS) & set(LAZY_MODULES))
        for name in LAZY_MODULES:
            with self.subTest(module=name):
                self.assertIn(name, HIDDEN_IMPORTS)

    def test_publish_preserves_previous_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "JSON万能解析器-0.0.2"
            source.mkdir()
            (source / "JSON万能解析器-0.0.2.exe").write_bytes(b"new executable")
            output = root / "output"
            output.mkdir()
            old = output / "JSON万能解析器-0.0.1"
            old.mkdir()
            (old / "JSON万能解析器-0.0.1.exe").write_bytes(b"old executable")
            published = publish_bundle(source, output)
            self.assertEqual({source.name, old.name}, {entry.name for entry in output.iterdir()})
            self.assertEqual(b"new executable", (published / "JSON万能解析器-0.0.2.exe").read_bytes())
            self.assertEqual(b"old executable", (old / "JSON万能解析器-0.0.1.exe").read_bytes())
            publish_bundle(source, output)
            self.assertEqual(2, len(list(output.iterdir())))

    def test_unknown_user_files_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "JSON万能解析器-0.0.1"
            source.mkdir()
            (source / "JSON万能解析器-0.0.1.exe").write_bytes(b"executable")
            output = root / "output"
            output.mkdir()
            user_file = output / "my-data.txt"
            user_file.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                publish_bundle(source, output)
            self.assertEqual("keep", user_file.read_text(encoding="utf-8"))
            self.assertFalse((output / source.name).exists())
