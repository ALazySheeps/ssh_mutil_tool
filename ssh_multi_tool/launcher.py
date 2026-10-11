"""Background scheduling and session status; no GUI dependencies."""
import queue
import secrets
import threading
import time
import uuid
from pathlib import Path

from .broker import Broker
from .models import validate_profile, validate_target
from .scripts import build_script
from .ssh import preflight, error_text
from .terminal import open_terminal

FINAL = {"succeeded", "failed", "disconnected", "cancelled"}


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

    def active(self):
        with self.lock:
            return self.starting or any(task["state"] not in FINAL | {"unknown"} for task in self.tasks.values())

    def has_pending(self):
        with self.lock: return any(task["state"] == "waiting" for task in self.tasks.values())

    def start(self, target, password, profile):
        target = validate_target(target); profile = validate_profile(profile)
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            if self.active(): raise ValueError("已有活动任务，请先处理当前终端")
            self.starting = True
        try:
            self.preflight_fn(target, password, Path(self.known_hosts), **self.connect_options)
            with self.lock:
                if self.closed: raise ValueError("启动已取消")
                if self.broker is None: self.broker = self.broker_factory()
                self.run_id = uuid.uuid4().hex; self.profile_id = profile["id"]; self.target_id = target["id"]
                self.cancel = threading.Event()
                self.tasks = {window["id"]: {"name": window["name"], "state": "waiting"}
                              for _, window in schedule(profile)}
                epoch = time.monotonic()
                self.thread = threading.Thread(target=self._dispatch,
                    args=(target, password, profile, epoch, self.cancel), daemon=True)
                self.thread.start()
                return self.run_id
        finally:
            with self.lock: self.starting = False

    def _dispatch(self, target, password, profile, epoch, cancel):
        try:
            for delay, window in schedule(profile):
                if cancel.wait(max(0, epoch + delay - time.monotonic())): break
                with self.lock:
                    if cancel.is_set() or self.closed: break
                    marker = secrets.token_hex(16)
                    snapshot = {"run_id": self.run_id, "task_id": window["id"], "target": target,
                                "password": password, "known_hosts": self.known_hosts,
                                "script": build_script(profile, window, marker), "marker": marker,
                                "connect_options": self.connect_options}
                    token = self.broker.issue(snapshot)
                    task = self.tasks[window["id"]]
                    task.update(state="opening", token=token, opened_at=time.monotonic())
                    try:
                        self.open_fn(self.broker.path, token, f"{target['name']} · {window['name']}")
                    except Exception as exc:
                        self.broker.cancel(token); task.update(state="failed", error=error_text(exc))
        finally:
            password = None
            with self.lock:
                for task in self.tasks.values():
                    if task["state"] == "waiting": task["state"] = "cancelled"

    def poll(self):
        with self.lock:
            if self.broker is not None:
                while True:
                    try: event = self.broker.events.get_nowait()
                    except queue.Empty: break
                    if event.get("run_id") == self.run_id and event.get("task_id") in self.tasks:
                        task = self.tasks[event["task_id"]]
                        task.update({key: value for key, value in event.items() if key not in {"run_id", "task_id"}})
                        task["last_seen"] = time.monotonic()
                for task in self.tasks.values():
                    if task["state"] == "opening" and time.monotonic() - task["opened_at"] >= 60:
                        self.broker.cancel(task["token"])
                        task.update(state="unknown", error="终端未报告连接状态；请检查窗口后再启动")
                    elif task["state"] in {"connecting", "initializing", "running"} and time.monotonic() - task["last_seen"] >= 20:
                        task.update(state="unknown", error="终端会话未报告心跳；请检查窗口和远端进程后再启动")
            return {key: dict(value) for key, value in self.tasks.items()}

    def cancel_pending(self):
        self.cancel.set()

    def close(self):
        with self.lock: self.closed = True; self.cancel.set()
        if self.thread is not None: self.thread.join(timeout=2)
        if self.broker is not None: self.broker.close()
