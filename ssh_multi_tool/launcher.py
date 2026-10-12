"""Background scheduling and session status; no GUI dependencies."""
import copy
import queue
import secrets
import threading
import time
import uuid
from pathlib import Path

from .broker import Broker
from .models import validate_profile, validate_target
from .scripts import build_script, build_interactive_script
from .ssh import preflight, error_text
from .terminal import open_terminal

FINAL = {"succeeded", "failed", "disconnected", "cancelled", "closed"}


def schedule(profile):
    data = validate_profile(profile)
    return sorted([(window["start_delay_seconds"], window) for window in data["windows"] if window["enabled"]],
                  key=lambda item: item[0])


class Launcher:
    def __init__(self, known_hosts, *, preflight_fn=preflight, broker_factory=Broker, open_fn=open_terminal, connect_options=None):
        self.known_hosts = str(known_hosts); self.preflight_fn = preflight_fn
        self.broker_factory = broker_factory; self.open_fn = open_fn
        self.broker = None; self.thread = None; self.tasks = {}; self.run_id = None
        self.lock = threading.RLock(); self.cancel = threading.Event(); self.closed = False
        self.starting = False; self.profile_id = None; self.target_id = None
        self.connect_options = dict(connect_options or {})
        self.run_target = None; self.run_profile = None; self.run_options = {}; self.control_epoch = 0
        self.shell_tasks = {}; self.shell_specs = {}; self.window_specs = {}

    def active(self):
        with self.lock:
            return self.starting or any(task["state"] not in FINAL | {"unknown"} for task in self.tasks.values())

    def has_pending(self):
        with self.lock: return any(task["state"] == "waiting" for task in self.tasks.values())

    def start(self, target, password, profile, *, expected_epoch=None):
        target = validate_target(target); profile = validate_profile(profile)
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            if expected_epoch is not None and expected_epoch != self.control_epoch: raise ValueError("启动已取消")
            if self.active(): raise ValueError("已有活动任务，请先处理当前终端")
            self.starting = True
            self.cancel = threading.Event()
            start_cancel = self.cancel
        try:
            self.preflight_fn(target, password, Path(self.known_hosts), **self.connect_options)
            with self.lock:
                if self.closed or start_cancel.is_set(): raise ValueError("启动已取消")
                if self.broker is None: self.broker = self.broker_factory()
                self.run_id = uuid.uuid4().hex; self.profile_id = profile["id"]; self.target_id = target["id"]
                self.run_target = copy.deepcopy(target); self.run_profile = copy.deepcopy(profile)
                self.run_options = dict(self.connect_options)
                self.cancel = threading.Event()
                self.tasks = {window["id"]: {"name": window["name"], "state": "waiting"}
                              for _, window in schedule(profile)}
                self.window_specs = {window["id"]: {"target": target, "profile": profile, "window": window,
                                     "connect_options": dict(self.run_options)} for _, window in schedule(profile)}
                epoch = time.monotonic()
                self.thread = threading.Thread(target=self._dispatch,
                    args=(target, password, profile, epoch, self.cancel, self.tasks), daemon=True)
                self.thread.start()
                return self.run_id
        finally:
            with self.lock: self.starting = False

    def _dispatch(self, target, password, profile, epoch, cancel, tasks):
        try:
            for delay, window in schedule(profile):
                if cancel.wait(max(0, epoch + delay - time.monotonic())): break
                with self.lock:
                    if cancel.is_set() or self.closed or self.tasks is not tasks: break
                    task = self.tasks[window["id"]]
                    if task["state"] != "waiting": continue
                    self._open_window(window, password, self.run_id)

        finally:
            password = None
            with self.lock:
                for task in tasks.values():
                    if task["state"] == "waiting": task["state"] = "cancelled"

    def _open_window(self, window, password, session_id):
        spec = self.window_specs[window["id"]]
        marker = secrets.token_hex(16)
        snapshot = {"run_id": session_id, "task_id": window["id"], "target": spec["target"],
                    "password": password, "known_hosts": self.known_hosts,
                    "script": build_script(spec["profile"], spec["window"], marker), "marker": marker,
                    "connect_options": spec["connect_options"]}
        token = self.broker.issue(snapshot)
        task = self.tasks[window["id"]]
        task.clear()
        task.update(name=window["name"], state="opening", token=token,
                    session_id=session_id, opened_at=time.monotonic())
        try:
            self.open_fn(self.broker.path, token, f"{spec['target']['name']} · {window['name']}")
        except Exception as exc:
            self.broker.cancel(token); task.update(state="failed", error=error_text(exc))

    def start_window(self, target, password, profile, identifier, *, expected_epoch=None):
        target = validate_target(target); profile = validate_profile(profile)
        if not password: raise ValueError("请输入SSH密码")
        window = next((w for w in profile["windows"] if w["id"] == identifier), None)
        if window is None or not window["enabled"]: raise ValueError("请选择当前配置中启用的窗口")
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            epoch = self.control_epoch
            if expected_epoch is not None and epoch != expected_epoch: raise ValueError("启动已取消")
            if self.starting: raise ValueError("已有启动操作，请稍后重试")
            same_run = self.profile_id == profile["id"] and self.run_target == target
            if self.active() and not same_run: raise ValueError("已有其他目标或配置的活动任务，请先关闭")
            task = self.tasks.get(identifier) if same_run else None
            if task and task["state"] not in FINAL: raise ValueError("该窗口已有活动任务或状态未知，请先关闭")
            options = dict(self.connect_options); self.starting = True
        try:
            self.preflight_fn(target, password, Path(self.known_hosts), **options)
            with self.lock:
                if self.closed or epoch != self.control_epoch: raise ValueError("启动已取消")
                if self.broker is None: self.broker = self.broker_factory()
                if not same_run:
                    self.cancel.set(); self.cancel = threading.Event()
                    self.run_id = uuid.uuid4().hex; self.profile_id = profile["id"]; self.target_id = target["id"]
                    self.run_target = target; self.run_profile = profile; self.run_options = options
                    self.tasks = {}; self.window_specs = {}
                self.window_specs[identifier] = {"target": target, "profile": profile,
                                                  "window": window, "connect_options": options}
                self.tasks[identifier] = {"name": window["name"]}
                self._open_window(window, password, uuid.uuid4().hex)
                return identifier
        finally:
            with self.lock: self.starting = False

    def all_tasks(self):
        return {**self.tasks, **self.shell_tasks}

    def open_remote_terminal(self, target, password, *, profile=None, expected_epoch=None):
        target = validate_target(target)
        profile = validate_profile(profile) if profile is not None else None
        if not password: raise ValueError("请输入SSH密码")
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            epoch = self.control_epoch
            if expected_epoch is not None and expected_epoch != epoch: raise ValueError("启动已取消")
            options = dict(self.connect_options)
        self.preflight_fn(target, password, Path(self.known_hosts), **options)
        with self.lock:
            if self.closed or self.control_epoch != epoch: raise ValueError("启动已取消")
            if self.broker is None: self.broker = self.broker_factory()
            identifier = "shell_" + uuid.uuid4().hex
            name = f"{target['name']} · 远程终端 {len(self.shell_specs) + 1}"
            self.shell_specs[identifier] = {"target": target, "connect_options": options, "name": name, "profile": profile}
            self.shell_tasks[identifier] = {"name": name}
            self._open_shell(identifier, password)
            return identifier

    def _open_shell(self, identifier, password):
        spec = self.shell_specs[identifier]
        session_id = uuid.uuid4().hex; marker = secrets.token_hex(16)
        script = build_interactive_script(spec["profile"], marker) if spec["profile"] is not None else ""
        snapshot = {"run_id": session_id, "task_id": identifier, "target": spec["target"],
                    "password": password, "known_hosts": self.known_hosts, "mode": "shell",
                    "script": script, "marker": marker, "connect_options": spec["connect_options"]}
        token = self.broker.issue(snapshot)
        task = self.shell_tasks[identifier]
        task.clear()
        task.update(name=spec["name"], mode="shell", inherits_init=spec["profile"] is not None, state="opening", token=token,
                    session_id=session_id, opened_at=time.monotonic())
        try: self.open_fn(self.broker.path, token, spec["name"])
        except Exception as exc:
            self.broker.cancel(token); task.update(state="failed", error=error_text(exc))

    def close_window(self, identifier):
        with self.lock:
            if identifier not in self.all_tasks(): raise ValueError("窗口尚未启动")
            task = self.all_tasks()[identifier]
            if task.get("token"):
                self.broker.cancel(task["token"])
                task["state"] = "closed" if self.broker.is_closed(task["token"]) else "closing"
            else: task["state"] = "closed"

    def close_all(self):
        with self.lock:
            self.control_epoch += 1
            self.cancel.set()
            if self.broker is not None: self.broker.cancel_all()
            for identifier in self.all_tasks(): self.close_window(identifier)

    def restart_window(self, identifier, password, *, expected_epoch=None):
        if not password: raise ValueError("请输入本轮目标的SSH密码")
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            if identifier not in self.all_tasks(): raise ValueError("窗口尚未启动")
            if expected_epoch is not None and expected_epoch != self.control_epoch: raise ValueError("重启已取消")
            run_id = self.run_id; epoch = self.control_epoch
            is_shell = identifier in self.shell_specs
            spec = self.shell_specs[identifier] if is_shell else self.window_specs[identifier]
            target = copy.deepcopy(spec["target"])
            options = dict(spec["connect_options"])
            window = None if is_shell else spec["window"]
        # Validate credentials before interrupting the existing session.
        self.preflight_fn(target, password, Path(self.known_hosts), **options)
        with self.lock:
            if self.closed or (not is_shell and self.run_id != run_id) or self.control_epoch != epoch: raise ValueError("重启已取消")
            self.close_window(identifier)
            token = self.all_tasks()[identifier].get("token")
        deadline = time.monotonic() + 20
        while token and not self.broker.is_closed(token):
            if self.closed or self.control_epoch != epoch: raise ValueError("重启已取消")
            time.sleep(.05)
            if time.monotonic() >= deadline: raise ValueError("旧窗口尚未确认关闭，未启动新窗口")
        with self.lock:
            if self.closed or (not is_shell and self.run_id != run_id) or self.control_epoch != epoch: raise ValueError("重启已取消")
            if is_shell: self._open_shell(identifier, password)
            else: self._open_window(window, password, uuid.uuid4().hex)

    def poll(self):
        with self.lock:
            tasks = self.all_tasks()
            if self.broker is not None:
                while True:
                    try: event = self.broker.events.get_nowait()
                    except queue.Empty: break
                    if event.get("task_id") in tasks:
                        task = tasks[event["task_id"]]
                        if event.get("run_id") != task.get("session_id", self.run_id) or task["state"] in {"closing", "closed"}: continue
                        task.update({key: value for key, value in event.items() if key not in {"run_id", "task_id"}})
                        task["last_seen"] = time.monotonic()
                for task in tasks.values():
                    if task["state"] == "closing" and self.broker.is_closed(task["token"]):
                        task["state"] = "closed"
                    elif task["state"] == "opening" and time.monotonic() - task["opened_at"] >= 60:
                        self.broker.cancel(task["token"])
                        task.update(state="unknown", error="终端未报告连接状态；请检查窗口后再启动")
                    elif task["state"] in {"connecting", "initializing", "running", "interactive"} and time.monotonic() - task["last_seen"] >= 20:
                        task.update(state="unknown", error="终端会话未报告心跳；请检查窗口和远端进程后再启动")
            return {key: dict(value) for key, value in tasks.items()}

    def cancel_pending(self):
        self.cancel.set()

    def close(self):
        with self.lock: self.closed = True; self.cancel.set()
        if self.thread is not None: self.thread.join(timeout=2)
        if self.broker is not None: self.broker.close()
