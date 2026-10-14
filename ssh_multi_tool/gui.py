"""Desktop application; workers communicate with Tk via a bounded queue."""
import copy
import queue
import sys
import threading
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .editor import ProfileEditor
from .debug_editor import DebugSlotsEditor
from .icons import WINDOW_CLASS, apply_window_icon
from .launcher import FINAL, Launcher
from .models import validate_profile, validate_target
from .ssh import UnknownHost, error_text, inspect_host, preflight, trust_host
from .storage import Store, read_json
from .terminal import check_terminal, target_key
from .settings import connection_options, debug_slots, debug_preferences

STATE_NAMES = {"waiting": "等待", "opening": "打开终端", "connecting": "连接中", "initializing": "初始化中",
               "running": "执行中", "succeeded": "成功结束", "failed": "失败", "disconnected": "连接断开",
               "unknown": "状态未知", "cancelled": "已取消", "closing": "关闭中", "closed": "已关闭", "interactive": "终端已连接", "detached": "已断开（可重连）"}


def example_path():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    source = root / "examples" / "rdkx5.json"
    if source.is_file(): return source
    return Path(sys.prefix) / "share" / "ssh-multi-tool" / "examples" / "rdkx5.json"


class App(ttk.Frame):
    def __init__(self, root, store=None):
        super().__init__(root, padding=18)
        self.root = root; self.store = store or Store(); self.closed = False; self.restoring = True
        self.work_queue = queue.Queue(maxsize=32); self.busy = False; self.last_states = {}
        self.launcher = Launcher(self.store.known_hosts)
        self.targets = []; self.profiles = []; self.target_id = None
        self.target_choice = tk.StringVar(); self.profile_choice = tk.StringVar()
        self.target_vars = {key: tk.StringVar(value="22" if key == "port" else "")
                            for key in ("name", "host", "port", "username")}
        self.password = tk.StringVar(); self.show_password = tk.BooleanVar(); self.status = tk.StringVar(value="就绪")
        self.summary = tk.StringVar(value="选择或新建启动配置")
        self.debug_slots = debug_slots({})
        self.keep_alive = tk.BooleanVar(value=False)
        self._build()
        self._window_icons = apply_window_icon(self.root)
        try: self.preferences = self.store.preferences()
        except (ValueError, OSError) as exc: self.preferences = {}; self.log(f"偏好恢复失败：{exc}")
        self.debug_slots = debug_slots(self.preferences)
        self.keep_alive.set(self.preferences.get("keep_alive", "0") == "1")
        self.launcher.keep_alive = self.keep_alive.get()
        try: self.launcher.connect_options = connection_options(self.preferences)
        except ValueError as exc:
            self.preferences.pop("connect_timeout", None); self.preferences.pop("auth_timeout", None)
            self.log(str(exc)); self.launcher.connect_options = connection_options({})
        try: self.targets = self.store.targets()
        except (ValueError, OSError) as exc: self.log(f"目标恢复失败：{exc}")
        self.refresh_targets(self.preferences.get("last_target_id"))
        self.refresh_profiles(self.preferences.get("last_profile_id"))
        geometry = self.preferences.get("geometry")
        if geometry:
            try: self.root.geometry(geometry)
            except tk.TclError: pass
        self.restoring = False
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)
        if not sys.platform.startswith("linux"):
            self.log("当前平台支持配置编辑和连接测试；多终端启动请在Ubuntu桌面使用。")

    def _build(self):
        self.root.title('SSH Multi Terminal'); self.root.geometry("1020x780"); self.root.minsize(880, 680)
        self.pack(fill="both", expand=True); self.columnconfigure(0, weight=1); self.rowconfigure(3, weight=1)
        style = ttk.Style(self.root)
        if "clam" in style.theme_names(): style.theme_use("clam")
        style.configure("Title.TLabel", font=("TkDefaultFont", 19, "bold"))
        style.configure("Treeview", rowheight=30)
        style.configure("Launch.TButton", font=("TkDefaultFont", 12, "bold"), padding=(16, 10))
        title = ttk.Frame(self); title.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        ttk.Label(title, text="SSH 多终端启动", style="Title.TLabel").pack(side="left")
        ttk.Label(title, text="环境配置一次填写 · 多窗口一键运行").pack(side="right")
        connection = ttk.LabelFrame(self, text="目标设备", padding=12)
        connection.grid(row=1, column=0, sticky="ew", pady=(0, 12)); connection.columnconfigure(1, weight=1)
        connection.columnconfigure(3, weight=1)
        ttk.Label(connection, text="已保存目标").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.target_combo = ttk.Combobox(connection, textvariable=self.target_choice, state="readonly")
        self.target_combo.grid(row=0, column=1, columnspan=3, sticky="ew")
        self.target_combo.bind("<<ComboboxSelected>>", lambda event: self._target_selected())
        toolbar = ttk.Frame(connection); toolbar.grid(row=0, column=4, padx=(12, 0))
        for text, command in [("新目标", self.new_target), ("保存", self.save_target), ("删除", self.delete_target)]:
            ttk.Button(toolbar, text=text, command=command).pack(side="left", padx=2)
        fields = [("名称", "name", 1, 0), ("IP / 主机名", "host", 1, 2),
                  ("SSH 端口", "port", 2, 0), ("用户名", "username", 2, 2)]
        for label, key, row, col in fields:
            ttk.Label(connection, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=6)
            ttk.Entry(connection, textvariable=self.target_vars[key]).grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=6)
        ttk.Label(connection, text="密码").grid(row=3, column=0, sticky="w")
        self.password_entry = ttk.Entry(connection, textvariable=self.password, show="•")
        self.password_entry.grid(row=3, column=1, sticky="ew", padx=(0, 12), pady=6)
        ttk.Checkbutton(connection, text="显示密码", variable=self.show_password,
                        command=lambda: self.password_entry.configure(show="" if self.show_password.get() else "•")).grid(row=3, column=2, sticky="w")
        ttk.Label(connection, text="仅本次使用，不写入配置").grid(row=3, column=3, sticky="w")
        self.test_button = ttk.Button(connection, text="测试连接", command=lambda: self.connection(False))
        self.test_button.grid(row=1, column=4, sticky="ew")
        ttk.Button(connection, text="主机信任管理", command=self.manage_trust).grid(row=2, column=4, sticky="ew")
        ttk.Button(connection, text="高级设置", command=self.advanced_settings).grid(row=3, column=4, sticky="ew")
        config = ttk.LabelFrame(self, text="启动配置", padding=12)
        config.grid(row=2, column=0, sticky="ew", pady=(0, 12)); config.columnconfigure(0, weight=1)
        self.profile_combo = ttk.Combobox(config, textvariable=self.profile_choice, state="readonly")
        self.profile_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", lambda event: self._profile_selected())
        buttons = ttk.Frame(config); buttons.grid(row=0, column=1)
        for text, command in [("新建", lambda: self.edit_profile(new=True)), ("编辑", self.edit_profile),
                              ("复制", self.copy_profile), ("删除", self.delete_profile),
                              ("导入", self.import_profile), ("导出", self.export_profile)]:
            ttk.Button(buttons, text=text, command=command, width=6).pack(side="left", padx=2)
        ttk.Label(config, textvariable=self.summary, wraplength=950).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        debug_toolbar = ttk.Frame(config); debug_toolbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(debug_toolbar, text="特别调试窗口").pack(side="left", padx=(0, 10))
        self.debug_buttons = []
        for index, slot in enumerate(self.debug_slots):
            button = ttk.Button(debug_toolbar, text=f"{index + 1} · {slot['name']}",
                                command=lambda selected=index: self.open_debug_terminal(selected))
            button.pack(side="left", padx=3); self.debug_buttons.append(button)
            menu = tk.Menu(button, tearoff=False)
            menu.add_command(label="新开会话", command=lambda selected=index: self.open_debug_terminal(selected, force_new=True))
            button.bind("<Button-3>", lambda event, popup=menu: popup.tk_popup(event.x_root, event.y_root))
        ttk.Button(debug_toolbar, text="配置调试窗口", command=self.edit_debug_slots).pack(side="right")
        middle = ttk.Frame(self); middle.grid(row=3, column=0, sticky="nsew")
        middle.columnconfigure(0, weight=1); middle.rowconfigure(0, weight=3); middle.rowconfigure(3, weight=1)
        self.window_tabs = ttk.Notebook(middle)
        self.window_tabs.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.config_page = ttk.Frame(self.window_tabs)
        self.manual_page = ttk.Frame(self.window_tabs)
        self.window_tabs.add(self.config_page, text="配置窗口")
        self.window_tabs.add(self.manual_page, text="手动终端")
        for page in (self.config_page, self.manual_page):
            page.columnconfigure(0, weight=1); page.rowconfigure(0, weight=1)
        self.window_tabs.bind("<<NotebookTabChanged>>", lambda event: self._update_window_controls())
        self.table = ttk.Treeview(self.config_page, columns=("name", "directory", "delay", "state"), show="headings", height=7, selectmode="browse")
        for column, label, width in [("name", "窗口", 170), ("directory", "远端工作目录", 390), ("delay", "延迟秒数", 90), ("state", "状态", 130)]:
            self.table.heading(column, text=label); self.table.column(column, width=width, minwidth=60, stretch=column in {"name", "directory"})
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.config_page, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns"); self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<Double-1>", lambda event: self.edit_profile())
        self.table.bind("<<TreeviewSelect>>", lambda event: self._update_window_controls())
        self.manual_table = ttk.Treeview(self.manual_page, columns=("name", "directory", "state"),
                                        show="headings", height=7, selectmode="browse")
        for column, label, width in [("name", "目标 / 终端", 320), ("directory", "初始目录", 320), ("state", "状态", 150)]:
            self.manual_table.heading(column, text=label)
            self.manual_table.column(column, width=width, minwidth=60, stretch=column != "state")
        self.manual_table.grid(row=0, column=0, sticky="nsew")
        manual_scrollbar = ttk.Scrollbar(self.manual_page, orient="vertical", command=self.manual_table.yview)
        manual_scrollbar.grid(row=0, column=1, sticky="ns")
        self.manual_table.configure(yscrollcommand=manual_scrollbar.set)
        self.manual_table.bind("<<TreeviewSelect>>", lambda event: self._update_window_controls())
        window_toolbar = ttk.Frame(middle); window_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 5))
        ttk.Label(window_toolbar, text="会话操作").pack(side="left")
        self.reconnect_window_button = ttk.Button(window_toolbar, text="重新连接", command=lambda: self.control_persistent_window(False), state="disabled")
        self.reconnect_window_button.pack(side="left", padx=3)
        self.stop_window_button = ttk.Button(window_toolbar, text="停止程序", command=lambda: self.control_persistent_window(True), state="disabled")
        self.stop_window_button.pack(side="left", padx=3)
        ttk.Checkbutton(window_toolbar, text="断线保活（tmux）", variable=self.keep_alive,
                        command=self.save_keep_alive).pack(side="left", padx=8)
        self.remote_terminal_button = ttk.Button(window_toolbar, text="打开远程终端", command=self.open_remote_terminal)
        self.remote_terminal_button.pack(side="right")
        self.start_window_button = ttk.Button(window_toolbar, text="启动所选窗口", command=self.start_selected_window, state="disabled")
        self.start_window_button.pack(side="right", padx=(0, 6))
        tmux_toolbar = ttk.Frame(middle)
        tmux_toolbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        ttk.Label(tmux_toolbar, text="tmux 批量操作").pack(side="left", padx=(0, 8))
        self.reconnect_tmux_all_button = ttk.Button(tmux_toolbar, text="一键重连 tmux",
            command=lambda: self.control_tmux_all(False), state="disabled")
        self.reconnect_tmux_all_button.pack(side="left", padx=3)
        self.stop_tmux_all_button = ttk.Button(tmux_toolbar, text="一键关闭 tmux",
            command=lambda: self.control_tmux_all(True), state="disabled")
        self.stop_tmux_all_button.pack(side="left", padx=3)
        ttk.Label(tmux_toolbar, text="覆盖本轮管理的所有目标；关闭将停止远端程序").pack(side="left", padx=10)
        self.events_list = tk.Listbox(middle, height=4, borderwidth=1, relief="solid", exportselection=False)
        self.events_list.grid(row=3, column=0, columnspan=2, sticky="nsew")
        footer = ttk.Frame(self); footer.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(footer, text="导入示例", command=lambda: self.import_profile(example_path())).pack(side="left")
        ttk.Label(footer, textvariable=self.status).pack(side="left", padx=12)
        self.launch_button = ttk.Button(footer, text="一键启动", style="Launch.TButton", command=lambda: self.connection(True))
        self.launch_button.pack(side="right")
        self.close_all_button = ttk.Button(footer, text="一键断开", command=self.close_all_windows, state="disabled")
        self.close_all_button.pack(side="right", padx=5)
        self.restart_window_button = ttk.Button(footer, text="重启会话", command=self.restart_selected_window, state="disabled")
        self.restart_window_button.pack(side="right", padx=5)
        self.close_window_button = ttk.Button(footer, text="关闭会话", command=self.close_selected_window, state="disabled")
        self.close_window_button.pack(side="right", padx=5)

    def log(self, text):
        self.events_list.insert("end", text)
        if self.events_list.size() > 200: self.events_list.delete(0)
        self.events_list.see("end"); self.status.set(text[:75])

    @staticmethod
    def _labels(items): return [f"{item['name']}  ·  {item['id'][:8]}" for item in items]

    def refresh_targets(self, selected=None):
        self.target_combo["values"] = self._labels(self.targets)
        index = next((i for i, item in enumerate(self.targets) if item["id"] == selected), 0)
        if self.targets:
            self.target_combo.current(index); self._target_selected()
        else: self.new_target()

    def _target_selected(self):
        index = self.target_combo.current()
        if index < 0: return
        target = self.targets[index]
        if self.target_id != target["id"]: self.password.set("")
        self.target_id = target["id"]
        for key, variable in self.target_vars.items(): variable.set(str(target[key]))
        self._save_preferences()

    def new_target(self):
        self.target_id = uuid.uuid4().hex; self.target_choice.set("")
        for key, variable in self.target_vars.items(): variable.set("22" if key == "port" else "")
        self.password.set("")

    def target(self):
        data = {key: variable.get().strip() for key, variable in self.target_vars.items()}
        try: data["port"] = int(data["port"])
        except ValueError: raise ValueError("SSH端口必须是整数")
        return validate_target(dict(data, id=self.target_id or uuid.uuid4().hex))

    def save_target(self, quiet=False):
        try:
            target = self.target(); items = [item for item in self.targets if item["id"] != target["id"]] + [target]
            self.store.save_targets(items); self.targets = items; self.refresh_targets(target["id"])
            if not quiet: self.log("目标已保存，密码未写入文件")
            return target
        except (ValueError, OSError) as exc:
            if quiet: raise
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root)

    def delete_target(self):
        if not any(item["id"] == self.target_id for item in self.targets): return
        if not messagebox.askyesno('Confirm - SSH Multi Terminal', "删除此目标的本地记录？", parent=self.root): return
        try:
            self.store.save_targets([item for item in self.targets if item["id"] != self.target_id])
            self.targets = self.store.targets(); self.refresh_targets(); self.password.set("")
        except (ValueError, OSError) as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root)

    def selected_profile(self):
        index = self.profile_combo.current()
        return self.profiles[index] if 0 <= index < len(self.profiles) else None

    def refresh_profiles(self, selected=None):
        self.profiles, errors = self.store.list_profiles()
        self.profile_combo["values"] = self._labels(self.profiles)
        for error in errors: self.log("跳过无效配置：" + error)
        if self.profiles:
            index = next((i for i, item in enumerate(self.profiles) if item["id"] == selected), 0)
            self.profile_combo.current(index)
        else: self.profile_choice.set("")
        self._profile_selected()

    def _profile_selected(self):
        self.table.delete(*self.table.get_children()); profile = self.selected_profile()
        if profile:
            enabled = sum(window["enabled"] for window in profile["windows"])
            lines = len(profile["common_init"].splitlines())
            self.summary.set(f"{enabled} 个启用窗口 · 公共初始化 {lines} 行 · {profile['description']}")
            for window in profile["windows"]:
                self.table.insert("", "end", iid=window["id"], values=(window["name"], window["working_directory"] or "继承初始化目录",
                                  window["start_delay_seconds"], "待启动" if window["enabled"] else "已禁用"))
        else: self.summary.set("尚无配置。可新建配置或导入示例。")
        self._sync_remote_windows(self.launcher.poll())
        self._update_window_controls()
        if hasattr(self, "preferences"): self._save_preferences()

    def save_keep_alive(self):
        updated = dict(self.preferences, keep_alive="1" if self.keep_alive.get() else "0")
        try: self.store.save_preferences(updated)
        except (ValueError, OSError) as exc:
            self.keep_alive.set(self.launcher.keep_alive); self.log(f"偏好保存失败：{exc}"); return
        self.preferences = updated; self.launcher.keep_alive = self.keep_alive.get()
        self.log("断线保活已启用，新窗口将在远端tmux中运行" if self.keep_alive.get() else "断线保活已关闭，仅影响后续新窗口")

    def _save_preferences(self):
        if self.restoring or not hasattr(self, "preferences"): return
        profile = self.selected_profile()
        self.preferences.update(last_target_id=self.target_id or "", last_profile_id=profile["id"] if profile else "",
                                geometry=self.root.geometry(), terminal="gnome-terminal")
        try: self.store.save_preferences(self.preferences)
        except (ValueError, OSError) as exc: self.log(f"偏好保存失败：{exc}")

    def _profile_saved(self, profile):
        self.store.save_profile(profile); self.refresh_profiles(profile["id"]); self.log("配置已保存")

    def edit_profile(self, new=False):
        profile = None if new else self.selected_profile()
        if not new and profile is None: return
        ProfileEditor(self.root, profile, self._profile_saved)

    def copy_profile(self):
        profile = self.selected_profile()
        if profile:
            copied = copy.deepcopy(profile); copied["id"] = uuid.uuid4().hex; copied["name"] += "（副本）"
            ProfileEditor(self.root, copied, self._profile_saved)

    def delete_profile(self):
        profile = self.selected_profile()
        if profile and messagebox.askyesno('Confirm - SSH Multi Terminal', f"删除配置“{profile['name']}”？已运行的终端继续执行。", parent=self.root):
            try: self.store.delete_profile(profile["id"]); self.refresh_profiles()
            except OSError as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root)

    def import_profile(self, path=None):
        path = path or filedialog.askopenfilename(parent=self.root, title='Import Profile', filetypes=[("JSON配置", "*.json")])
        if not path: return
        try:
            profile = validate_profile(read_json(Path(path)))
            if any(item["id"] == profile["id"] for item in self.profiles):
                profile["id"] = uuid.uuid4().hex; profile["name"] += "（导入副本）"
            self.log("请检查导入的全部命令，保存后才能选择启动；导入本身不执行命令")
            ProfileEditor(self.root, profile, self._profile_saved)
        except (ValueError, OSError) as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root)

    def export_profile(self):
        profile = self.selected_profile()
        if not profile: return
        if not messagebox.askokcancel('Confirm - SSH Multi Terminal', "请检查自己填写的命令中是否含密码、令牌等秘密，再分享配置。", parent=self.root): return
        path = filedialog.asksaveasfilename(parent=self.root, title='Export Profile', defaultextension=".json", initialfile=profile["id"] + ".json")
        if path:
            try: self.store.export_profile(profile["id"], path); self.log("配置已导出")
            except (ValueError, OSError) as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root)

    def _work(self, function, success):
        if self.busy: return
        self.busy = True; self.launch_button.state(["disabled"]); self.test_button.state(["disabled"])
        self._update_window_controls()
        operation_epoch = self.launcher.control_epoch
        def worker():
            try: result = function(); item = (True, result, function, success, operation_epoch)
            except Exception as exc: item = (False, exc, function, success, operation_epoch)
            if not self.closed: self.work_queue.put(item)
        threading.Thread(target=worker, daemon=True).start()

    def connection(self, start):
        if self.busy: return
        try:
            if start: check_terminal()
            target = self.save_target(quiet=True); password = self.password.get()
            if not password: raise ValueError("请输入SSH密码，密码仅在本次使用期间保留")
            profile = self.selected_profile()
            if start and profile is None: raise ValueError("请先新建或导入启动配置")
            if start and any(task["state"] == "unknown" for task in self.launcher.tasks.values()):
                if not messagebox.askyesno('Confirm - SSH Multi Terminal', "旧窗口状态无法确认，远端程序可能仍在运行。确认再次启动？", parent=self.root): return
        except (ValueError, OSError, RuntimeError) as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        self.log("正在验证SSH连接……")
        if start:
            operation_epoch = self.launcher.control_epoch
            self._work(lambda: self.launcher.start(target, password, profile, expected_epoch=operation_epoch), lambda result: self.log("启动已调度；各窗口独立执行"))
        else:
            options = dict(self.launcher.connect_options)
            self._work(lambda: preflight(target, password, self.store.known_hosts, **options), lambda result: self.log("SSH连接及密码验证成功"))

    def _refresh_debug_buttons(self):
        for index, (button, slot) in enumerate(zip(self.debug_buttons, self.debug_slots)):
            button.configure(text=f"{index + 1} · {slot['name'][:14]}")
            button.state(["!disabled"] if slot["commands"].strip() and self.selected_profile() and not self.busy else ["disabled"])

    def save_debug_slots(self, slots):
        updated = dict(self.preferences, **debug_preferences(slots))
        self.store.save_preferences(updated)
        self.preferences = updated; self.debug_slots = debug_slots(updated)
        self._refresh_debug_buttons(); self.log("四个特别调试槽位已保存，所有目标共用")

    def edit_debug_slots(self):
        DebugSlotsEditor(self.root, self.debug_slots, self.save_debug_slots)

    def _manual_window_opened(self, identifier, message):
        self._sync_remote_windows(self.launcher.poll())
        self.window_tabs.select(self.manual_page)
        if self.manual_table.exists(identifier):
            self.manual_table.selection_set(identifier); self.manual_table.see(identifier)
            self.log(message)
        else: self.log("远程终端已关闭或未能打开")

    def open_debug_terminal(self, index, force_new=False):
        if self.busy: return
        try:
            slot = dict(self.debug_slots[index]); profile = self.selected_profile()
            if not slot["commands"].strip(): raise ValueError("请先配置该调试槽位的命令")
            if profile is None: raise ValueError("请选择启动配置，以继承公共初始化")
            check_terminal()
            target = self.save_target(quiet=True); password = self.password.get()
            existing = None if force_new else self.launcher.find_debug_terminal(target, slot, index)
            if not password and existing is None: raise ValueError("请输入SSH密码")
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        operation_epoch = self.launcher.control_epoch
        self.log(f"正在打开调试会话：{slot['name']}……")
        def opened(identifier):
            self._manual_window_opened(identifier, "已复用已有调试会话" if existing == identifier else "特别调试窗口已打开")
        def open_or_reuse():
            identifier = self.launcher.open_debug_terminal(target, password, profile, slot, slot_index=index,
                                                          force_new=force_new, expected_epoch=operation_epoch)
            if existing == identifier:
                focused = self.launcher.focus_window(identifier)
                return identifier, focused
            return identifier, True
        def complete(result):
            identifier, focused = result; opened(identifier)
            if not focused: self.log("已复用并选中会话；桌面未允许自动聚焦，请在开发板终端切换到对应标签页")
        self._work(open_or_reuse, complete)

    def open_remote_terminal(self):
        if self.busy: return
        try:
            check_terminal()
            target = self.save_target(quiet=True); password = self.password.get()
            if not password: raise ValueError("请输入SSH密码，密码仅在本次使用期间保留")
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        self.log("正在验证SSH连接并打开远程终端……")
        operation_epoch = self.launcher.control_epoch
        def opened(identifier):
            self._manual_window_opened(identifier, "远程终端已打开，可在新窗口中手动输入命令")
        profile = self.selected_profile()
        self._work(lambda: self.launcher.open_remote_terminal(target, password, profile=profile, expected_epoch=operation_epoch), opened)

    def _sync_remote_windows(self, tasks):
        live = {identifier: task for identifier, task in tasks.items() if task.get("mode") == "shell"}
        previous = set(self.manual_table.get_children())
        for identifier in previous - live.keys(): self.manual_table.delete(identifier)
        for key in list(self.last_states):
            if key[1] in previous and (key[1] not in live or key[0] != live[key[1]].get("session_id")):
                del self.last_states[key]
        for identifier, task in live.items():
            state = STATE_NAMES.get(task["state"], task["state"])
            if task.get("exit_code") is not None: state += f" ({task['exit_code']})"
            values = (task["name"], "继承公共初始化目录" if task.get("inherits_init") else "远端登录目录", state)
            if self.manual_table.exists(identifier): self.manual_table.item(identifier, values=values)
            else: self.manual_table.insert("", "end", iid=identifier, values=values)

    def _selected_config_window(self):
        if self.window_tabs.select() != str(self.config_page): return None
        selected = self.table.selection(); profile = self.selected_profile()
        if selected and profile:
            return next((w for w in profile["windows"] if w["id"] == selected[0]), None)
        return None

    def start_selected_window(self):
        if self.busy: return
        try:
            profile = self.selected_profile(); window = self._selected_config_window()
            if not window or not window["enabled"]: raise ValueError("请选择当前配置中启用的窗口")
            check_terminal()
            target = self.save_target(quiet=True); password = self.password.get()
            if not password: raise ValueError("请输入SSH密码")
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        identifier = window["id"]; operation_epoch = self.launcher.control_epoch
        def started(result):
            current = self.selected_profile()
            if current and current["id"] == profile["id"] and self.table.exists(identifier):
                self.table.selection_set(identifier); self.table.see(identifier)
            self.log("所选窗口已启动；其他窗口继续运行")
        self.log("正在验证SSH连接并启动所选窗口……")
        self._work(lambda: self.launcher.start_window(target, password, profile, identifier,
                                                     expected_epoch=operation_epoch), started)

    def _selected_run_window(self):
        if self.window_tabs.select() == str(self.manual_page):
            selected = self.manual_table.selection()
            return selected[0] if selected and selected[0] in self.launcher.shell_tasks else None
        profile = self.selected_profile(); selected = self.table.selection()
        if profile and profile["id"] == self.launcher.profile_id and selected and selected[0] in self.launcher.tasks:
            return selected[0]
        return None

    def _update_window_controls(self):
        self._refresh_debug_buttons()
        identifier = self._selected_run_window()
        tasks = self.launcher.poll()
        self._sync_remote_windows(tasks)
        state = tasks.get(identifier, {}).get("state") if identifier else None
        window = self._selected_config_window(); profile = self.selected_profile()
        current_task = tasks.get(window["id"]) if window and profile and profile["id"] == self.launcher.profile_id else None
        can_start = bool(window and window["enabled"] and not self.busy and
                         (not current_task or (current_task["state"] in FINAL and (not current_task.get("tmux_session") or current_task.get("stopped")))))
        if self.launcher.active() and (not profile or profile["id"] != self.launcher.profile_id or self.target_id != self.launcher.target_id):
            can_start = False
        self.start_window_button.state(["!disabled"] if can_start else ["disabled"])
        self.remote_terminal_button.state(["disabled"] if self.busy else ["!disabled"])
        self.close_window_button.state(["!disabled"] if state and state not in {"closing", "closed", "detached"} else ["disabled"])
        persistent = bool(tasks.get(identifier, {}).get("tmux_session"))
        self.close_window_button.configure(text="断开窗口" if persistent else "关闭会话")
        self.reconnect_window_button.state(["!disabled"] if persistent and not tasks.get(identifier, {}).get("stopped") and state in FINAL | {"unknown"} and not self.busy else ["disabled"])
        self.stop_window_button.state(["!disabled"] if persistent and state != "closing" and not self.busy and not tasks.get(identifier, {}).get("stopped") else ["disabled"])
        self.restart_window_button.state(["!disabled"] if identifier and state and state != "closing" and not self.busy else ["disabled"])
        self.reconnect_tmux_all_button.state(["!disabled"] if not self.busy and self.launcher.tmux_windows(reconnect_only=True) else ["disabled"])
        self.stop_tmux_all_button.state(["!disabled"] if not self.busy and self.launcher.tmux_windows() else ["disabled"])
        self.close_all_button.state(["!disabled"] if self.busy or self.launcher.all_tasks() else ["disabled"])

    def close_selected_window(self):
        identifier = self._selected_run_window()
        if identifier is None: return
        self.launcher.close_window(identifier)
        self.log("已请求断开所选窗口，tmux会话保留" if self.launcher.all_tasks()[identifier].get("tmux_session") else "已请求关闭所选窗口")
        self._update_window_controls()

    def close_all_windows(self):
        self.launcher.close_all()
        self.log("已请求断开全部窗口，并取消延迟启动任务；tmux保活程序继续运行")
        self._update_window_controls()

    def _tmux_passwords(self, windows):
        targets = {target_key(entry["target"]): entry["target"] for entry in windows.values()}
        try: current = target_key(self.target())
        except ValueError: current = None
        passwords = {}
        for key, target in targets.items():
            password = self.password.get() if key == current else ""
            if not password:
                password = simpledialog.askstring('TMUX Password',
                    f"目标：{target['name']}\n{target['username']}@{target['host']}:{target['port']}\n\n请输入SSH密码（取消或留空将跳过此目标）：",
                    show="*", parent=self.root)
            if password: passwords[key] = password
        return passwords

    def control_tmux_all(self, stopping):
        if self.busy: return
        windows = self.launcher.tmux_windows(reconnect_only=not stopping)
        if not windows: self.log("没有需要处理的tmux会话"); return
        try:
            if not stopping: check_terminal()
        except (OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        passwords = self._tmux_passwords(windows)
        epoch = self.launcher.control_epoch
        self.log("正在批量停止远端tmux会话……" if stopping else "正在尝试重连所有已断开的tmux会话……")
        def work():
            try: return self.launcher.control_tmux_all(passwords, stopping=stopping, expected_epoch=epoch)
            finally: passwords.clear()
        def complete(report):
            for entry in report["failed"].values():
                target = entry["target"]
                self.log(f"{entry['name']}（{target['username']}@{target['host']}:{target['port']}）：{entry['error']}")
            if stopping:
                self.log(f"tmux批量关闭：已停止 {len(report['stopped'])}，失败 {len(report['failed'])}；失败项保留，可稍后重试")
            else:
                self.log(f"tmux批量重连：已发起 {len(report['requested'])}，跳过 {len(report['skipped'])}，失败 {len(report['failed'])}；连接结果见窗口状态")
            self._update_window_controls()
        self._work(work, complete)

    def control_persistent_window(self, stopping):
        identifier = self._selected_run_window()
        if identifier is None or self.busy: return
        try:
            spec = self.launcher.shell_specs.get(identifier) or self.launcher.window_specs[identifier]
            if target_key(self.target()) != target_key(spec["target"]): raise ValueError("请先切回该窗口的目标并输入其SSH密码")
            password = self.password.get()
            if not password: raise ValueError("请输入该目标的SSH密码")
            if not stopping: check_terminal()
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        epoch = self.launcher.control_epoch
        action = self.launcher.stop_window if stopping else self.launcher.reconnect_window
        self.log("正在停止远端tmux会话……" if stopping else "正在重新连接tmux会话……")
        self._work(lambda: action(identifier, password, expected_epoch=epoch),
                   lambda result: self.log("远端tmux会话已停止" if stopping else "已打开重连终端，复用原tmux会话"))

    def restart_selected_window(self):
        identifier = self._selected_run_window()
        if identifier is None or self.busy: return
        try:
            shell_spec = self.launcher.shell_specs.get(identifier)
            target_id = shell_spec["target"]["id"] if shell_spec else self.launcher.target_id
            if self.target_id != target_id: raise ValueError("请先切回该窗口的目标并输入其SSH密码")
            password = self.password.get()
            if not password: raise ValueError("请输入本轮目标的SSH密码")
            check_terminal()
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        self.log("正在验证连接并重启所选窗口……")
        operation_epoch = self.launcher.control_epoch
        self._work(lambda: self.launcher.restart_window(identifier, password, expected_epoch=operation_epoch), lambda result: self.log("所选窗口已重新启动"))
        self._update_window_controls()

    def advanced_settings(self):
        if self.busy or self.launcher.active(): return
        dialog = tk.Toplevel(self.root); dialog.title('Connection Settings'); dialog.transient(self.root); dialog.grab_set()
        body = ttk.Frame(dialog, padding=18); body.pack(fill="both", expand=True)
        variables = {}
        for row, (key, label, default) in enumerate([("connect_timeout", "连接超时秒数", "10"), ("auth_timeout", "认证超时秒数", "15")]):
            variable = tk.StringVar(value=self.preferences.get(key, default)); variables[key] = variable
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Spinbox(body, from_=1, to=120, textvariable=variable, width=12).grid(row=row, column=1, pady=5)
        ttk.Label(body, text="SSH保活15秒；常驻任务没有统一运行时限。").grid(row=2, column=0, columnspan=2, pady=12)
        def save():
            try:
                values = {key: variable.get() for key, variable in variables.items()}
                options = connection_options(values)
                self.preferences.update(values); self._save_preferences(); self.launcher.connect_options = options
                dialog.destroy(); self.log("连接设置已保存")
            except ValueError as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=dialog)
        ttk.Button(body, text="保存", command=save).grid(row=3, column=1, sticky="e")

    def manage_trust(self):
        if self.busy: return
        try: target = self.target()
        except ValueError as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        self.log("读取服务器指纹……")
        def confirm(found):
            if messagebox.askyesno('Confirm - SSH Multi Terminal', f"目标：{found.hostname}\n指纹：{found.fingerprint}\n\n请通过可信渠道核对。确认后保存此指纹；同类型旧指纹将被替换。", parent=self.root):
                try: trust_host(self.store.known_hosts, found); self.log("主机指纹已显式更新")
                except OSError as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root)
        self._work(lambda: inspect_host(target), confirm)

    def _poll(self):
        if self.closed: return
        while True:
            try: ok, result, function, success, operation_epoch = self.work_queue.get_nowait()
            except queue.Empty: break
            self.busy = False; self.launch_button.state(["!disabled"]); self.test_button.state(["!disabled"])
            if operation_epoch != self.launcher.control_epoch:
                self.log("操作已被一键断开取消"); continue
            if ok: success(result)
            elif isinstance(result, UnknownHost):
                if messagebox.askyesno('Confirm - SSH Multi Terminal', f"主机：{result.hostname}\n指纹：{result.fingerprint}\n\n请核对设备指纹，是否信任并继续？", parent=self.root):
                    try: trust_host(self.store.known_hosts, result); self._work(function, success)
                    except OSError as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root)
                else: self.log("已取消连接，主机未被信任")
            else:
                self.log(error_text(result)); messagebox.showerror('Error - SSH Multi Terminal', error_text(result), parent=self.root)
        tasks = self.launcher.poll()
        self._sync_remote_windows(tasks)
        profile = self.selected_profile()
        for identifier, task in tasks.items():
            state = STATE_NAMES.get(task["state"], task["state"])
            if task.get("exit_code") is not None: state += f" ({task['exit_code']})"
            if task.get("mode") != "shell" and self.table.exists(identifier) and profile and profile["id"] == self.launcher.profile_id:
                self.table.set(identifier, "state", state)
            signature = (task["state"], task.get("exit_code"), task.get("error"))
            session_id = task.get("session_id", self.launcher.run_id)
            if self.last_states.get((session_id, identifier)) != signature:
                self.last_states[(session_id, identifier)] = signature
                self.log(f"{task['name']}：{state}" + (" · " + task["error"] if task.get("error") else ""))
        self.launch_button.state(["disabled"] if self.busy or self.launcher.active() else ["!disabled"])
        self._update_window_controls()
        self.root.after(100, self._poll)

    def close(self):
        if self.launcher.has_pending() or self.busy:
            if not messagebox.askyesno('Confirm - SSH Multi Terminal', "退出将取消尚未启动的任务。已启动的终端继续运行。是否退出？", parent=self.root): return
        self._save_preferences(); self.closed = True; self.password.set(""); self.launcher.close(); self.root.destroy()


def main():
    root = tk.Tk(className=WINDOW_CLASS); App(root); root.mainloop()
