"""Configuration editing; draft data never mutates a saved profile."""
import copy
import uuid
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .models import validate_profile


class EditorData:
    def __init__(self, profile=None):
        self.profile = copy.deepcopy(profile) if profile else {
            "schema_version": 1, "id": uuid.uuid4().hex, "name": "新配置", "description": "",
            "common_init": "", "windows": []}

    def add_window(self):
        if len(self.profile["windows"]) >= 32: raise ValueError("最多32个窗口")
        window = {"id": uuid.uuid4().hex, "name": f"窗口 {len(self.profile['windows']) + 1}", "enabled": True,
                  "working_directory": "", "commands": "", "start_delay_seconds": 0}
        self.profile["windows"].append(window); return window

    def duplicate_window(self, index):
        if len(self.profile["windows"]) >= 32: raise ValueError("最多32个窗口")
        window = copy.deepcopy(self.profile["windows"][index]); window["id"] = uuid.uuid4().hex
        window["name"] += "（副本）"; self.profile["windows"].insert(index + 1, window); return window

    def move_window(self, index, offset):
        destination = index + offset
        if 0 <= destination < len(self.profile["windows"]):
            items = self.profile["windows"]; items[index], items[destination] = items[destination], items[index]
            return destination
        return index

    def delete_window(self, index): self.profile["windows"].pop(index)


class ProfileEditor(tk.Toplevel):
    def __init__(self, parent, profile, on_save):
        super().__init__(parent)
        self.title('Launch Profile Editor'); self.geometry("980x720"); self.minsize(780, 580)
        self.transient(parent); self.grab_set()
        self.data = EditorData(profile); self.on_save = on_save; self.index = None
        if not self.data.profile["windows"]: self.data.add_window()
        self.original = copy.deepcopy(self.data.profile)
        self.name = tk.StringVar(value=self.data.profile["name"])
        self.description = tk.StringVar(value=self.data.profile["description"])
        self.window_name = tk.StringVar(); self.directory = tk.StringVar(); self.delay = tk.StringVar()
        self.enabled = tk.BooleanVar(value=True)
        self._build(); self._refresh(0)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Control-s>", lambda event: self._save())

    def _build(self):
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=12); header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="配置名称").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(header, textvariable=self.name).grid(row=0, column=1, sticky="ew")
        ttk.Label(header, text="说明").grid(row=1, column=0, pady=(8, 0))
        ttk.Entry(header, textvariable=self.description).grid(row=1, column=1, sticky="ew", pady=(8, 0))
        book = ttk.Notebook(self); book.grid(row=1, column=0, sticky="nsew", padx=12)
        common = ttk.Frame(book, padding=10); book.add(common, text="公共初始化")
        common.columnconfigure(0, weight=1); common.rowconfigure(1, weight=1)
        ttk.Label(common, text="以下命令在每个窗口执行一次。请使用 export 让环境变量被子进程继承。",
                  wraplength=800).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.common = ScrolledText(common, wrap="none", undo=True, font=("TkFixedFont", 11))
        self.common.grid(row=1, column=0, sticky="nsew"); self.common.insert("1.0", self.data.profile["common_init"])
        ttk.Label(common, text="示例：source /opt/ros/humble/setup.bash\nexport ROS_DOMAIN_ID=10\nexport RMW_IMPLEMENTATION=rmw_cyclonedds_cpp").grid(row=2, column=0, sticky="w", pady=10)
        windows = ttk.Frame(book, padding=10); book.add(windows, text="终端窗口")
        windows.columnconfigure(1, weight=1); windows.rowconfigure(0, weight=1)
        left = ttk.Frame(windows); left.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        left.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(left, width=23, exportselection=False)
        self.listbox.grid(row=0, column=0, columnspan=2, sticky="ns")
        self.listbox.bind("<<ListboxSelect>>", self._select)
        actions = [("新建窗口", "add"), ("复制", "copy"), ("删除", "delete"), ("上移", "up"), ("下移", "down")]
        for row, (label, action) in enumerate(actions, 1):
            ttk.Button(left, text=label, command=lambda value=action: self._action(value)).grid(row=row, column=0, columnspan=2, sticky="ew", pady=3)
        right = ttk.Frame(windows); right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1); right.rowconfigure(5, weight=1)
        for row, (label, variable) in enumerate([( "窗口名称", self.window_name), ("远端工作目录", self.directory), ("延迟秒数", self.delay)]):
            ttk.Label(right, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
            ttk.Entry(right, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(right, text="启用此窗口", variable=self.enabled).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Label(right, text="启动命令 · 完整 Bash 片段").grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        self.commands = ScrolledText(right, wrap="none", undo=True, font=("TkFixedFont", 11))
        self.commands.grid(row=5, column=0, columnspan=2, sticky="nsew")
        ttk.Label(right, text="工作目录可留空，或使用 /绝对路径、~/路径。\n前台程序结束后才执行下一条命令；多个常驻程序建议分窗口。\n延迟相对于本轮共同起点，不保证节点已就绪。", wraplength=650).grid(row=6, column=0, columnspan=2, sticky="w", pady=10)
        footer = ttk.Frame(self, padding=12); footer.grid(row=2, column=0, sticky="ew")
        ttk.Button(footer, text="保存配置", command=self._save).pack(side="right")
        ttk.Button(footer, text="取消", command=self._close).pack(side="right", padx=8)

    def _flush(self):
        self.data.profile.update(name=self.name.get(), description=self.description.get(), common_init=self.common.get("1.0", "end-1c"))
        if self.index is not None:
            try: delay = float(self.delay.get())
            except ValueError: raise ValueError("启动延迟须为0至3600的数值")
            window = self.data.profile["windows"][self.index]
            window.update(name=self.window_name.get(), working_directory=self.directory.get(), start_delay_seconds=delay,
                          enabled=self.enabled.get(), commands=self.commands.get("1.0", "end-1c"))

    def _refresh(self, index):
        self.listbox.delete(0, "end")
        for window in self.data.profile["windows"]:
            self.listbox.insert("end", ("● " if window["enabled"] else "○ ") + window["name"])
        self.index = index if self.data.profile["windows"] else None
        if self.index is not None:
            self.index = min(self.index, len(self.data.profile["windows"]) - 1)
            self.listbox.selection_set(self.index)
            window = self.data.profile["windows"][self.index]
            self.window_name.set(window["name"]); self.directory.set(window["working_directory"])
            self.delay.set(str(window["start_delay_seconds"])); self.enabled.set(window["enabled"])
            self.commands.delete("1.0", "end"); self.commands.insert("1.0", window["commands"])
        else:
            self.window_name.set(""); self.directory.set(""); self.delay.set("0")
            self.commands.delete("1.0", "end")

    def _select(self, event=None):
        selection = self.listbox.curselection()
        if selection and selection[0] != self.index:
            try: self._flush(); self._refresh(selection[0])
            except ValueError as exc:
                messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self)
                self.listbox.selection_clear(0, "end")
                if self.index is not None: self.listbox.selection_set(self.index)

    def _action(self, action):
        try:
            self._flush(); index = self.index
            if action == "add": self.data.add_window(); index = len(self.data.profile["windows"]) - 1
            elif index is not None:
                if action == "copy": self.data.duplicate_window(index); index += 1
                elif action == "delete":
                    if not messagebox.askyesno('Confirm - SSH Multi Terminal', "删除此窗口配置？", parent=self): return
                    self.data.delete_window(index)
                elif action == "up": index = self.data.move_window(index, -1)
                elif action == "down": index = self.data.move_window(index, 1)
            self._refresh(index or 0)
        except ValueError as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self)

    def _save(self):
        try:
            self._flush(); validated = validate_profile(self.data.profile)
            self.on_save(validated)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self); return False
        self.destroy(); return True

    def _close(self):
        try: self._flush()
        except ValueError:
            if messagebox.askyesno('Confirm - SSH Multi Terminal', "字段含无效值，是否放弃修改并关闭？", parent=self): self.destroy()
            return
        if self.data.profile != self.original:
            result = messagebox.askyesnocancel('Confirm - SSH Multi Terminal', "是否保存修改？", parent=self)
            if result is None: return
            if result: self._save(); return
        self.destroy()
