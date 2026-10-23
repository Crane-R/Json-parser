import tempfile
import tkinter as tk
import unittest
from pathlib import Path

import settings
from document_tabs import DocumentTabs
from main import Application, create_root
from settings import Settings, clamp_geometry, default_geometry, minimum_size


class StubRoot:
    """只提供 settings 需要的几何接口，避免为纯逻辑测试开 Tk 窗口。"""

    def __init__(self, width=3840, height=2160):
        self._width, self._height = width, height

    def winfo_screenwidth(self):
        return self._width

    def winfo_screenheight(self):
        return self._height


class GeometryTests(unittest.TestCase):
    def test_parse_geometry_rejects_garbage(self):
        self.assertIsNone(settings.parse_geometry("not-a-geometry"))
        self.assertIsNone(settings.parse_geometry(None))
        self.assertEqual((800, 600, 10, 20), settings.parse_geometry("800x600+10+20"))

    def test_clamp_geometry_keeps_window_on_screen(self):
        root = StubRoot()
        self.assertEqual("1320x900+100+50", clamp_geometry(root, "1320x900+100+50"))
        # 跑到屏幕外的窗口要被拉回来
        moved = clamp_geometry(root, "1320x900+90000+90000")
        self.assertIsNotNone(moved)
        width, height, left, top = settings.parse_geometry(moved)
        self.assertLess(left, root.winfo_screenwidth())
        self.assertLessEqual(top, root.winfo_screenheight())

    def test_default_geometry_fits_screen_and_scales_with_dpi(self):
        class ScaledRoot(StubRoot):
            def tk(self):
                return self

            def call(self, command, argument):
                return "2.0" if argument == "scaling" else ""

        geometry = settings.parse_geometry(default_geometry(ScaledRoot()))
        self.assertIsNotNone(geometry)
        width, height, left, top = geometry
        self.assertLessEqual(width, 3840)
        self.assertLessEqual(height, 2160)
        self.assertGreaterEqual(width, 1320)  # 200% DPI 下不应再开出 1320 的小窗口
        self.assertEqual(2, len(minimum_size(ScaledRoot())))


class SettingsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        original = settings.config_directory
        settings.config_directory = lambda: Path(self.directory.name)
        self.addCleanup(lambda: setattr(settings, "config_directory", original))

    def test_settings_round_trip(self):
        first = Settings()
        first.set("window", {"geometry": "1200x800+30+40", "zoomed": False})
        first.save()
        self.assertTrue(first.path.exists())
        second = Settings()
        self.assertEqual({"geometry": "1200x800+30+40", "zoomed": False}, second.get("window"))

    def test_corrupted_settings_file_is_ignored(self):
        Settings().path.write_text("{ not json", encoding="utf-8")
        self.assertEqual({}, Settings().data)

    def test_window_size_is_remembered_between_sessions(self):
        # 窗口必须真正映射过，Tk 才会回读真实几何；用 alpha=0 保持不可见
        def visible_root():
            root = create_root()
            root.wm_attributes("-alpha", 0.0)
            root.deiconify()

            def discard():
                try:
                    root.destroy()
                except tk.TclError:
                    pass

            self.addCleanup(discard)
            return root

        first_root = visible_root()
        first = DocumentTabs(first_root, Application)
        first_root.geometry("1500x950+120+80")
        first_root.update()
        saved_geometry = first_root.geometry()
        self.assertEqual("1500x950", saved_geometry.split("+")[0])
        first.close()

        self.assertEqual(saved_geometry, Settings().get("window")["geometry"])

        second_root = visible_root()
        DocumentTabs(second_root, Application)
        second_root.update()
        self.assertEqual(clamp_geometry(second_root, saved_geometry), second_root.geometry())


class TabOverflowTests(unittest.TestCase):
    def test_many_tabs_stay_reachable(self):
        root = create_root(hidden=True)
        tabs = DocumentTabs(root, Application, remember=False)
        try:
            for unused in range(14):
                tabs.new_document()
            for index, page in enumerate(tabs.documents):
                page.set_input('{"index":%d}' % index)
                tabs.rename_document(page, f"2026年度业务数据导出-{index}.json")
            root.update_idletasks()
            self.assertEqual(15, len(tabs.documents))

            menu = tabs._fill_overflow()
            for index in range(15):
                self.assertIn(f"2026年度业务数据导出-{index}.json", menu.entrycget(index, "label"))
            self.assertTrue(menu.entrycget(14, "label").startswith("●"))  # 当前标签页有标记
            self.assertEqual(len(tabs.documents) + 1, menu.index("end"))  # 末尾还有分隔线和「关闭其它标签页」
            self.assertEqual("关闭其它标签页", menu.entrycget(menu.index("end"), "label"))

            # 标签总宽超过可视宽度，必须能横向滚动，否则就看不见了
            self.assertGreater(tabs.tab_strip.winfo_reqwidth(), tabs.canvas.winfo_width())

            # 溢出菜单可以直接跳到被挤出去的标签页
            menu.invoke(12)
            self.assertIs(tabs.active_document, tabs.documents[12])
            # 标签上标题过长会截断，但完整名称仍能在菜单里看到
            self.assertTrue(tabs.tab_widgets[tabs.documents[12]][1].cget("text").endswith("…"))
            self.assertIn("2026年度业务数据导出-12.json", tabs._fill_overflow().entrycget(12, "label"))
        finally:
            tabs.close()


if __name__ == "__main__":
    unittest.main()
