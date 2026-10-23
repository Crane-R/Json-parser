import re
import tkinter as tk
from tkinter import ttk

from tkinterdnd2 import DND_FILES

from versioning import VERSION


# 说明：这一层刻意不依赖 customtkinter。实测它导入一次要 450 ms 以上，
# 且圆角控件每次重绘都要走 canvas，是启动慢、操作发涩的主要来源。
# 全部改用原生 Tk/ttk 控件后，启动和交互都快一个量级，观感保持一致。
BACKGROUND = "#f4f6fb"
SURFACE = "#ffffff"
EDITOR = "#ffffff"
BORDER = "#e2e6ef"
TEXT = "#253047"
MUTED = "#6b778f"
ACCENT = "#7256db"
ACCENT_SOFT = "#ece6ff"
ACCENT_SOFT_HOVER = "#dcd2fb"
CONTROL = "#f5f6fb"
CONTROL_HOVER = "#eaeef7"
TRACK = "#eceef6"
FONT = "Microsoft YaHei UI"


class SegmentedControl(tk.Frame):
    """查找 / 替换 模式切换。保留 get()/set() 接口，内部是普通按钮。"""

    def __init__(self, parent, values, command=None, font=(FONT, 10)):
        super().__init__(parent, bg=TRACK, borderwidth=0, highlightthickness=0)
        self._command = command
        self._selected = values[0]
        self._buttons = {}
        for index, value in enumerate(values):
            button = tk.Button(self, text=value, font=font, relief="flat", borderwidth=0,
                               bg=TRACK, fg=TEXT, activebackground=ACCENT_SOFT_HOVER,
                               activeforeground=TEXT, padx=12, pady=2, cursor="hand2",
                               command=lambda selected=value: self.set(selected))
            button.grid(row=0, column=index, padx=1, pady=1)
            self._buttons[value] = button
        self._paint()

    def _paint(self):
        for value, button in self._buttons.items():
            active = value == self._selected
            button.configure(bg=ACCENT_SOFT if active else TRACK,
                             fg=ACCENT if active else TEXT)

    def get(self):
        return self._selected

    def set(self, value):
        if value not in self._buttons or value == self._selected:
            return
        self._selected = value
        self._paint()
        if self._command is not None:
            self._command(value)


class TargetSelector(tk.Menubutton):
    """查找范围下拉（结果 / 输入）。切换标签页时重新绑定到新文档的变量。"""

    def __init__(self, parent, values, font=(FONT, 10), width=4):
        super().__init__(parent, relief="flat", borderwidth=0, bg=CONTROL, fg=TEXT,
                         activebackground=CONTROL_HOVER, font=font, width=width,
                         cursor="hand2", highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=BORDER, direction="below", indicatoron=True)
        self._values = list(values)
        self._menu = tk.Menu(self, tearoff=False, bg=SURFACE, fg=TEXT,
                             activebackground=ACCENT_SOFT, activeforeground=TEXT,
                             relief="flat", borderwidth=0, font=font)
        self.configure(menu=self._menu)
        for value in self._values:
            self._menu.add_radiobutton(label=value, value=value)

    def attach(self, variable):
        for index, value in enumerate(self._values):
            self._menu.entryconfigure(index, variable=variable, value=value)
        self.configure(textvariable=variable)


class ModernTabs(tk.Frame):
    def __init__(self, parent, toolbar):
        super().__init__(parent, bg=EDITOR, borderwidth=0)
        self.frames = []
        self.names = []
        self.active = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.selector = tk.Frame(toolbar, bg="#f1f3f9", borderwidth=0)
        self.selector.pack(side="right", padx=(0, 6))
        self.buttons = []
        for index, title in enumerate(("JSON 文本", "结构树", "处理说明")):
            button = tk.Button(self.selector, text=title, font=(FONT, 8), relief="flat", borderwidth=0,
                               bg="#f1f3f9", fg=TEXT, activebackground=ACCENT_SOFT_HOVER, padx=6, pady=1,
                               cursor="hand2", command=lambda selected=index: self.select(selected))
            button.pack(side="left")
            self.buttons.append(button)

    def add(self, frame, text):
        self.frames.append(frame)
        self.names.append(text)
        if len(self.frames) == 1:
            self.select(0)

    def select(self, index):
        self.active = index
        for frame in self.frames:
            frame.grid_remove()
        self.frames[index].grid(row=0, column=0, sticky="nsew")
        for position, button in enumerate(self.buttons):
            button.configure(bg=ACCENT_SOFT if position == index else "#f1f3f9")

    def tab(self, index, text):
        self.names[index] = text
        self.buttons[index].configure(text=text)

    def _selected(self, name):
        self.select(self.names.index(name))


class ApplicationView:
    def _action(self, name, **options):
        owner = self.owner
        standalone = self if owner is None else None
        def invoke(*arguments):
            document = owner.active_document if owner is not None else standalone
            return getattr(document, name)(*arguments, **options)
        return invoke

    def _control(self, widget, name, option="variable"):
        if self.owner is not None:
            self.owner.control_variables.append((widget, option, name))
        return widget

    def _button(self, parent, text, command, primary=False, width=8):
        return tk.Button(
            parent, text=text, command=command, width=width, relief="flat", borderwidth=0,
            bg=ACCENT if primary else CONTROL, fg="white" if primary else TEXT,
            activebackground="#6144c8" if primary else CONTROL_HOVER,
            activeforeground="white" if primary else TEXT,
            disabledforeground="#9aa4b7", font=(FONT, 10), padx=6, pady=3,
            highlightthickness=1 if not primary else 0,
            highlightbackground=BORDER, highlightcolor=BORDER, cursor="hand2",
        )

    def _toggle(self, parent, text, variable, command, width):
        """CTkSwitch 的替代：无指示框的复选按钮，看起来就是开关。"""
        return tk.Checkbutton(parent, text=text, variable=variable, command=command,
                              indicatoron=False, relief="flat", borderwidth=0,
                              bg=CONTROL, fg=MUTED, selectcolor=ACCENT_SOFT,
                              activebackground=CONTROL_HOVER, activeforeground=TEXT,
                              disabledforeground="#9aa4b7", font=(FONT, 10),
                              padx=8, pady=2, width=width, cursor="hand2",
                              highlightthickness=1, highlightbackground=BORDER,
                              highlightcolor=BORDER)

    def _entry(self, parent, variable, width=12):
        return tk.Entry(parent, textvariable=variable, width=width, font=(FONT, 11),
                        bg=SURFACE, fg=TEXT, insertbackground=ACCENT,
                        relief="flat", borderwidth=0, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=ACCENT)

    def _build(self):
        window = self.root
        if self.owner is None or not self.owner.controls:
            window.configure(bg=BACKGROUND)
            window.option_add("*Font", (FONT, 10))
            style = ttk.Style(window)
            style.theme_use("clam")
            style.configure("Treeview", font=("Consolas", 11), rowheight=28, background=EDITOR,
                            fieldbackground=EDITOR, foreground=TEXT, borderwidth=0)
            style.configure("Treeview.Heading", font=(FONT, 10), background="#f3f5fa",
                            foreground=MUTED, relief="flat", padding=7)
            style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", TEXT)])
            style.map("Treeview.Heading", background=[("active", "#e9edf5")])
            style.configure("Vertical.TScrollbar", background="#cbd2e0", troughcolor=EDITOR,
                            borderwidth=0, arrowcolor=MUTED, relief="flat")
            style.configure("Horizontal.TScrollbar", background="#cbd2e0", troughcolor=EDITOR,
                            borderwidth=0, arrowcolor=MUTED, relief="flat")
        if self.owner is None:
            window.title(f"JSON 万能解析器 · v{VERSION}")
            window.geometry("1320x900")
            window.minsize(1060, 800)
        root = self.shell = tk.Frame(self.parent, bg=BACKGROUND, borderwidth=0)
        if self.owner is None:
            root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1 if self.owner is None else 0, weight=1)
        if self.owner is None or not self.owner.controls:
            self._build_toolbar(root)
        else:
            for name, widget in self.owner.controls.items():
                setattr(self, name, widget)
        workspace = self.workspace = tk.Frame(root, bg=BACKGROUND, borderwidth=0)
        workspace.grid(row=1 if self.owner is None else 0, column=0, sticky="nsew", padx=4)
        workspace.grid_columnconfigure((0, 1), weight=1, uniform="editor")
        workspace.grid_rowconfigure(0, weight=1)
        left, right = self.editor_panels = [
            tk.Frame(workspace, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER, borderwidth=0)
            for unused in range(2)
        ]
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        headings = []
        for panel, title, count in ((left, "原始输入", self.input_count), (right, "解析结果", self.output_count)):
            panel.grid_columnconfigure(0, weight=1)
            panel.grid_rowconfigure(1, weight=1)
            heading = tk.Frame(panel, bg=SURFACE, height=22, borderwidth=0)
            heading.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
            tk.Label(heading, text=title, bg=SURFACE, fg=TEXT, font=(FONT, 9, "bold"), pady=0).pack(side="left")
            tk.Label(heading, textvariable=count, bg=SURFACE, fg=MUTED, font=(FONT, 8), pady=0).pack(side="right")
            headings.append(heading)
        input_area = tk.Frame(left, bg=EDITOR, borderwidth=0)
        input_area.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 2))
        self.source = self._editor(input_area)
        self.source.bind("<<Modified>>", self._modified)
        self.source.drop_target_register(DND_FILES)
        self.source.dnd_bind("<<Drop>>", self._drop)
        self.input_hint = tk.Label(self.source, text="粘贴任何文本，从这里开始\n\nJSON · XML · 转义字符串 · YAML · CSV\n\n也可以将文件拖入此处",
                                   bg=EDITOR, fg=MUTED, font=(FONT, 12), justify="center", cursor="xterm")
        self.input_hint.place(relx=.5, rely=.42, anchor="center")
        self.input_hint.bind("<Button-1>", lambda event: self.source.focus_set())
        self.input_hint.drop_target_register(DND_FILES)
        self.input_hint.dnd_bind("<<Drop>>", self._drop)
        for title, variable in (("排序", self.sort_keys), ("压缩", self.compact)):
            tk.Checkbutton(headings[0], text=title, variable=variable, command=self.reformat,
                           bg=SURFACE, fg=MUTED, activebackground=SURFACE, selectcolor=SURFACE,
                           font=(FONT, 8), borderwidth=0, highlightthickness=0,
                           pady=0, cursor="hand2").pack(side="right", padx=(0, 8))
        self.tabs = ModernTabs(right, headings[1])
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 2))
        text_tab, tree_tab, info_tab = (tk.Frame(self.tabs, bg=EDITOR, borderwidth=0) for unused in range(3))
        self.tabs.add(text_tab, text="JSON 文本")
        self.tabs.add(tree_tab, text="结构树")
        self.tabs.add(info_tab, text="处理说明")
        self.output = self._editor(text_tab, readonly=True)
        self.info = self._editor(info_tab, readonly=True, wrap="word")
        self.output_hint = tk.Label(self.output, text="解析结果将在这里出现\n\n自动识别格式，输出标准 JSON",
                                    bg=EDITOR, fg=MUTED, font=(FONT, 12), justify="center")
        self.output_hint.place(relx=.5, rely=.42, anchor="center")
        tree_actions = tk.Frame(tree_tab, bg=SURFACE, borderwidth=0)
        tree_actions.pack(side="bottom", fill="x")
        self._button(tree_actions, "复制节点值", self.copy_node, width=10).pack(side="left", padx=8, pady=4)
        self._button(tree_actions, "复制 JSON 路径", lambda: self.copy_node(path_only=True), width=14).pack(side="left")
        tree_scroll = ttk.Scrollbar(tree_tab, orient="vertical", style="Vertical.TScrollbar")
        tree_scroll.pack(side="right", fill="y")
        self.tree = ttk.Treeview(tree_tab, columns=("type", "value"), selectmode="browse", yscrollcommand=tree_scroll.set)
        tree_scroll.configure(command=self.tree.yview)
        for column, label, width in (("#0", "字段 / 索引", 160), ("type", "类型", 80), ("value", "值 / 大小", 210)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, stretch=column != "type")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewOpen>>", self._expand_tree)
        self.tree.bind("<Double-1>", self._more_tree)
        if self.owner is None or "search_entry" not in self.owner.controls:
            self._build_search(root)
        if self.owner is None:
            for sequence, callback in (("<Control-Return>", self.parse), ("<Control-o>", self.open_file),
                                        ("<Control-s>", self.save), ("<Control-f>", self.open_search),
                                        ("<Control-h>", self.open_replace), ("<Escape>", self.close_search), ("<F5>", self.parse)):
                window.bind(sequence, lambda event, action=callback: action())

    def _build_toolbar(self, root):
        toolbar = self.toolbar = tk.Frame(root if self.owner is None else self.owner.toolbar_host,
                                          bg=BACKGROUND, height=30, borderwidth=0)
        toolbar.grid(row=0, column=0, sticky="ew", padx=4, pady=0)
        if self.owner is None:
            tk.Label(toolbar, text="{ } JSON 万能解析器", bg=BACKGROUND, fg=ACCENT,
                     font=(FONT, 13, "bold")).pack(side="left", padx=(2, 14))
        self._button(toolbar, "打开文件", self.open_file if self.owner is None else self.owner.open_files, width=8).pack(side="left", padx=(0, 5))
        self._button(toolbar, "粘贴并解析", self._action("paste"), primary=True, width=10).pack(side="left", padx=(0, 5))
        self._button(toolbar, "清空", self._action("clear"), width=5).pack(side="left", padx=(0, 10))
        self.copy_button = self._button(toolbar, "复制结果", self._action("copy"), width=8)
        self.copy_button.pack(side="left", padx=(0, 5))
        self.save_button = self._button(toolbar, "导出 JSON", self._action("save"), width=9)
        self.save_button.pack(side="left", padx=(0, 12))
        self.copy_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        for title, name, width in (("严格 JSON", "strict", 10), ("展开内嵌 JSON", "nested", 15)):
            control = self._toggle(toolbar, title, getattr(self, name), self._action("invalidate"), width)
            self._control(control, name).pack(side="left", padx=(0, 8))
        self.notice = tk.Label(toolbar, textvariable=self.status, bg=BACKGROUND, fg=MUTED,
                               font=(FONT, 10), anchor="e")
        self._control(self.notice, "status", "textvariable").pack(side="right")
        if self.owner is not None:
            self.owner.controls.update(toolbar=toolbar, copy_button=self.copy_button,
                                       save_button=self.save_button, notice=self.notice)

    def _build_search(self, root):
        self.search_panel = self.owner.search_host if self.owner is not None else tk.Frame(
            root, bg="#f8f9fd", highlightthickness=1, highlightbackground=BORDER, borderwidth=0)
        self.search_panel.grid_columnconfigure(0, weight=1)
        header = tk.Frame(self.search_panel, bg="#f8f9fd")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        self.search_mode = SegmentedControl(header, ["查找", "替换"], command=self._action("set_search_mode"))
        self.search_mode.grid(row=0, column=0, padx=(0, 20))
        target_button = TargetSelector(header, ["结果", "输入"])
        target_button.attach(self.search_target)
        self._control(target_button, "search_target", "@attach").grid(row=0, column=1, padx=(0, 12))
        match_case = tk.Checkbutton(header, text="区分大小写", variable=self.match_case, bg="#f8f9fd",
                                    fg=MUTED, activebackground="#f8f9fd", selectcolor=ACCENT_SOFT,
                                    font=(FONT, 10), borderwidth=0, highlightthickness=0, cursor="hand2")
        self._control(match_case, "match_case").grid(row=0, column=2)
        header.grid_columnconfigure(3, weight=1)
        self._button(header, "×", self._action("close_search"), width=3).grid(row=0, column=4, padx=(8, 0))
        search = tk.Frame(self.search_panel, bg="#f8f9fd")
        search.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        search.grid_columnconfigure(0, weight=1)
        self.search_entry = self._entry(search, self.search_term)
        self._control(self.search_entry, "search_term", "textvariable").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.search_entry.bind("<Return>", lambda event, action=self._action("find"): action())
        self.search_entry.bind("<Shift-Return>", lambda event, action=self._action("find", previous=True): action())
        counter = tk.Label(search, textvariable=self.search_count, bg="#f8f9fd", fg=MUTED,
                           width=6, font=("Consolas", 11), anchor="e")
        self._control(counter, "search_count", "textvariable").grid(row=0, column=1)
        self._button(search, "↑", self._action("find", previous=True), width=3).grid(row=0, column=2, padx=2)
        self._button(search, "↓", self._action("find"), width=3).grid(row=0, column=3, padx=(2, 6))
        replacement = self.replace_row = tk.Frame(self.search_panel, bg="#f8f9fd")
        replacement.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        replacement.grid_columnconfigure(0, weight=1)
        self.replace_entry = self._entry(replacement, self.replacement_term)
        self._control(self.replace_entry, "replacement_term", "textvariable").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.replace_entry.bind("<Return>", lambda event, action=self._action("replace"): action())
        self._button(replacement, "替换", self._action("replace"), width=6).grid(row=0, column=1, padx=(0, 6))
        self._button(replacement, "全部替换", self._action("replace", all_matches=True), width=9).grid(row=0, column=2)
        replacement.grid_remove()
        if self.owner is not None:
            self.owner.controls.update(search_entry=self.search_entry, replace_entry=self.replace_entry,
                                       search_panel=self.search_panel, search_mode=self.search_mode, replace_row=self.replace_row)

    def _editor(self, parent, readonly=False, wrap="none"):
        frame = tk.Frame(parent, bg=EDITOR, borderwidth=0)
        frame.pack(fill="both", expand=True)
        editor = tk.Text(frame, wrap=wrap, width=1, height=1, undo=not readonly, font=("Consolas", 12),
                         background=EDITOR, foreground=TEXT, insertbackground=ACCENT,
                         selectbackground="#e2d9ff", selectforeground=TEXT, relief="flat",
                         borderwidth=0, highlightthickness=0, padx=12, pady=8, tabs=(32,),
                         spacing1=2, spacing3=2, insertwidth=2)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=editor.yview, style="Vertical.TScrollbar")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=editor.xview, style="Horizontal.TScrollbar")
        editor.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        editor.pack(fill="both", expand=True)
        for tag, color in (("key", "#7150b5"), ("string", "#15745b"), ("number", "#a65a10"), ("literal", "#2468b0")):
            editor.tag_configure(tag, foreground=color)
        editor.tag_configure("matches", background="#fff3d0", foreground="#684607")
        editor.tag_configure("found", background="#ffd67c", foreground="#513600")
        if readonly:
            editor.configure(state="disabled")
        editor.bind("<Control-a>", lambda event: self._select_all(editor))
        menu = tk.Menu(editor, tearoff=False, bg=SURFACE, fg=TEXT, activebackground=ACCENT_SOFT,
                       activeforeground=TEXT, relief="flat", borderwidth=0)
        menu.add_command(label="复制", command=lambda: editor.event_generate("<<Copy>>"))
        if not readonly:
            menu.add_command(label="粘贴", command=lambda: editor.event_generate("<<Paste>>"))
            menu.add_command(label="撤销", command=lambda: editor.event_generate("<<Undo>>"))
        menu.add_command(label="全选", command=lambda: self._select_all(editor))
        editor.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))
        return editor

    def _highlight(self, text):
        if len(text) > HIGHLIGHT_LIMIT:
            return
        pattern = r'"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'
        tag_ranges = {tag: [] for tag in ("key", "string", "number", "literal")}
        key_suffix = re.compile(r"\s*:")
        for match in re.finditer(pattern, text):
            token = match.group()
            if token.startswith('"'):
                tag = "key" if key_suffix.match(text, match.end()) else "string"
            else:
                tag = "literal" if token in ("true", "false", "null") else "number"
            tag_ranges[tag].extend((f"1.0+{match.start()}c", f"1.0+{match.end()}c"))
        for tag, ranges in tag_ranges.items():
            for offset in range(0, len(ranges), 1000):
                self.output.tag_add(tag, *ranges[offset:offset + 1000])
        self.output.tag_raise("matches")
        self.output.tag_raise("found")

    def _sync_hints(self):
        if self.source.get("1.0", "end-1c"):
            self.input_hint.place_forget()
        else:
            self.input_hint.place(relx=.5, rely=.42, anchor="center")
        if self.result is None:
            self.output_hint.place(relx=.5, rely=.42, anchor="center")
        else:
            self.output_hint.place_forget()


# 超过这个长度就不再做语法着色 / 匹配着色，保证大文件下依然跟手
HIGHLIGHT_LIMIT = 400_000
# 结果文本超过这个长度时只渲染前一段：整段塞进 Tk 文本控件会随大小线性变慢
# （125 MB 约 3 秒），截断后界面始终跟手，完整内容仍可导出。
PREVIEW_LIMIT = 24 * 1024 * 1024
