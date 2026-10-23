import time
import unittest

from document_tabs import DocumentTabs
from main import Application, create_root
from parser_engine import parse_text


class TabPerformanceTests(unittest.TestCase):
    def test_find_panel_starts_hidden_and_replacement_requires_switching_modes(self):
        root = create_root(hidden=True)
        tabs = DocumentTabs(root, Application, remember=False)
        errors = []
        root.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
        try:
            document = tabs.active_document
            document.show_result(parse_text('{"text":"cat cat CAT"}'))
            self.assertFalse(document.search_panel.winfo_manager())
            document.tabs.select(1)
            self.assertEqual("break", tabs._dispatch("open_search"))
            self.assertEqual("结果", document.search_target.get())
            self.assertEqual(0, document.tabs.active)
            self.assertEqual("查找", document.search_mode.get())
            self.assertEqual("place", document.search_panel.winfo_manager())
            self.assertFalse(document.replace_row.winfo_manager())
            document.search_entry.insert(0, "cat")
            deadline = time.monotonic() + 2
            while document.search_count.get() != "1 / 3" and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            self.assertEqual("1 / 3", document.search_count.get())
            document.find()
            self.assertEqual("2 / 3", document.search_count.get())
            next(widget for widget in document.search_mode.winfo_children()
                 if widget.cget("text") == "替换").invoke()
            self.assertTrue(document.replace_visible)
            self.assertEqual("grid", document.replace_row.winfo_manager())
            document.replace_entry.insert(0, "dog")
            replace_all = next(widget for widget in document.replace_row.winfo_children()
                               if isinstance(widget, type(document.copy_button)) and widget.cget("text") == "全部替换")
            replace_all.invoke()
            self.assertEqual({"text": "dog dog dog"}, document.result.value)
            self.assertEqual("0 / 0", document.search_count.get())
            next(widget for widget in document.search_mode.winfo_children()
                 if widget.cget("text") == "查找").invoke()
            self.assertFalse(document.replace_row.winfo_manager())
            document.open_replace()
            self.assertEqual("替换", document.search_mode.get())
            other = tabs.new_document()
            self.assertFalse(other.search_panel.winfo_manager())
            tabs.select_document(document)
            self.assertEqual("place", document.search_panel.winfo_manager())
            self.assertEqual("替换", document.search_mode.get())
            self.assertEqual("cat", document.search_entry.get())
            self.assertEqual("dog", document.replace_entry.get())
            tabs._dispatch("open_search")
            self.assertFalse(document.replace_visible)
            self.assertFalse(document.replace_row.winfo_manager())
            tabs.shell.pack_forget()
            root.tk.call("place", tabs.shell, "-x", 0, "-y", 0, "-width", 1060, "-height", 800)
            root.update_idletasks()
            self.assertLess(document.search_panel.winfo_width(), document.editor_panels[1].winfo_width() - 8)
            self.assertGreater(document.search_entry.winfo_width(), 100)
            tabs._dispatch("close_search")
            self.assertFalse(document.search_panel.winfo_manager())
            self.assertFalse(document.replace_visible)
            self.assertFalse(document.output.tag_ranges("found"))
            self.assertFalse(document.output.tag_ranges("matches"))
            root.update()
            self.assertFalse(errors, errors)
            self.assertEqual("withdrawn", root.state())
        finally:
            tabs.close()

    def test_shared_controls_follow_active_document_and_survive_original_page_close(self):
        root = create_root(hidden=True)
        tabs = DocumentTabs(root, Application, remember=False)
        try:
            first = tabs.active_document
            first.set_input('{"name":"first"}')
            second = tabs.new_document()
            first.show_result(parse_text('{"name":"first"}'))
            self.assertEqual("disabled", second.copy_button.cget("state"))
            second.set_input('{"name":"second"}')
            second.show_result(parse_text('{"name":"second"}'))
            first.invalidate()
            self.assertEqual("normal", second.copy_button.cget("state"))
            second.search_entry.insert(0, "second")
            self.assertEqual("second", second.search_term.get())
            self.assertEqual("", first.search_term.get())
            tabs.close_document(first)
            clear = next(widget for widget in second.toolbar.winfo_children()
                         if widget.cget("text") == "清空")
            clear.invoke()
            self.assertEqual("", second.source.get("1.0", "end-1c"))
            self.assertEqual("disabled", second.copy_button.cget("state"))
            self.assertEqual("withdrawn", root.state())
        finally:
            tabs.close()

    def test_new_tabs_reuse_controls_without_rescaling_existing_pages(self):
        root = create_root(hidden=True)
        tabs = DocumentTabs(root, Application, remember=False)
        try:
            first = tabs.active_document
            root.update_idletasks()
            timings = []
            scaling = root.tk.call("tk", "scaling")
            for index in range(8):
                started = time.perf_counter()
                document = tabs.new_document()
                root.update_idletasks()
                timings.append(round((time.perf_counter() - started) * 1000, 1))
                self.assertIs(first.toolbar, document.toolbar)
                self.assertIs(first.search_entry, document.search_entry)
                self.assertIsNot(first.source, document.source)
            # 新建标签页不得触发全局缩放重算（会把已有页面一起重排）
            self.assertEqual(scaling, root.tk.call("tk", "scaling"))
            self.assertEqual("withdrawn", root.state())
            self.assertLess(max(timings), 250, timings)
            print("new_tab_ms:", timings)
        finally:
            tabs.close()


if __name__ == "__main__":
    unittest.main()
