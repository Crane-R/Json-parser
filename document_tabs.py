import tkinter as tk
from tkinter import filedialog

from tkinterdnd2 import DND_FILES

from desktop_ui import ACCENT, ACCENT_SOFT, BACKGROUND, BORDER, FONT, MUTED, TEXT
from settings import Settings, default_geometry, minimum_size, remember_window_now, restore_window
from versioning import VERSION


TAB_TITLE_LIMIT = 18


class DocumentTabs:
    def __init__(self, root, application_factory, remember=True):
        self.root = root
        self.application_factory = application_factory
        self.documents = []
        self.titles = {}
        self.tab_widgets = {}
        self.active_document = None
        self.sequence = 0
        self.closed = False
        self.controls = {}
        self.control_variables = []
        self.reveal_job = None
        self.settings = Settings()
        self.remember = remember
        self._last_geometry = None
        self._overflow = None
        root.title(f"JSON 万能解析器 · v{VERSION}")
        if not (remember and restore_window(root, self.settings)):
            root.geometry(default_geometry(root))
        root.minsize(*minimum_size(root))
        root.configure(bg=BACKGROUND)
        if remember:
            root.bind("<Configure>", self._track_geometry, add=True)
        self.shell = tk.Frame(root, bg=BACKGROUND, borderwidth=0)
        self.shell.pack(fill="both", expand=True)
        self.shell.grid_columnconfigure(0, weight=1)
        self.shell.grid_rowconfigure(2, weight=1)
        self._build_tab_bar()
        self.toolbar_host = tk.Frame(self.shell, bg=BACKGROUND, height=32, borderwidth=0)
        self.toolbar_host.grid(row=1, column=0, sticky="nsew", padx=4)
        self.toolbar_host.grid_columnconfigure(0, weight=1)
        self.toolbar_host.grid_rowconfigure(0, weight=1)
        self.pages = tk.Frame(self.shell, bg=BACKGROUND, borderwidth=0)
        self.pages.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 2))
        self.search_host = tk.Frame(self.shell, bg="#f8f9fd", highlightthickness=1,
                                    highlightbackground=BORDER, borderwidth=0)
        root.protocol("WM_DELETE_WINDOW", self.close)
        # 窗口首次显示（<Map>）时再同步一次高度，保证单标签也不会卡在 1px 高
        root.bind("<Map>", lambda event=None: self._sync_tab_height(), add=True)
        for sequence, callback in (("<Control-t>", self.new_document), ("<Control-w>", self.close_current),
                                    ("<Control-Tab>", self.next_document),
                                    ("<Control-Shift-Tab>", lambda: self.next_document(-1)),
                                    ("<Control-ISO_Left_Tab>", lambda: self.next_document(-1)),
                                    ("<Control-o>", self.open_files)):
            root.bind(sequence, lambda event, action=callback: self._shortcut(action))
        for sequence, action in (("<Control-Return>", "parse"), ("<F5>", "parse"), ("<Control-s>", "save"),
                                  ("<Control-f>", "open_search"), ("<Control-h>", "open_replace"),
                                  ("<Escape>", "close_search")):
            root.bind(sequence, lambda event, name=action: self._dispatch(name))
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", self._drop)
        self.new_document()

    # ---------- 标签栏 ----------

    def _build_tab_bar(self):
        tab_bar = self.tab_bar = tk.Frame(self.shell, bg=BACKGROUND, borderwidth=0)
        tab_bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(3, 0))
        tab_bar.grid_columnconfigure(0, weight=1)
        # 画布只负责横向滚动；高度由真实标签高度决定（见 _sync_tab_height），
        # 不再用探针预估——高 DPI 下探针偏小就会让画布比标签矮，文字下半截被裁（「被盖住」）。
        self.canvas = tk.Canvas(tab_bar, bg=BACKGROUND, highlightthickness=0, borderwidth=0, height=30)
        self.canvas.grid(row=0, column=0, sticky="ew")
        self.tab_strip = tk.Frame(self.canvas, bg=BACKGROUND, borderwidth=0)
        self.canvas_window = self.canvas.create_window(0, 0, window=self.tab_strip, anchor="nw")
        self.tab_strip.bind("<Configure>", self._on_strip_configure)
        self.canvas.bind("<Configure>", self._update_scroll)
        for widget in (self.canvas, self.tab_strip):
            widget.bind("<MouseWheel>", self._scroll_tabs, add=True)
        self.arrows = []
        for column, label, step in ((1, "‹", -1), (2, "›", 1)):
            button = tk.Button(tab_bar, text=label, width=2, relief="flat", borderwidth=0,
                               bg=BACKGROUND, fg=MUTED, activebackground=ACCENT_SOFT,
                               activeforeground=ACCENT, font=(FONT, 12), pady=2, cursor="hand2",
                               command=lambda direction=step: self._scroll_pages(direction))
            button.grid(row=0, column=column, padx=(2, 0), sticky="ns")
            self.arrows.append(button)
        new_button = tk.Button(tab_bar, text="+", width=3, relief="flat", borderwidth=0,
                               bg=ACCENT_SOFT, fg=ACCENT, activebackground=ACCENT_SOFT,
                               activeforeground=ACCENT, font=(FONT, 13), pady=2, cursor="hand2",
                               command=self.new_document)
        new_button.grid(row=0, column=3, padx=(3, 0), sticky="ns")
        overflow = tk.Button(tab_bar, text="≡", width=3, relief="flat", borderwidth=0,
                             bg=BACKGROUND, fg=MUTED, activebackground=ACCENT_SOFT,
                             activeforeground=ACCENT, font=(FONT, 13), pady=2, cursor="hand2",
                             command=self._open_overflow)
        overflow.grid(row=0, column=4, padx=(2, 0), sticky="ns")
        self.overflow_button = overflow
        tab_bar.configure(height=30)
        tab_bar.grid_propagate(False)

    def _on_strip_configure(self, event=None):
        """标签条尺寸变化（新增/删除/窗口缩放）时同步高度并重算滚动。"""
        self._sync_tab_height()
        self._update_scroll()

    def _sync_tab_height(self):
        """画布与标签栏高度始终等于真实标签高度，保证文字不被裁切。"""
        if not self.tab_strip.winfo_exists():
            return
        if self.tab_strip.winfo_height() < 1:
            self.tab_strip.update_idletasks()
        height = self.tab_strip.winfo_height()
        if height < 1:
            height = self.tab_strip.winfo_reqheight()
        if height < 1 or self.canvas.winfo_height() == height:
            return
        self.canvas.configure(height=height)
        self.tab_bar.configure(height=height)

    def _scroll_pages(self, direction):
        self.canvas.xview_scroll(direction * 3, "units")
        self._update_scroll()

    def _fill_overflow(self):
        if self._overflow is None:
            self._overflow = tk.Menu(self.root, tearoff=False, bg="#ffffff", fg=TEXT,
                                     activebackground=ACCENT_SOFT, activeforeground=TEXT,
                                     relief="flat", borderwidth=0, font=(FONT, 10))
        menu = self._overflow
        menu.delete(0, "end")
        for document in self.documents:
            title = self.titles.get(document, "JSON")
            label = ("● " if document is self.active_document else "    ") + title
            menu.add_command(label=label, command=lambda page=document: self.select_document(page))
        if len(self.documents) > 1:
            menu.add_separator()
            menu.add_command(label="关闭其它标签页", command=self.close_others)
        return menu

    def _open_overflow(self):
        menu = self._fill_overflow()
        button = self.overflow_button
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def close_others(self):
        active = self.active_document
        for document in [page for page in self.documents if page is not active]:
            self.close_document(document)

    def _shortcut(self, callback):
        callback()
        return "break"

    def _dispatch(self, action):
        if self.active_document is not None:
            target = getattr(self.active_document, action)
            target.focus_set() if action.endswith("entry") else target()
        return "break"

    def new_document(self):
        if self.closed:
            return None
        self.sequence += 1
        document = self.application_factory(self.root, parent=self.pages, owner=self)
        self._route_shortcuts(document.shell)
        self._route_shortcuts(document.toolbar)
        if len(self.documents) == 0:
            self._route_shortcuts(self.search_host)
        self.documents.append(document)
        self.titles[document] = f"JSON {self.sequence}"
        card = tk.Frame(self.tab_strip, bg=BACKGROUND, borderwidth=0)
        card.pack(side="left", padx=1, pady=2)
        button = tk.Button(card, text=self.titles[document], width=14, relief="flat", borderwidth=0,
                           bg=BACKGROUND, fg=TEXT, activebackground=ACCENT_SOFT, activeforeground=ACCENT,
                           font=(FONT, 9), cursor="hand2", command=lambda: self.select_document(document))
        button.pack(side="left")
        close_button = tk.Button(card, text="×", width=2, relief="flat", borderwidth=0,
                                 bg=BACKGROUND, fg=MUTED, activebackground="#f8dedf", activeforeground=TEXT,
                                 font=(FONT, 10), cursor="hand2", command=lambda: self.close_document(document))
        close_button.pack(side="left", padx=(0, 2))
        for widget in (card, button, close_button):
            widget.bind("<MouseWheel>", self._scroll_tabs, add=True)
            widget.bind("<Button-2>", lambda event, page=document: self.close_document(page), add=True)
        self.tab_widgets[document] = (card, button)
        self.select_document(document)
        self._sync_tab_height()
        self.root.after(0, self._sync_tab_height)
        self._update_scroll()
        return document

    def _route_shortcuts(self, widget):
        tags = list(widget.bindtags())
        window_tag = str(self.root)
        if window_tag in tags:
            tags.remove(window_tag)
            tags.insert(1, window_tag)
            widget.bindtags(tuple(tags))
        for child in widget.winfo_children():
            self._route_shortcuts(child)

    def select_document(self, document):
        if self.closed or document not in self.documents:
            return
        if document is self.active_document:
            return
        previous = self.active_document
        if previous in self.documents:
            previous.shell.pack_forget()
            self._style_tab(previous, False)
        self.active_document = document
        document.shell.pack(fill="both", expand=True)
        self._style_tab(document, True)
        for widget, option, name in self.control_variables:
            value = getattr(document, name)
            if option.startswith("@"):
                getattr(widget, option[1:])(value)
            else:
                widget.configure(**{option: value})
        document._sync_result_actions()
        document._sync_search_panel()
        self.root.title(f"{self.titles[document]} · JSON 万能解析器 v{VERSION}")
        document.source.focus_set()
        if self.reveal_job is None:
            self.reveal_job = self.root.after_idle(self._reveal_current)

    def _style_tab(self, document, active):
        card, button = self.tab_widgets[document]
        color = ACCENT_SOFT if active else BACKGROUND
        card.configure(bg=color)
        for child in card.winfo_children():
            child.configure(bg=color)
        button.configure(fg=ACCENT if active else TEXT, font=(FONT, 9, "bold") if active else (FONT, 9))

    def _reveal_current(self):
        self.reveal_job = None
        if not self.closed and self.active_document in self.tab_widgets:
            self._reveal_document(self.active_document)

    def rename_document(self, document, title):
        if document not in self.documents:
            return
        self.titles[document] = title
        shown = title if len(title) <= TAB_TITLE_LIMIT else title[:TAB_TITLE_LIMIT - 1] + "…"
        self.tab_widgets[document][1].configure(text=shown)
        if document is self.active_document:
            self.root.title(f"{title} · JSON 万能解析器 v{VERSION}")
        self._update_scroll()

    def close_document(self, document):
        if self.closed or document not in self.documents:
            return
        index = self.documents.index(document)
        was_active = document is self.active_document
        self.documents.remove(document)
        self.titles.pop(document)
        self.tab_widgets.pop(document)[0].destroy()
        document.dispose()
        if not self.documents:
            self.active_document = None
            self.new_document()
        elif was_active:
            self.select_document(self.documents[min(index, len(self.documents) - 1)])
        self._update_scroll()

    def close_current(self):
        self.close_document(self.active_document)

    def next_document(self, step=1):
        if self.documents:
            index = self.documents.index(self.active_document)
            self.select_document(self.documents[(index + step) % len(self.documents)])

    def open_files(self, paths=None):
        paths = paths if paths is not None else filedialog.askopenfilenames(
            title="打开文本文件，可多选", filetypes=[("文本与数据", "*.json *.txt *.xml *.csv *.tsv *.yaml *.yml *.log *.jsonl"), ("所有文件", "*.*")])
        for index, path in enumerate(paths):
            page = self.active_document
            if index or page is None or page.source.get("1.0", "end-1c"):
                page = self.new_document()
            page.open_file(path)

    def _drop(self, event):
        self.open_files(self.root.tk.splitlist(event.data))
        return event.action

    def _scroll_tabs(self, event):
        self.canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
        self._update_scroll()
        return "break"

    def _update_scroll(self, event=None):
        if self.closed:
            return
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            return
        first, last = self.canvas.xview()
        self.arrows[0].configure(state="normal" if first > 0 else "disabled",
                                 fg=MUTED if first > 0 else "#c3c9d6")
        self.arrows[1].configure(state="normal" if last < 1 else "disabled",
                                 fg=MUTED if last < 1 else "#c3c9d6")
        overflow_needed = last - first < 1
        self.overflow_button.configure(fg=MUTED if overflow_needed or len(self.documents) > 1 else "#c3c9d6")

    def _reveal_document(self, document):
        card = self.tab_widgets[document][0]
        total = max(self.tab_strip.winfo_width(), 1)
        viewport = self.canvas.winfo_width()
        position = max(0, card.winfo_x() + card.winfo_width() - viewport)
        self.canvas.xview_moveto(position / total)
        self._update_scroll()

    def _track_geometry(self, event=None):
        try:
            if self.root.state() != "zoomed":
                self._last_geometry = self.root.geometry()
        except tk.TclError:
            pass

    def close(self):
        if self.closed:
            return
        if self.remember:
            remember_window_now(self.root, self.settings, self._last_geometry)
        self.closed = True
        if self.reveal_job is not None:
            self.root.after_cancel(self.reveal_job)
        for document in self.documents:
            document.dispose()
        self.documents.clear()
        self.titles.clear()
        self.tab_widgets.clear()
        self.controls.clear()
        self.control_variables.clear()
        self.active_document = None
        self.root.destroy()
