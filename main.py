from __future__ import annotations

import argparse
import ctypes
import itertools
import json
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from tkinterdnd2 import TkinterDnD

from auto_parse import AutoParse
from desktop_ui import PREVIEW_LIMIT, ApplicationView
from parser_engine import ParseResult, parse_text, read_text
from text_search import TextSearch
from versioning import VERSION


TITLE = "JSON 万能解析器"
# 超长文本不再逐处标注匹配背景，只保留计数与跳转，避免大文件下卡顿
MATCH_TAG_LIMIT = 2_000_000
DEMO = '<Response>{&quot;code&quot;:0,&quot;data&quot;:{&quot;name&quot;:&quot;你好，JSON&quot;,&quot;items&quot;:[1,2,3]},&quot;ok&quot;:true}</Response>'


class Application(ApplicationView):
    def __init__(self, root, parent=None, owner=None):
        self.root = root
        self.parent = parent if parent is not None else root
        self.owner = owner
        self.closed = False
        self.closed_event = threading.Event()
        self.result: ParseResult | None = None
        self.rendered = ""
        self.revision = 0
        self.messages = queue.Queue()
        self.render_revision = 0
        self.render_messages = queue.Queue()
        self.render_jobs = 0
        self.tree_data = {}
        self.tree_pages = {}
        self.strict = tk.BooleanVar(root)
        self.nested = tk.BooleanVar(root)
        self.compact = tk.BooleanVar(root)
        self.sort_keys = tk.BooleanVar(root)
        self.status = tk.StringVar(root, value="粘贴即解析  ·  拖入文件即可开始")
        self.input_count = tk.StringVar(root, value="0 字符")
        self.output_count = tk.StringVar(root, value="等待输入")
        self.search_term = tk.StringVar(root)
        self.search_target = tk.StringVar(root, value="结果")
        self.search_count = tk.StringVar(root, value="0 / 0")
        self.replacement_term = tk.StringVar(root)
        self.match_case = tk.BooleanVar(root)
        self.search_job = None
        self.search_session = None
        self.search_key = None
        self.search_visible = False
        self.replace_visible = False
        self.auto = AutoParse(root.after, root.after_cancel, self.parse)
        self._build()
        self.search_traces = [(variable, variable.trace_add("write", self._schedule_search))
                              for variable in (self.search_term, self.search_target, self.match_case)]
        # 轮询只在真的有后台任务时运行，空闲标签页不会每 60 ms 空转一次
        self.poll_timer = None
        if owner is None:
            root.protocol("WM_DELETE_WINDOW", self.close)

    @property
    def busy(self):
        return self.auto.busy

    def close(self):
        if self.owner is not None:
            self.owner.close_document(self)
            return
        self.dispose()
        self.root.destroy()

    def dispose(self):
        if self.closed:
            return
        self.closed = True
        self.closed_event.set()
        self.auto.close()
        if self.search_job is not None:
            self.root.after_cancel(self.search_job)
        if self.poll_timer is not None:
            self.root.after_cancel(self.poll_timer)
            self.poll_timer = None
        for variable, trace in self.search_traces:
            variable.trace_remove("write", trace)
        self.search_traces.clear()
        self.result = None
        self.rendered = ""
        self.search_session = None
        self.search_key = None
        self.tree_data.clear()
        self.tree_pages.clear()
        self.messages = queue.Queue()
        self.render_messages = queue.Queue()
        self.shell.destroy()

    def _sync_result_actions(self):
        if self.owner is None or self.owner.active_document is self:
            state = "normal" if self.result is not None else "disabled"
            self.copy_button.configure(state=state)
            self.save_button.configure(state=state)

    def _select_all(self, editor):
        editor.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _write(self, editor, text):
        editor.configure(state="normal")
        editor.delete("1.0", "end")
        editor.insert("1.0", text)
        editor.configure(state="disabled")

    def _modified(self, event=None):
        if self.source.edit_modified():
            self.source.edit_modified(False)
            count = self.source.count("1.0", "end-1c", "chars")
            self.input_count.set(f"{count[0] if count else 0:,} 字符")
            self.invalidate()

    def invalidate(self):
        self.revision += 1
        self.result = None
        self.rendered = ""
        self._sync_result_actions()
        self._write(self.output, "")
        self._write(self.info, "等待自动解析…")
        self.tree.delete(*self.tree.get_children())
        self.tree_data.clear()
        self.tree_pages.clear()
        self.tabs.tab(2, text="处理说明")
        active = bool(self.source.get("1.0", "end-1c").strip())
        self.output_count.set("等待解析" if active else "等待输入")
        self.status.set("输入已更新，正在准备解析…" if active else "粘贴即解析  ·  拖入文件即可开始")
        self._sync_hints()
        self.auto.request(active)
        self._start_poll()
        self._schedule_search()

    def set_input(self, text):
        self.source.delete("1.0", "end")
        self.source.insert("1.0", text)
        self._modified()

    def clear(self):
        self.set_input("")
        self.status.set("已清空  ·  粘贴内容或拖入文件开始")

    def paste(self):
        try:
            self.set_input(self.root.clipboard_get())
            self.parse()
        except tk.TclError:
            self.status.set("剪贴板里没有可用的文本")

    def open_file(self, path=None):
        path = path or filedialog.askopenfilename(title="打开文本文件", filetypes=[("文本与数据", "*.json *.txt *.xml *.csv *.tsv *.yaml *.yml *.log *.jsonl"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            text, encoding = read_text(path)
            self.set_input(text)
            self.status.set(f"{Path(path).name}  ·  {encoding}")
            if self.owner is not None:
                self.owner.rename_document(self, Path(path).name)
            self.parse()
        except (OSError, ValueError) as error:
            self.status.set("打开失败：" + str(error))

    def _drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        if self.owner is not None:
            self.owner.open_files(paths)
        elif len(paths) == 1:
            self.open_file(paths[0])
        elif paths:
            self.status.set("请一次拖入一个文件，文件内可包含多段数据")
        return event.action

    def parse(self):
        if self.closed:
            return
        self._modified()
        source = self.source.get("1.0", "end-1c")
        if not source.strip():
            self.auto.request(False)
            return
        if not self.auto.begin():
            return
        revision = self.revision
        strict, nested = self.strict.get(), self.nested.get()
        compact, sort_keys = self.compact.get(), self.sort_keys.get()
        messages, closed_event = self.messages, self.closed_event
        self.status.set("正在识别格式并解析…")
        self._start_poll()

        def worker():
            started = time.perf_counter()
            try:
                result = parse_text(source, strict, nested)
                # 序列化是大结果里最重的一步，放在后台线程，主线程只负责写控件
                rendered = result.render(compact, sort_keys)
                if not closed_event.is_set():
                    messages.put((revision, result, rendered, time.perf_counter() - started, None))
            except Exception as error:
                if not closed_event.is_set():
                    messages.put((revision, None, None, 0, str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _start_poll(self):
        if self.closed or self.poll_timer is not None:
            return
        self.poll_timer = self.root.after(30, self._poll)

    def _poll(self):
        if self.closed:
            return
        self.poll_timer = None
        try:
            if self._drain_render():
                self.auto.complete()
            revision, result, rendered, elapsed, error = self.messages.get_nowait()
            if revision == self.revision:
                if error:
                    self._write(self.info, "解析未完成\n\n" + error)
                    self.tabs.select(2)
                    self.output_count.set("解析未完成")
                    self.status.set("解析未完成，请查看处理说明")
                else:
                    self.show_result(result, elapsed, rendered)
            self.auto.complete()
        except queue.Empty:
            pass
        if not self.closed and (self.auto.busy or self.auto.pending or self.render_jobs
                                or not self.messages.empty() or not self.render_messages.empty()):
            self.poll_timer = self.root.after(30, self._poll)

    def show_result(self, result, elapsed=0, rendered=None):
        self.result = result
        if rendered is None:
            self.reformat()
        else:
            self.rendered = rendered
            self._apply_rendered()
        summary = f"识别格式：{result.format}\n耗时：{elapsed:.3f} 秒\n\n处理步骤\n"
        summary += "\n".join(f"{index}. {step}" for index, step in enumerate(result.steps, 1))
        summary += "\n\n注意事项\n" + ("\n".join("• " + warning for warning in result.warnings) or "无需修复，未发现兼容性转换提示。")
        self._write(self.info, summary)
        self.tabs.tab(2, text=f"处理说明 ({len(result.warnings)})" if result.warnings else "处理说明")
        self.tree.delete(*self.tree.get_children())
        self.tree_data.clear()
        self.tree_pages.clear()
        root_id = self._add_node("", "$", result.value, "$")
        self.tree.item(root_id, open=True)
        self._load_children(root_id)
        self._sync_result_actions()
        self.status.set(f"{result.format}  ·  {elapsed:.3f} 秒  ·  " + (f"{len(result.warnings)} 条提示，见处理说明" if result.warnings else "解析完成"))
        self._sync_hints()

    def reformat(self):
        if self.result is None:
            return
        # 小结果就地重排，省掉一次线程往返；大结果必须放后台，否则切「紧凑」
        # 这类开关会让界面卡住几秒。
        if len(self.rendered) <= PREVIEW_LIMIT:
            self.rendered = self.result.render(self.compact.get(), self.sort_keys.get())
            self._apply_rendered()
            return
        self.render_revision += 1
        self.render_jobs += 1
        revision = self.render_revision
        compact, sort_keys = self.compact.get(), self.sort_keys.get()
        result, messages, closed_event = self.result, self.render_messages, self.closed_event
        self.status.set("正在重新格式化…")
        self._start_poll()

        def worker():
            try:
                rendered = result.render(compact, sort_keys)
                if not closed_event.is_set():
                    messages.put((revision, rendered, None))
            except Exception as error:
                if not closed_event.is_set():
                    messages.put((revision, None, str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_render(self):
        try:
            revision, rendered, error = self.render_messages.get_nowait()
        except queue.Empty:
            return False
        self.render_jobs = max(0, self.render_jobs - 1)
        if revision != self.render_revision:
            return True
        if error:
            self.status.set("重新格式化失败：" + error)
        else:
            self.rendered = rendered
            self._apply_rendered()
            self.status.set("已重新格式化")
        return True

    def _apply_rendered(self):
        text = self.rendered
        if len(text) > PREVIEW_LIMIT:
            shown = text[:PREVIEW_LIMIT] + (
                f"\n\n… 结果共 {len(text):,} 字符（约 {len(text) / 1048576:.1f} MB），"
                f"为保持界面流畅，预览只显示前 {PREVIEW_LIMIT // 1048576} MB。"
                "完整内容请点「导出 JSON」保存。")
        else:
            shown = text
        self._write(self.output, shown)
        self._highlight(shown)
        self.output_count.set(f"{len(text):,} 字符" +
                              ("（预览已截断）" if len(text) > PREVIEW_LIMIT else ""))
        self._schedule_search()

    def _add_node(self, parent, label, value, path):
        typename = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "null" if value is None else "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "string"
        preview = f"{len(value):,} 项" if isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False)[:160]
        node = self.tree.insert(parent, "end", text=label, values=(typename, preview))
        self.tree_data[node] = (value, path)
        if isinstance(value, (dict, list)) and value:
            self.tree.insert(node, "end", text="加载中…", tags=("placeholder",))
        return node

    def _load_children(self, node, offset=0):
        if node not in self.tree_data:
            return
        value, path = self.tree_data[node]
        if not isinstance(value, (dict, list)):
            return
        if offset == 0:
            for child in self.tree.get_children(node):
                if "placeholder" in self.tree.item(child, "tags"):
                    self.tree.delete(child)
                else:
                    return
        children = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in itertools.islice(children, offset, offset + 250):
            suffix = "[" + (json.dumps(key, ensure_ascii=False) if isinstance(value, dict) else str(key)) + "]"
            self._add_node(node, str(key), child, path + suffix)
        if len(value) > offset + 250:
            more = self.tree.insert(node, "end", text=f"双击加载更多（剩余 {len(value) - offset - 250:,} 项）")
            self.tree_pages[more] = (node, offset + 250)

    def _expand_tree(self, event=None):
        self._load_children(self.tree.focus())

    def _more_tree(self, event):
        node = self.tree.identify_row(event.y)
        if node in self.tree_pages:
            parent, offset = self.tree_pages.pop(node)
            self.tree.delete(node)
            self._load_children(parent, offset)
            return "break"

    def copy_node(self, path_only=False):
        node = self.tree.focus()
        if node in self.tree_data:
            value, path = self.tree_data[node]
            self._clipboard(path if path_only else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))

    def _clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.status.set("已复制到剪贴板")

    def copy(self):
        if self.result is None:
            return
        # 超大结果塞进剪贴板会长时间卡住，直接提示改用导出
        if len(self.rendered) > PREVIEW_LIMIT:
            self.status.set(f"结果过大（{len(self.rendered):,} 字符），请用「导出 JSON」保存")
            return
        self._clipboard(self.rendered)

    def save(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(title="导出标准 JSON", defaultextension=".json", initialfile="解析结果.json", filetypes=[("JSON 文件", "*.json")])
        if path:
            try:
                Path(path).write_text(self.rendered + "\n", encoding="utf-8")
                self.status.set(f"已保存 {Path(path).name}  ·  UTF-8")
            except OSError as error:
                self.status.set("保存失败：" + str(error))

    def open_search(self, replacement=False, target=None):
        focus = self.root.focus_get()
        if target is None:
            if focus is self.source:
                target = "输入"
            elif focus is self.output or not self.search_visible:
                target = "结果"
            else:
                target = self.search_target.get()
        self.search_target.set(target)
        self.search_visible = True
        self.replace_visible = replacement
        if target == "结果":
            self.tabs.select(0)
        self._sync_search_panel()
        self.search_key = None
        self._refresh_search()
        entry = self.replace_entry if replacement else self.search_entry
        entry.focus_set()
        entry.select_range(0, "end")
        return "break"

    def open_replace(self):
        return self.open_search(replacement=True)

    def set_search_mode(self, mode):
        self.replace_visible = mode == "替换"
        self._sync_search_panel()
        (self.replace_entry if self.replace_visible else self.search_entry).focus_set()

    def close_search(self):
        if self.search_visible:
            self.search_visible = False
            self.replace_visible = False
            self._sync_search_panel()
            for editor in (self.source, self.output):
                editor.tag_remove("matches", "1.0", "end")
                editor.tag_remove("found", "1.0", "end")
            (self.output if self.search_target.get() == "结果" else self.source).focus_set()
        return "break"

    def _sync_search_panel(self):
        if self.owner is not None and self.owner.active_document is not self:
            return
        if not self.search_visible:
            self.search_panel.place_forget()
            return
        self.search_mode.set("替换" if self.replace_visible else "查找")
        self.replace_row.grid() if self.replace_visible else self.replace_row.grid_remove()
        panel = self.editor_panels[1 if self.search_target.get() == "结果" else 0]
        self.search_panel.place(in_=panel, relx=1, x=-6, y=27, anchor="ne")
        self.search_panel.lift()

    def _schedule_search(self, *unused):
        if self.closed:
            return
        self._sync_search_panel()
        if self.search_job is not None:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(180, self._refresh_search)

    def _refresh_search(self):
        if self.search_job is not None:
            self.root.after_cancel(self.search_job)
            self.search_job = None
        editor = self.output if self.search_target.get() == "结果" else self.source
        text = editor.get("1.0", "end-1c")
        key = (self.search_target.get(), self.search_term.get(), self.match_case.get(), text)
        fresh = key != self.search_key
        if fresh:
            self.search_key = key
            self.search_session = TextSearch(text, key[1], key[2])
            for candidate in (self.source, self.output):
                candidate.tag_remove("found", "1.0", "end")
                candidate.tag_remove("matches", "1.0", "end")
            ranges = []
            if self.search_visible and len(text) <= MATCH_TAG_LIMIT:
                for match in self.search_session.ranges():
                    ranges.extend((self.search_session.tk_index(match.start),
                                   self.search_session.tk_index(match.end)))
            for offset in range(0, len(ranges), 1000):
                editor.tag_add("matches", *ranges[offset:offset + 1000])
            self.search_session.next()
        self._show_match(editor, scroll=False)
        return editor, fresh

    def _show_match(self, editor, scroll=True):
        session = self.search_session
        editor.tag_remove("found", "1.0", "end")
        if session.current and self.search_visible:
            start = session.tk_index(session.current.start)
            end = session.tk_index(session.current.end)
            editor.tag_add("found", start, end)
            editor.tag_raise("matches")
            editor.tag_raise("found")
            if scroll:
                editor.see(start)
        self.search_count.set(f"{session.ordinal} / {session.total}")

    def find(self, previous=False):
        editor, fresh = self._refresh_search()
        if not fresh or previous:
            self.search_session.previous() if previous else self.search_session.next()
        if editor is self.output:
            self.tabs.select(0)
        self._show_match(editor)
        if not self.search_session.total and self.search_term.get():
            self.status.set("未找到匹配内容")

    def replace(self, all_matches=False):
        editor, unused = self._refresh_search()
        session = self.search_session
        if not session.total:
            self.status.set("没有可替换的匹配内容")
            return
        replacement = self.replacement_term.get()
        candidate, count = session.replacement(replacement, all_matches)
        next_offset = session.current.start + len(replacement)
        if editor is self.source:
            editor.edit_separator()
            editor.replace("1.0", "end-1c", candidate)
            editor.edit_separator()
            self._modified()
        else:
            if self.busy or self.result is None:
                self.status.set("请等待解析完成后替换结果")
                return
            try:
                parsed = parse_text(candidate, strict=True)
            except ValueError as error:
                self._write(self.info, "替换未应用，原结果已保留。\n\n替换后的内容不是有效的标准 JSON：\n" + str(error))
                self.tabs.select(2)
                self.status.set("替换未应用，请查看处理说明")
                return
            self.show_result(ParseResult(parsed.value, self.result.format,
                                         self.result.steps + [f"在解析结果中替换 {count} 处文本"],
                                         self.result.warnings + parsed.warnings))
        editor, unused = self._refresh_search()
        if not all_matches:
            self.search_session.select_after(next_offset)
        self._show_match(editor)
        self.status.set(f"已替换 {count} 处" + ("，自动解析中…" if editor is self.source else ""))


def configure_windows():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Local.JsonUniversal.1")
        except (AttributeError, OSError):
            pass


_CLASS_BRUSH = None


def _set_class_background(window):
    """把窗口类背景刷设成界面底色。

    Tk 的窗口类没有背景刷，最大化 / 拉伸时新暴露的区域要等 Tk 重绘才有内容，
    期间显示为黑色——这就是最大化瞬间「黑窗口」的来源。先刷底色就能消掉。"""
    global _CLASS_BRUSH
    from desktop_ui import BACKGROUND
    value = (int(BACKGROUND[1:3], 16) | int(BACKGROUND[3:5], 16) << 8
             | int(BACKGROUND[5:7], 16) << 16)
    if _CLASS_BRUSH is None:
        _CLASS_BRUSH = ctypes.windll.gdi32.CreateSolidBrush(value)
    setter = ctypes.windll.user32.SetClassLongPtrW
    setter.restype = ctypes.c_void_p
    setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    setter(window, -10, _CLASS_BRUSH)  # GCLP_HBRBACKGROUND


def create_root(hidden=False):
    configure_windows()
    root = TkinterDnD.Tk()
    root.withdraw()
    if hidden:
        root.wm_attributes("-alpha", 0)
    if sys.platform == "win32":
        # 关掉 Win11 自动加深的标题栏 + 设置类背景刷（消除最大化黑闪）。
        # 实测：withdrawn 状态下 Tk 的 wrapper 窗口并不存在，GetParent 返回 0，
        # 此时 SetClassLongPtrW 会静默失败——必须等窗口真正 Map 之后才能设置。
        # 因此挂 <Map> 事件（此时 wrapper 必然已创建），after_idle 仅作已显示场景的兜底。
        state = {"map_bound": True}

        def polish_window(event=None):
            try:
                window = ctypes.windll.user32.GetParent(root.winfo_id())
                if not window:
                    return  # wrapper 尚未创建，等下一次 Map 事件再试
                ctypes.windll.dwmapi.DwmSetWindowAttribute(window, 20, ctypes.byref(ctypes.c_int(0)), 4)
                _set_class_background(window)
                if state["map_bound"] and root.bind("<Map>"):
                    root.unbind("<Map>", map_binding)
                    state["map_bound"] = False
            except (AttributeError, OSError):
                pass

        map_binding = root.bind("<Map>", polish_window, add=True)
        root.after_idle(polish_window)
    return root


def self_test(report_path):
    from document_tabs import DocumentTabs
    root = create_root(hidden=True)
    documents = DocumentTabs(root, Application, remember=False)
    app = documents.active_document
    callback_errors = []
    report = {}
    root.report_callback_exception = lambda kind, error, trace: callback_errors.append(str(error))

    def wait_until(predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        assert predicate(), "后台自检等待超时"
        assert not callback_errors, callback_errors
        assert root.state() == "withdrawn", "自检窗口必须保持隐藏"

    try:
        app.source.insert("1.0", DEMO)
        wait_until(lambda: app.result is not None)
        assert app.result.value["data"]["name"] == "你好，JSON"
        assert json.loads(app.output.get("1.0", "end"))["ok"] is True
        assert app.output.tag_ranges("key")
        app.source.delete("1.0", "end")
        app.source.insert("1.0", '{"edited":1}')
        wait_until(lambda: app.result is not None and app.result.value == {"edited": 1})
        app.set_input(DEMO)
        app.parse()
        app.set_input('{"latest":true}')
        wait_until(lambda: app.result is not None and app.result.value == {"latest": True})
        app.clear()
        wait_until(lambda: app.result is None and not app.busy and not app.auto.pending)
        assert app.input_count.get() == "0 字符"
        assert not app.output.get("1.0", "end").strip()
        app.set_input("{a:1}")
        wait_until(lambda: app.result is not None)
        app.strict.set(True)
        app.invalidate()
        wait_until(lambda: not app.busy and not app.auto.pending and app.auto.timer is None)
        assert app.result is None
        assert "标准 JSON" in app.info.get("1.0", "end")
        app.strict.set(False)
        app.invalidate()
        wait_until(lambda: app.result is not None)
        app.auto.close()
        app.show_result(parse_text(json.dumps(list(range(600)))))
        tree_root = app.tree.get_children()[0]
        assert len(app.tree.get_children(tree_root)) == 251
        more_node = next(iter(app.tree_pages))
        parent_node, page_offset = app.tree_pages.pop(more_node)
        app.tree.delete(more_node)
        app._load_children(parent_node, page_offset)
        assert len(app.tree.get_children(tree_root)) == 501
        app.tabs.select(1)
        assert app.tabs.active == 1
        assert not app.search_visible and not app.search_panel.winfo_manager()
        assert documents._dispatch("open_search") == "break"
        assert app.search_visible and not app.replace_visible and app.tabs.active == 0
        assert app.search_panel.winfo_manager() == "place"
        assert not app.replace_row.winfo_manager()
        app.search_term.set("120")
        app.find()
        assert app.output.tag_ranges("found")
        next(widget for widget in app.search_mode.winfo_children() if widget.cget("text") == "替换").invoke()
        assert app.replace_visible and app.replace_row.winfo_manager() == "grid"
        next(widget for widget in app.search_mode.winfo_children() if widget.cget("text") == "查找").invoke()
        assert not app.replace_visible and not app.replace_row.winfo_manager()
        assert documents._dispatch("close_search") == "break"
        assert not app.search_visible and not app.search_panel.winfo_manager()
        assert not app.output.tag_ranges("found")
        assert documents._dispatch("open_replace") == "break"
        assert app.search_visible and app.replace_visible
        app.auto.close()
        app.set_input('{"text":"猫 猫 CAT cat 😀中文"}')
        wait_until(lambda: app.result is not None and not app.busy)
        app.search_target.set("输入")
        app.search_term.set("猫")
        app._refresh_search()
        assert app.search_count.get() == "1 / 2"
        app.find()
        assert app.search_count.get() == "2 / 2"
        app.find(previous=True)
        assert app.search_count.get() == "1 / 2"
        app.replacement_term.set("狗")
        app.replace()
        wait_until(lambda: app.result is not None and app.result.value["text"].startswith("狗 猫"))
        app.source.edit_undo()
        wait_until(lambda: app.result is not None and app.result.value["text"].startswith("猫 猫"))
        app.replace()
        wait_until(lambda: app.result is not None and app.result.value["text"].startswith("狗 猫"))
        app.replacement_term.set("鸟")
        app.replace(all_matches=True)
        wait_until(lambda: app.result is not None and app.result.value["text"].startswith("狗 鸟"))
        app.search_target.set("结果")
        app.search_term.set("cat")
        app._refresh_search()
        assert app.search_count.get() == "1 / 2"
        app.match_case.set(True)
        app._refresh_search()
        assert app.search_count.get() == "1 / 1"
        app.match_case.set(False)
        app.replacement_term.set("小猫")
        app.replace(all_matches=True)
        assert app.result.value["text"] == "狗 鸟 小猫 小猫 😀中文"
        assert json.loads(app.rendered) == app.result.value
        app.search_term.set("小猫")
        app.replacement_term.set("")
        app.replace(all_matches=True)
        assert app.result.value["text"] == "狗 鸟   😀中文"
        app.search_term.set("中文")
        app._refresh_search()
        assert app.output.get(*app.output.tag_ranges("found")) == "中文"
        app.search_term.set('"')
        app.replacement_term.set("")
        before = app.rendered
        app.replace(all_matches=True)
        assert app.rendered == before
        assert "替换未应用" in app.info.get("1.0", "end")
        app.search_term.set("")
        app._refresh_search()
        assert app.search_count.get() == "0 / 0"
        app.tabs.select(0)
        documents.shell.pack_forget()
        area_ratios = {}
        for width, height in ((1060, 800), (1320, 900), (1600, 1000)):
            tk.Place.place_configure(documents.shell, x=0, y=0, width=width, height=height)
            root.update_idletasks()
            area = sum(editor.winfo_width() * editor.winfo_height() for editor in (app.source, app.output))
            # 与内容行（去掉标签栏 / 工具栏后剩下的区域）比较，衡量编辑器是否被边框吃掉
            content = documents.pages.winfo_width() * documents.pages.winfo_height()
            ratio = area / max(content, 1)
            area_ratios[f"{width}x{height}"] = round(ratio, 4)
            assert ratio >= .85, area_ratios
        assert not callback_errors, callback_errors
        report = {"ok": True, "version": VERSION, "frozen": bool(getattr(sys, "frozen", False)),
                  "window_hidden": root.state() == "withdrawn", "gui": True,
                  "drag_drop": root.tk.call("package", "present", "tkdnd"),
                  "automatic_parse": True, "automatic_reparse": True, "latest_input_wins": True,
                  "clear_input": True, "options_reparse": True, "syntax_highlight": True,
                  "tree_pagination": True, "search": True, "search_count": True,
                  "replace_input": True, "replace_output": True, "invalid_replacement_preserved": True,
                  "unicode_search": True, "replace_undo": True, "empty_replacement": True,
                  "on_demand_find_panel": True, "find_replace_mode_switch": True,
                  "escape_closes_find_panel": True, "replacement_hidden_by_default": True,
                  "editor_area_ratios": area_ratios, "callback_errors": callback_errors}
        documents.close_document(app)
        first = documents.active_document
        assert len(documents.documents) == 1
        for widget in (first.source, first.output, first.search_entry, first.replace_entry):
            assert widget.bindtags().index(str(root)) < widget.bindtags().index(widget.winfo_class())
        assert not first.source.get("1.0", "end-1c")
        malformed_response = 'Respon1&quot;data&quot;:{&quot;records&quot;:[{&quot;flowId&quot;:2438}],&quot;total&quot;:15},&quot;ok&quot;:true}\\</Response>'
        first.set_input(malformed_response)
        wait_until(lambda: first.result is not None)
        assert first.result.value == {"data": {"records": [{"flowId": 2438}], "total": 15}, "ok": True}
        assert first.result.warnings
        report["damaged_response_recovery"] = True
        first.set_input('{"name":"first first"}')
        first.search_term.set("first")
        first.replacement_term.set("updated")
        first.search_target.set("输入")
        first.match_case.set(True)
        first.compact.set(True)
        second = documents.new_document()
        second.set_input('{"name":"second"}')
        second.strict.set(True)
        second.invalidate()
        third = documents.new_document()
        third.set_input(DEMO)
        wait_until(lambda: all(page.result is not None for page in (first, second, third)))
        assert first.result.value == {"name": "first first"}
        assert second.result.value == {"name": "second"}
        assert third.result.value["data"]["name"] == "你好，JSON"
        documents.select_document(first)
        first._refresh_search()
        assert first.search_count.get() == "1 / 2"
        first.find()
        assert first.search_count.get() == "2 / 2"
        first.tabs.select(1)
        documents.select_document(second)
        assert second.strict.get() and not second.compact.get()
        assert not second.search_term.get() and not second.replacement_term.get()
        first.set_input('{"name":"first first","background":true}')
        wait_until(lambda: first.result is not None and first.result.value.get("background"))
        assert documents.active_document is second
        assert second.result.value == {"name": "second"}
        documents.select_document(first)
        assert first.compact.get() and first.match_case.get() and not first.strict.get()
        assert first.replacement_term.get() == "updated"
        assert first.tabs.active == 1
        first.replace(all_matches=True)
        wait_until(lambda: first.result is not None and first.result.value["name"] == "updated updated")
        first.source.edit_undo()
        wait_until(lambda: first.result is not None and first.result.value["name"] == "first first")
        assert second.result.value == {"name": "second"}
        assert documents._dispatch("open_search") == "break"
        documents.next_document()
        assert documents.active_document is second
        documents.next_document(-1)
        assert documents.active_document is first
        documents.close_document(second)
        assert second.closed and second.result is None and not second.source.winfo_exists()
        assert documents.active_document is first
        assert second not in documents.titles and second not in documents.tab_widgets
        busy = documents.new_document()
        busy.set_input('{"closing":"busy"}')
        busy.parse()
        assert busy.busy
        documents.close_current()
        assert busy.closed and busy.auto.timer is None and not busy.auto.pending
        assert not busy.source.winfo_exists()
        assert documents.active_document is third
        documents.close_document(third)
        assert documents.active_document is first
        documents.close_current()
        blank = documents.active_document
        assert len(documents.documents) == 1 and blank is not first
        assert blank.result is None and not blank.source.get("1.0", "end-1c")
        assert not blank.search_term.get() and not blank.replacement_term.get()
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / "第一份.json", Path(directory) / "第二份.json"]
            for index, path in enumerate(paths):
                path.write_text(json.dumps({"file": index}), encoding="utf-8")
            documents.open_files(paths)
            imported = documents.documents[:]
            wait_until(lambda: all(page.result is not None for page in imported))
            assert [page.result.value for page in imported] == [{"file": 0}, {"file": 1}]
            assert [documents.titles[page] for page in imported] == [path.name for path in paths]
        documents.shell.pack_forget()
        tab_area_ratios = {}
        active = documents.active_document
        for width, height in ((1060, 800), (1320, 900), (1600, 1000)):
            tk.Place.place_configure(documents.shell, x=0, y=0, width=width, height=height)
            root.update_idletasks()
            area = sum(editor.winfo_width() * editor.winfo_height() for editor in (active.source, active.output))
            content = documents.pages.winfo_width() * documents.pages.winfo_height()
            tab_area_ratios[f"{width}x{height}"] = round(area / max(content, 1), 4)
            assert area / max(content, 1) >= .85, tab_area_ratios
            assert active.save_button.winfo_x() + active.save_button.winfo_width() <= active.toolbar.winfo_width()
        wait_until(lambda: not active.busy and active.search_job is None)
        assert not callback_errors, callback_errors
        report.update(document_tabs=True, independent_documents=True, background_documents_parse=True,
                      tab_search_replace_undo=True, close_busy_tab=True, close_last_tab_resets=True,
                      multi_file_import=True, tab_editor_area_ratios=tab_area_ratios)
        new_tab_times = []
        shared_toolbar, shared_search = active.toolbar, active.search_entry
        for unused in range(8):
            started = time.perf_counter()
            page = documents.new_document()
            root.update_idletasks()
            new_tab_times.append(round((time.perf_counter() - started) * 1000, 1))
            assert page.toolbar is shared_toolbar and page.search_entry is shared_search
        report["new_tab_ms"] = new_tab_times
        report["shared_document_controls"] = True
        assert not callback_errors, callback_errors
    finally:
        if documents is not None:
            remaining = documents.documents[:]
            documents.close()
            assert not documents.documents and not documents.titles and not documents.tab_widgets
            assert documents.active_document is None
            assert all(page.closed and page.result is None and not page.rendered for page in remaining)
            report["exit_destroys_all_tabs"] = True
        else:
            app.close()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    arguments = argparse.ArgumentParser(description=f"{TITLE} v{VERSION}")
    arguments.add_argument("file", nargs="?", help="启动时打开的文件")
    arguments.add_argument("--input", help="命令行输入文件，与 --output 同时使用")
    arguments.add_argument("--output", help="命令行输出 JSON 文件")
    arguments.add_argument("--report", help="命令行处理报告 JSON 文件")
    arguments.add_argument("--strict", action="store_true")
    arguments.add_argument("--decode-nested", action="store_true")
    arguments.add_argument("--compact", action="store_true")
    arguments.add_argument("--self-test", metavar="REPORT", help="后台自检，窗口始终隐藏")
    options = arguments.parse_args()
    try:
        if options.self_test:
            self_test(options.self_test)
            return 0
        if options.input or options.output:
            if not options.input or not options.output:
                raise ValueError("命令行转换需要同时指定 --input 和 --output。")
            if Path(options.input).resolve() == Path(options.output).resolve():
                raise ValueError("输出路径不能与输入文件相同。")
            if options.report and Path(options.report).resolve() in (Path(options.input).resolve(), Path(options.output).resolve()):
                raise ValueError("报告路径不能与输入或输出文件相同。")
            source, encoding = read_text(options.input)
            result = parse_text(source, options.strict, options.decode_nested)
            Path(options.output).write_text(result.render(options.compact) + "\n", encoding="utf-8")
            if options.report:
                Path(options.report).write_text(json.dumps({"ok": True, "version": VERSION, "format": result.format,
                                                           "encoding": encoding, "steps": result.steps,
                                                           "warnings": result.warnings}, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0
        root = create_root()
        from document_tabs import DocumentTabs
        app = DocumentTabs(root, Application)
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "app.ico"
        if icon.exists():
            root.iconbitmap(str(icon))
        app.active_document.strict.set(options.strict)
        app.active_document.nested.set(options.decode_nested)
        app.active_document.compact.set(options.compact)
        root.deiconify()
        if options.file:
            root.after(150, lambda: app.open_files([options.file]))
        root.mainloop()
        return 0
    except Exception as error:
        error_text = str(error)
        if options.self_test:
            Path(options.self_test).write_text(json.dumps({"ok": False, "version": VERSION, "error": error_text}, ensure_ascii=False), encoding="utf-8")
        elif options.report and Path(options.report).resolve() not in {Path(path).resolve() for path in (options.input, options.output) if path}:
            Path(options.report).write_text(json.dumps({"ok": False, "version": VERSION, "error": error_text}, ensure_ascii=False), encoding="utf-8")
        if sys.stderr:
            print(error_text, file=sys.stderr)
        elif not options.self_test and not options.report:
            ctypes.windll.user32.MessageBoxW(0, error_text, TITLE, 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
