"""Desktop application; workers communicate with Tk via a bounded queue."""
import copy
import queue
import sys
import threading
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .ui import apply_theme, style_table, style_text, row_tags
from .editor import ProfileEditor
from .debug_editor import DebugSlotsEditor
from .icons import WINDOW_CLASS, apply_window_icon
from .launcher import FINAL, Launcher
from .models import validate_profile, validate_target, is_local, local_target
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
        self.task_checks = {}
        self.all_tasks_selected = tk.BooleanVar(value=False)
        self.task_selection_summary = tk.StringVar()
        self.target_choice = tk.StringVar(); self.profile_choice = tk.StringVar()
        self.execution_mode = tk.StringVar(value="remote")
        self.target_vars = {key: tk.StringVar(value="22" if key == "port" else "")
                            for key in ("name", "host", "port", "username")}
        self.password = tk.StringVar(); self.show_password = tk.BooleanVar(); self.status = tk.StringVar(value="就绪")
        self.summary = tk.StringVar(value="选择或新建启动配置")
        self.debug_slots = debug_slots({})
        self.keep_alive = tk.BooleanVar(value=False)
        self._build()
        self._window_icons = apply_window_icon(self.root)
        self.brand_label.configure(image=self._window_icons[1])
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
        self.execution_mode.set(self.preferences.get("execution_mode", "remote"))
        self._execution_mode_changed()
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
        self.root.title('SSH Multi Terminal'); self.root.geometry('1160x780'); self.root.minsize(1040, 720)
        self.colors = apply_theme(self.root)
        self.configure(style='App.TFrame', padding=20)
        self.pack(fill='both', expand=True); self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, style='App.TFrame'); header.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        self.brand_label = ttk.Label(header, style='Subtitle.TLabel'); self.brand_label.pack(side='left', padx=(0, 10))
        ttk.Label(header, text='SSH 多终端启动', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='连接开发板  /  配置环境  /  管理会话', style='Subtitle.TLabel').pack(side='right')
        body = ttk.Frame(self, style='App.TFrame'); body.grid(row=1, column=0, sticky='nsew')
        body.columnconfigure(1, weight=1); body.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(body, style='App.TFrame', width=304)
        sidebar.grid(row=0, column=0, sticky='ns', padx=(0, 16)); sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        self._build_connection(sidebar)
        self._build_debug(sidebar)
        workspace = ttk.Frame(body, style='App.TFrame'); workspace.grid(row=0, column=1, sticky='nsew')
        workspace.columnconfigure(0, weight=1); workspace.rowconfigure(1, weight=1)
        self._build_profile(workspace)
        self._build_sessions(workspace)
        self._build_log(workspace)
        footer = ttk.Frame(self, style='App.TFrame'); footer.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        ttk.Label(footer, textvariable=self.status, style='Subtitle.TLabel', wraplength=1000).pack(side='left')

    def _build_connection(self, sidebar):
        card = ttk.Frame(sidebar, style='Card.TFrame', padding=14)
        card.grid(row=0, column=0, sticky='ew'); card.columnconfigure(0, weight=1)
        ttk.Label(card, text='启动位置', style='Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 10))
        modes = ttk.Frame(card); modes.grid(row=1, column=0, sticky='ew', pady=(0, 10))
        for label, value in (('远程 SSH', 'remote'), ('本机', 'local')):
            ttk.Radiobutton(modes, text=label, variable=self.execution_mode, value=value,
                            command=self._execution_mode_changed).pack(side='left', padx=(0, 16))
        self.local_hint = ttk.Label(card, text='在当前电脑执行配置命令\n无需 SSH 服务或密码\n工作目录和依赖使用本机环境',
                                    style='Muted.TLabel', wraplength=260)
        self.local_hint.grid(row=2, column=0, sticky='w', pady=(0, 8)); self.local_hint.grid_remove()
        self.remote_fields = ttk.Frame(card); self.remote_fields.grid(row=3, column=0, sticky='ew')
        card = self.remote_fields; card.columnconfigure(0, weight=1)
        self.target_combo = ttk.Combobox(card, textvariable=self.target_choice, state='readonly')
        self.target_combo.grid(row=1, column=0, sticky='ew')
        self.target_combo.bind('<<ComboboxSelected>>', lambda event: self._target_selected())
        toolbar = ttk.Frame(card); toolbar.grid(row=2, column=0, sticky='ew', pady=(8, 10))
        for text, command in [('新目标', self.new_target), ('保存', self.save_target), ('删除', self.delete_target)]:
            ttk.Button(toolbar, text=text, command=command, width=6).pack(side='left', padx=(0, 5))
        for row, (label, key) in enumerate((('设备名称', 'name'), ('IP / 主机名', 'host')), 3):
            field = ttk.Frame(card); field.grid(row=row, column=0, sticky='ew', pady=(0, 8)); field.columnconfigure(0, weight=1)
            field.columnconfigure(0, weight=0); field.columnconfigure(1, weight=1)
            ttk.Label(field, text=label, style='Muted.TLabel', width=11).grid(row=0, column=0, sticky='w')
            ttk.Entry(field, textvariable=self.target_vars[key]).grid(row=0, column=1, sticky='ew')
        credentials = ttk.Frame(card); credentials.grid(row=5, column=0, sticky='ew', pady=(0, 8))
        credentials.columnconfigure(1, weight=1)
        for column, label, key in ((0, '端口', 'port'), (1, '用户名', 'username')):
            ttk.Label(credentials, text=label, style='Muted.TLabel').grid(row=0, column=column, sticky='w', pady=(0, 3))
            ttk.Entry(credentials, textvariable=self.target_vars[key], width=7 if column == 0 else 14).grid(
                row=1, column=column, sticky='ew', padx=(0, 8) if column == 0 else 0)
        password_row = ttk.Frame(card); password_row.grid(row=6, column=0, sticky='ew'); password_row.columnconfigure(1, weight=1)
        ttk.Label(password_row, text='密码', style='Muted.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.password_entry = ttk.Entry(password_row, textvariable=self.password, show='•', width=12)
        self.password_entry.grid(row=0, column=1, sticky='ew')
        ttk.Checkbutton(password_row, text='显示', variable=self.show_password,
                        command=lambda: self.password_entry.configure(show='' if self.show_password.get() else '•')).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(card, text='密码仅本次使用，不写入配置', style='Muted.TLabel').grid(row=8, column=0, sticky='w', pady=(4, 0))
        actions = ttk.Frame(card); actions.grid(row=9, column=0, sticky='ew', pady=(12, 0)); actions.columnconfigure(0, weight=1)
        self.test_button = ttk.Button(actions, text='测试连接', command=lambda: self.connection(False))
        self.test_button.grid(row=0, column=0, sticky='ew', padx=(0, 6))
        settings = ttk.Menubutton(actions, text='连接设置', width=8)
        settings.grid(row=0, column=1, sticky='ew')
        menu = tk.Menu(settings, tearoff=False)
        menu.add_command(label='主机信任管理', command=self.manage_trust)
        menu.add_command(label='高级设置', command=self.advanced_settings); settings.configure(menu=menu)

    def _build_debug(self, sidebar):
        card = ttk.Frame(sidebar, style='Card.TFrame', padding=14)
        card.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        for column in range(2): card.columnconfigure(column, weight=1)
        header = ttk.Frame(card); header.grid(row=0, column=0, columnspan=2, sticky='ew')
        ttk.Label(header, text='特别调试窗口', style='Section.TLabel').pack(side='left')
        ttk.Button(header, text='配置', command=self.edit_debug_slots, style='Compact.TButton').pack(side='right')
        ttk.Label(card, text='所有目标共用 · 继承当前公共初始化', style='Muted.TLabel', wraplength=270).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(4, 10))
        self.debug_buttons = []
        for index, slot in enumerate(self.debug_slots):
            button = ttk.Button(card, text=f"{index + 1} · {slot['name'][:8]}", width=11,
                                command=lambda selected=index: self.open_debug_terminal(selected))
            button.grid(row=2 + index // 2, column=index % 2, sticky='ew', padx=(0, 6) if index % 2 == 0 else 0, pady=(0, 6))
            self.debug_buttons.append(button)
            menu = tk.Menu(button, tearoff=False)
            menu.add_command(label='新开会话', command=lambda selected=index: self.open_debug_terminal(selected, force_new=True))
            button.bind('<Button-3>', lambda event, popup=menu: popup.tk_popup(event.x_root, event.y_root))

    def _build_profile(self, workspace):
        card = ttk.Frame(workspace, style='Card.TFrame', padding=14)
        card.grid(row=0, column=0, sticky='ew', pady=(0, 12)); card.columnconfigure(1, weight=1)
        ttk.Label(card, text='启动配置', style='Section.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 12))
        self.profile_combo = ttk.Combobox(card, textvariable=self.profile_choice, state='readonly')
        self.profile_combo.grid(row=0, column=1, sticky='ew', padx=(0, 8))
        self.profile_combo.bind('<<ComboboxSelected>>', lambda event: self._profile_selected())
        actions = ttk.Frame(card); actions.grid(row=0, column=2, sticky='e')
        ttk.Button(actions, text='新建', command=lambda: self.edit_profile(new=True), width=5).pack(side='left', padx=(0, 5))
        ttk.Button(actions, text='编辑', command=self.edit_profile, width=5).pack(side='left', padx=(0, 5))
        more = ttk.Menubutton(actions, text='管理', width=6); more.pack(side='left')
        menu = tk.Menu(more, tearoff=False)
        for text, command in [('复制当前配置', self.copy_profile),
                              ('导入配置', self.import_profile), ('导出当前配置', self.export_profile),
                              ('导入示例', lambda: self.import_profile(example_path())), ('删除当前配置', self.delete_profile)]:
            menu.add_command(label=text, command=command)
        more.configure(menu=menu)
        ttk.Label(card, textvariable=self.summary, style='Muted.TLabel', wraplength=610).grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(8, 0))

    def _build_sessions(self, workspace):
        card = ttk.Frame(workspace, style='Card.TFrame', padding=14)
        card.grid(row=1, column=0, sticky='nsew'); card.columnconfigure(0, weight=1); card.rowconfigure(1, weight=1)
        heading = ttk.Frame(card); heading.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        ttk.Label(heading, text='窗口与会话', style='Section.TLabel').pack(side='left')
        self.keep_alive_button = ttk.Checkbutton(heading, text='断线保活（tmux）', variable=self.keep_alive, command=self.save_keep_alive)
        self.keep_alive_button.pack(side='right')
        self.window_tabs = ttk.Notebook(card); self.window_tabs.grid(row=1, column=0, sticky='nsew')
        self.config_page = ttk.Frame(self.window_tabs); self.manual_page = ttk.Frame(self.window_tabs)
        self.window_tabs.add(self.config_page, text='配置窗口'); self.window_tabs.add(self.manual_page, text='手动终端')
        for page in (self.config_page, self.manual_page):
            page.columnconfigure(0, weight=1); page.rowconfigure(0, weight=1)
        self.window_tabs.bind('<<NotebookTabChanged>>', lambda event: self._update_window_controls())
        self.config_page.rowconfigure(0, weight=0); self.config_page.rowconfigure(1, weight=1)
        selection = ttk.Frame(self.config_page); selection.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(4, 8))
        self.select_all_tasks_button = ttk.Checkbutton(selection, text='全选 / 取消全选', variable=self.all_tasks_selected,
                                                       command=self.select_all_tasks)
        self.select_all_tasks_button.pack(side='left')
        ttk.Label(selection, textvariable=self.task_selection_summary, style='Muted.TLabel').pack(side='left', padx=(12, 0))
        self.table = ttk.Treeview(self.config_page, columns=('name', 'directory', 'delay', 'state'), show='tree headings', height=5, selectmode='browse')
        self.table.heading('#0', text='选择')
        self.table.column('#0', width=54, minwidth=54, stretch=False)
        self.table.bind('<Button-1>', self.toggle_task_checkbox)
        self.table.bind('<space>', self.toggle_task_checkbox)
        self.manual_table = ttk.Treeview(self.manual_page, columns=('name', 'directory', 'state'), show='headings', height=5, selectmode='browse')
        for table, page, columns in ((self.table, self.config_page, [('name', '窗口', 150), ('directory', '工作目录', 220), ('delay', '延迟 / 秒', 70), ('state', '状态', 135)]),
                                      (self.manual_table, self.manual_page, [('name', '目标 / 终端', 250), ('directory', '初始目录', 210), ('state', '状态', 135)])):
            for column, label, width in columns:
                table.heading(column, text=label, anchor='w'); table.column(column, width=width, minwidth=60, stretch=column in {'name', 'directory'})
            row = 1 if page is self.config_page else 0
            table.grid(row=row, column=0, sticky='nsew'); style_table(table)
            scrollbar = ttk.Scrollbar(page, orient='vertical', command=table.yview)
            scrollbar.grid(row=row, column=1, sticky='ns'); table.configure(yscrollcommand=scrollbar.set)
            table.bind('<<TreeviewSelect>>', lambda event: self._update_window_controls())
        self.table.bind('<Double-1>', self.show_window_commands)
        self.table.bind('<Return>', lambda event: self.show_window_commands())
        selected = ttk.Frame(card); selected.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        ttk.Label(selected, text='所选窗口', style='Muted.TLabel').pack(side='left', padx=(0, 8))
        for attribute, text, command, style in (
            ('start_window_button', '启动', self.start_selected_window, 'TButton'),
            ('close_window_button', '关闭会话', self.close_selected_window, 'TButton'),
            ('restart_window_button', '重启会话', self.restart_selected_window, 'TButton'),
            ('reconnect_window_button', '重新连接', lambda: self.control_persistent_window(False), 'TButton'),
            ('stop_window_button', '停止程序', lambda: self.control_persistent_window(True), 'Danger.TButton')):
            button = ttk.Button(selected, text=text, command=command, state='disabled', style=style)
            button.pack(side='left', padx=(0, 5)); setattr(self, attribute, button)
        ttk.Separator(card).grid(row=3, column=0, sticky='ew', pady=(6, 6))
        batches = ttk.Frame(card); batches.grid(row=4, column=0, sticky='ew')
        ttk.Label(batches, text='tmux 批量', style='Muted.TLabel').pack(side='left', padx=(0, 8))
        self.reconnect_tmux_all_button = ttk.Button(batches, text='一键重连 tmux', command=lambda: self.control_tmux_all(False), state='disabled')
        self.reconnect_tmux_all_button.pack(side='left', padx=(0, 6))
        self.stop_tmux_all_button = ttk.Button(batches, text='一键关闭 tmux', command=lambda: self.control_tmux_all(True), state='disabled', style='Danger.TButton')
        self.stop_tmux_all_button.pack(side='left')
        ttk.Label(card, text='断开连接保留 tmux 程序；停止程序会结束远端会话。', style='Muted.TLabel').grid(row=5, column=0, sticky='w', pady=(6, 0))
        actions = ttk.Frame(workspace, style='App.TFrame'); actions.grid(row=3, column=0, sticky='ew', pady=(12, 0))
        self.remote_terminal_button = ttk.Button(actions, text='打开远程终端', command=self.open_remote_terminal)
        self.remote_terminal_button.pack(side='left')
        self.launch_button = ttk.Button(actions, text='启动所选任务', style='Launch.TButton', command=lambda: self.connection(True))
        self.launch_button.pack(side='right')
        self.close_all_button = ttk.Button(actions, text='一键断开', command=self.close_all_windows, state='disabled')
        self.close_all_button.pack(side='right', padx=(0, 8))

    def _build_log(self, workspace):
        card = ttk.Frame(workspace, style='Card.TFrame', padding=(14, 10))
        card.grid(row=2, column=0, sticky='ew', pady=(12, 0)); card.columnconfigure(0, weight=1)
        heading = ttk.Frame(card); heading.grid(row=0, column=0, sticky='ew')
        ttk.Label(heading, text='运行日志', style='Section.TLabel').pack(side='left')
        self.log_toggle_button = ttk.Button(heading, text='展开日志', command=self.toggle_log, style='Compact.TButton')
        self.log_toggle_button.pack(side='right')
        self.log_body = ttk.Frame(card); self.log_body.grid(row=1, column=0, sticky='ew', pady=(8, 0)); self.log_body.columnconfigure(0, weight=1)
        self.events_list = tk.Listbox(self.log_body, height=2, exportselection=False, font='TkDefaultFont')
        style_text(self.events_list); self.events_list.grid(row=0, column=0, sticky='ew')
        scrollbar = ttk.Scrollbar(self.log_body, orient='vertical', command=self.events_list.yview)
        scrollbar.grid(row=0, column=1, sticky='ns'); self.events_list.configure(yscrollcommand=scrollbar.set)
        self.log_visible = False; self.log_body.grid_remove()

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible: self.log_body.grid()
        else: self.log_body.grid_remove()
        self.log_toggle_button.configure(text='收起日志' if self.log_visible else '展开日志')

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
        if not self.targets or not self.target_combo.get(): return
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

    def _execution_mode_changed(self):
        local = self.execution_mode.get() == "local"
        self.password.set("")
        if local:
            self.remote_fields.grid_remove(); self.local_hint.grid()
        else:
            self.local_hint.grid_remove(); self.remote_fields.grid()
        self.password_entry.state(["disabled"] if local else ["!disabled"])
        self.keep_alive_button.state(["disabled"] if local else ["!disabled"])
        self.keep_alive.set(False if local else self.launcher.keep_alive)
        self.remote_terminal_button.configure(text="打开本机终端" if local else "打开远程终端")
        self._save_preferences(); self._update_window_controls()

    def _current_target_id(self):
        return local_target()["id"] if self.execution_mode.get() == "local" else self.target_id

    def target(self):
        if self.execution_mode.get() == "local": return local_target()
        data = {key: variable.get().strip() for key, variable in self.target_vars.items()}
        try: data["port"] = int(data["port"])
        except ValueError: raise ValueError("SSH端口必须是整数")
        return validate_target(dict(data, id=self.target_id or uuid.uuid4().hex))

    def save_target(self, quiet=False):
        if self.execution_mode.get() == "local": return local_target()
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
        if not self.profiles or not self.profile_combo.get(): return None
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
            checks = self.task_checks.setdefault(profile["id"], {})
            live_ids = {window["id"] for window in profile["windows"]}
            for identifier in list(checks):
                if identifier not in live_ids: del checks[identifier]
            for index, window in enumerate(profile["windows"]):
                checks.setdefault(window["id"], window["enabled"])
                if not window["enabled"]: checks[window["id"]] = False
                self.table.insert("", "end", iid=window["id"], values=(window["name"], window["working_directory"] or "继承初始化目录",
                                  window["start_delay_seconds"], "待启动" if window["enabled"] else "已禁用"), tags=row_tags("waiting", index))
        else: self.summary.set("尚无配置。可新建配置或导入示例。")
        self._sync_remote_windows(self.launcher.poll())
        self._update_window_controls()
        if hasattr(self, "preferences"): self._save_preferences()

    def checked_task_ids(self):
        profile = self.selected_profile()
        if profile is None: return set()
        checks = self.task_checks.get(profile["id"], {})
        return {window["id"] for window in profile["windows"] if window["enabled"] and checks.get(window["id"], False)}

    def refresh_task_checkboxes(self):
        profile = self.selected_profile()
        chosen = self.checked_task_ids()
        enabled = sum(window["enabled"] for window in profile["windows"]) if profile else 0
        if profile:
            for window in profile["windows"]:
                if self.table.exists(window["id"]):
                    text = "—" if not window["enabled"] else "☑" if window["id"] in chosen else "☐"
                    self.table.item(window["id"], text=text)
        self.all_tasks_selected.set(bool(enabled and len(chosen) == enabled))
        self.select_all_tasks_button.state(["alternate"] if 0 < len(chosen) < enabled else ["!alternate"])
        self.select_all_tasks_button.state(["disabled"] if self.busy or not enabled else ["!disabled"])
        self.task_selection_summary.set(f"已勾选 {len(chosen)} / {enabled} 个可启动任务")
        self.launch_button.state(["disabled"] if not chosen or self.busy or self.launcher.active() else ["!disabled"])

    def select_all_tasks(self):
        profile = self.selected_profile()
        if profile is None or self.busy: return
        chosen = self.all_tasks_selected.get()
        self.task_checks[profile["id"]] = {window["id"]: chosen and window["enabled"] for window in profile["windows"]}
        self.refresh_task_checkboxes()

    def toggle_task_checkbox(self, event):
        keyboard = event.keysym == "space"
        if not keyboard and self.table.identify_column(event.x) != "#0": return
        if self.busy: return "break"
        identifier = (self.table.focus() or next(iter(self.table.selection()), "")) if keyboard else self.table.identify_row(event.y)
        profile = self.selected_profile()
        if profile is not None:
            window = next((window for window in profile["windows"] if window["id"] == identifier), None)
            if window and window["enabled"]:
                checks = self.task_checks[profile["id"]]
                checks[identifier] = not checks[identifier]
                self.refresh_task_checkboxes()
        return "break"

    def checked_profile(self):
        profile = self.selected_profile()
        if profile is None: raise ValueError("请先新建或导入启动配置")
        chosen = self.checked_task_ids()
        if not chosen: raise ValueError("请至少勾选一个要启动的任务")
        snapshot = copy.deepcopy(profile)
        for window in snapshot["windows"]:
            window["enabled"] = window["id"] in chosen
        return snapshot

    def save_keep_alive(self):
        if self.execution_mode.get() == "local": return
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
                                geometry=self.root.geometry(), terminal="gnome-terminal", execution_mode=self.execution_mode.get())
        try: self.store.save_preferences(self.preferences)
        except (ValueError, OSError) as exc: self.log(f"偏好保存失败：{exc}")

    def _profile_saved(self, profile):
        self.store.save_profile(profile); self.refresh_profiles(profile["id"]); self.log("配置已保存")

    def show_window_commands(self, event=None):
        if event is not None:
            if self.table.identify_column(event.x) == "#0": return self.toggle_task_checkbox(event)
            identifier = self.table.identify_row(event.y)
            if not identifier or self.table.identify_region(event.x, event.y) != 'cell': return
            self.table.selection_set(identifier)
        window = self._selected_config_window()
        if window is None: return
        previous = self.root.children.get('command_preview')
        if previous is not None: previous.destroy()
        dialog = tk.Toplevel(self.root, name='command_preview')
        dialog.title('Window Commands - SSH Multi Terminal'); dialog.geometry('780x460'); dialog.minsize(480, 300)
        dialog.transient(self.root); dialog.bind('<Escape>', lambda event: dialog.destroy())
        dialog.columnconfigure(0, weight=1); dialog.rowconfigure(1, weight=1)
        header = ttk.Frame(dialog, padding=(16, 14, 16, 8)); header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text=window['name'], style='Section.TLabel').pack(anchor='w')
        ttk.Label(header, text='工作目录：' + (window['working_directory'] or '继承公共初始化目录'),
                  style='Muted.TLabel', wraplength=700).pack(anchor='w', pady=(4, 0))
        body = ttk.Frame(dialog, padding=(16, 0, 16, 0)); body.grid(row=1, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1); body.rowconfigure(0, weight=1)
        commands = window['commands']
        text = tk.Text(body, wrap='none', font=('TkFixedFont', 11), exportselection=False)
        style_text(text, code=True)
        text.insert('1.0', commands); text.configure(state='disabled')
        text.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(body, orient='vertical', command=text.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(body, orient='horizontal', command=text.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        def select_all(event):
            text.tag_add('sel', '1.0', 'end-1c'); return 'break'
        text.bind('<Control-a>', select_all)
        footer = ttk.Frame(dialog, padding=(16, 10)); footer.grid(row=2, column=0, sticky='ew')
        hint = ttk.Label(footer, text='可选中复制；此处仅显示该窗口的启动命令。', style='Muted.TLabel', wraplength=180)
        hint.pack(side='left', fill='x', expand=True)
        def copy_commands():
            self.root.clipboard_clear(); self.root.clipboard_append(commands)
            hint.configure(text='启动命令已复制')
        ttk.Button(footer, text='关闭', command=dialog.destroy).pack(side='right')
        ttk.Button(footer, text='复制启动命令', command=copy_commands,
                   style='Primary.TButton').pack(side='right', padx=(8, 8))
        text.focus_set()

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
            profile = self.checked_profile() if start else self.selected_profile()
            if start: check_terminal()
            target = self.save_target(quiet=True); password = self.password.get()
            if not password and not is_local(target): raise ValueError("请输入SSH密码，密码仅在本次使用期间保留")
            if start and any(task["state"] == "unknown" for task in self.launcher.tasks.values()):
                if not messagebox.askyesno('Confirm - SSH Multi Terminal', "旧窗口状态无法确认，远端程序可能仍在运行。确认再次启动？", parent=self.root): return
        except (ValueError, OSError, RuntimeError) as exc: messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        self.log("正在准备本机启动……" if is_local(target) else "正在验证SSH连接……")
        if start:
            operation_epoch = self.launcher.control_epoch
            self._work(lambda: self.launcher.start(target, password, profile, expected_epoch=operation_epoch), lambda result: self.log("启动已调度；各窗口独立执行"))
        elif is_local(target):
            from .local import check_local
            self._work(check_local, lambda result: self.log("本机 Bash 环境检查通过"))
        else:
            options = dict(self.launcher.connect_options)
            self._work(lambda: preflight(target, password, self.store.known_hosts, **options), lambda result: self.log("SSH连接及密码验证成功"))

    def _refresh_debug_buttons(self):
        for index, (button, slot) in enumerate(zip(self.debug_buttons, self.debug_slots)):
            button.configure(text=f"{index + 1} · {slot['name'][:8]}")
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
            if not password and existing is None and not is_local(target): raise ValueError("请输入SSH密码")
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
            if not password and not is_local(target): raise ValueError("请输入SSH密码，密码仅在本次使用期间保留")
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        self.log("正在打开本机终端……" if is_local(target) else "正在验证SSH连接并打开远程终端……")
        operation_epoch = self.launcher.control_epoch
        def opened(identifier):
            self._manual_window_opened(identifier, "终端已打开，可在新窗口中手动输入命令")
        profile = self.selected_profile()
        self._work(lambda: self.launcher.open_remote_terminal(target, password, profile=profile, expected_epoch=operation_epoch), opened)

    def _sync_remote_windows(self, tasks):
        live = {identifier: task for identifier, task in tasks.items() if task.get("mode") == "shell"}
        previous = set(self.manual_table.get_children())
        for identifier in previous - live.keys(): self.manual_table.delete(identifier)
        for key in list(self.last_states):
            if key[1] in previous and (key[1] not in live or key[0] != live[key[1]].get("session_id")):
                del self.last_states[key]
        for index, (identifier, task) in enumerate(live.items()):
            state = STATE_NAMES.get(task["state"], task["state"])
            if task.get("exit_code") is not None: state += f" ({task['exit_code']})"
            values = (task["name"], "继承公共初始化目录" if task.get("inherits_init") else "本机用户目录" if task.get("local") else "远端登录目录", state)
            if self.manual_table.exists(identifier): self.manual_table.item(identifier, values=values, tags=row_tags(task["state"], index))
            else: self.manual_table.insert("", "end", iid=identifier, values=values, tags=row_tags(task["state"], index))

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
            if not password and not is_local(target): raise ValueError("请输入SSH密码")
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        identifier = window["id"]; operation_epoch = self.launcher.control_epoch
        def started(result):
            current = self.selected_profile()
            if current and current["id"] == profile["id"] and self.table.exists(identifier):
                self.table.selection_set(identifier); self.table.see(identifier)
            self.log("所选窗口已启动；其他窗口继续运行")
        self.log("正在启动本机所选窗口……" if is_local(target) else "正在验证SSH连接并启动所选窗口……")
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
        self.refresh_task_checkboxes()
        self._refresh_debug_buttons()
        identifier = self._selected_run_window()
        tasks = self.launcher.poll()
        self._sync_remote_windows(tasks)
        state = tasks.get(identifier, {}).get("state") if identifier else None
        window = self._selected_config_window(); profile = self.selected_profile()
        current_task = tasks.get(window["id"]) if window and profile and profile["id"] == self.launcher.profile_id else None
        can_start = bool(window and window["enabled"] and not self.busy and
                         (not current_task or (current_task["state"] in FINAL and (not current_task.get("tmux_session") or current_task.get("stopped")))))
        if self.launcher.active() and (not profile or profile["id"] != self.launcher.profile_id or self._current_target_id() != self.launcher.target_id):
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
            spec = self.launcher.shell_specs.get(identifier) or self.launcher.window_specs[identifier]
            target = spec["target"]
            if target_key(self.target()) != target_key(target): raise ValueError("请先切回该窗口的启动位置和目标")
            password = self.password.get()
            if not password and not is_local(target): raise ValueError("请输入本轮目标的SSH密码")
            check_terminal()
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self.root); return
        self.log("正在重启本机所选窗口……" if is_local(target) else "正在验证连接并重启所选窗口……")
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
                self.table.item(identifier, tags=row_tags(task["state"], self.table.index(identifier)))
            signature = (task["state"], task.get("exit_code"), task.get("error"))
            session_id = task.get("session_id", self.launcher.run_id)
            if self.last_states.get((session_id, identifier)) != signature:
                self.last_states[(session_id, identifier)] = signature
                self.log(f"{task['name']}：{state}" + (" · " + task["error"] if task.get("error") else ""))
        self._update_window_controls()
        self.root.after(100, self._poll)

    def close(self):
        if self.launcher.has_pending() or self.busy:
            if not messagebox.askyesno('Confirm - SSH Multi Terminal', "退出将取消尚未启动的任务。已启动的终端继续运行。是否退出？", parent=self.root): return
        self._save_preferences(); self.closed = True; self.password.set(""); self.launcher.close(); self.root.destroy()


def main():
    root = tk.Tk(className=WINDOW_CLASS); App(root); root.mainloop()
