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
from .settings import validate_debug_slot
from .ssh import preflight, error_text
from .tmux import session_name, stop_session, check_tmux
from .terminal import open_terminal, TerminalGroups, target_key

FINAL = {"succeeded", "failed", "disconnected", "cancelled", "closed", "detached"}


def schedule(profile):
    data = validate_profile(profile)
    return sorted([(window["start_delay_seconds"], window) for window in data["windows"] if window["enabled"]],
                  key=lambda item: item[0])


class Launcher:
    def __init__(self, known_hosts, *, preflight_fn=preflight, broker_factory=Broker, open_fn=open_terminal, connect_options=None, tmux_check_fn=check_tmux):
        self.known_hosts = str(known_hosts); self.preflight_fn = preflight_fn
        self.broker_factory = broker_factory; self.open_fn = open_fn
        self.tmux_check_fn = tmux_check_fn
        self.terminal_groups = TerminalGroups() if open_fn is open_terminal else None
        self.broker = None; self.thread = None; self.tasks = {}; self.run_id = None
        self.lock = threading.RLock(); self.cancel = threading.Event(); self.closed = False
        self.starting = False; self.profile_id = None; self.target_id = None
        self.connect_options = dict(connect_options or {})
        self.run_target = None; self.run_profile = None; self.run_options = {}; self.control_epoch = 0
        self.shell_tasks = {}; self.shell_specs = {}; self.window_specs = {}
        self.restarting = set(); self.shell_count = 0; self.keep_alive = False; self.bulk_running = False

    def active(self):
        with self.lock:
            return self.starting or self.bulk_running or bool(self.restarting) or any(
                task["state"] not in FINAL | {"unknown"} or (task.get("tmux_session") and not task.get("stopped"))
                for task in self.tasks.values())

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
            start_cancel = self.cancel; keep_alive = self.keep_alive
        try:
            self.preflight_fn(target, password, Path(self.known_hosts), **self.connect_options)
            if keep_alive: self.tmux_check_fn(target, password, self.known_hosts, **self.connect_options)
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
                                     "connect_options": dict(self.run_options),
                                     "tmux_session": session_name(target, profile["id"], window["id"]) if keep_alive else None}
                                     for _, window in schedule(profile)}
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
                    opening = self._prepare_window(window, password, self.run_id)
                self._launch_prepared(opening)

        finally:
            password = None
            with self.lock:
                for task in tasks.values():
                    if task["state"] == "waiting": task["state"] = "cancelled"

    def _prepare_window(self, window, password, session_id, *, attach_only=False):
        spec = self.window_specs[window["id"]]
        marker = secrets.token_hex(16)
        snapshot = {"run_id": session_id, "task_id": window["id"], "target": spec["target"],
                    "password": password, "known_hosts": self.known_hosts,
                    "script": build_script(spec["profile"], spec["window"], "" if spec.get("tmux_session") else marker), "marker": marker,
                    "connect_options": spec["connect_options"], "tmux_session": spec.get("tmux_session"), "tmux_attach_only": attach_only}
        token = self.broker.issue(snapshot)
        task = self.tasks[window["id"]]
        task.clear()
        task.update(name=window["name"], state="opening", token=token,
                    session_id=session_id, opened_at=time.monotonic(), tmux_session=spec.get("tmux_session"))
        return {"target": spec["target"], "token": token, "title": f"{spec['target']['name']} · {window['name']}",
                "task": task, "identifier": window["id"], "epoch": self.control_epoch, "raise_errors": False}

    def start_window(self, target, password, profile, identifier, *, expected_epoch=None):
        target = validate_target(target); profile = validate_profile(profile)
        if not password: raise ValueError("请输入SSH密码")
        window = next((w for w in profile["windows"] if w["id"] == identifier), None)
        if window is None or not window["enabled"]: raise ValueError("请选择当前配置中启用的窗口")
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            epoch = self.control_epoch
            if expected_epoch is not None and epoch != expected_epoch: raise ValueError("启动已取消")
            if self.starting or self.bulk_running: raise ValueError("已有启动或批量操作，请稍后重试")
            self.poll()
            same_run = self.profile_id == profile["id"] and self.run_target == target
            if self.active() and not same_run: raise ValueError("已有其他目标或配置的活动任务，请先关闭")
            task = self.tasks.get(identifier) if same_run else None
            if task and task.get("tmux_session") and not task.get("stopped"):
                raise ValueError("该窗口的保活会话仍受管理，请使用重新连接或先停止程序")
            if identifier in self.restarting: raise ValueError("窗口正在操作，请稍后重试")
            if task and task["state"] not in FINAL: raise ValueError("该窗口已有活动任务或状态未知，请先关闭")
            options = dict(self.connect_options); keep_alive = self.keep_alive; self.starting = True
        try:
            self.preflight_fn(target, password, Path(self.known_hosts), **options)
            if keep_alive: self.tmux_check_fn(target, password, self.known_hosts, **options)
            with self.lock:
                if self.closed or epoch != self.control_epoch: raise ValueError("启动已取消")
                if self.broker is None: self.broker = self.broker_factory()
                if not same_run:
                    self.cancel.set(); self.cancel = threading.Event()
                    self.run_id = uuid.uuid4().hex; self.profile_id = profile["id"]; self.target_id = target["id"]
                    self.run_target = target; self.run_profile = profile; self.run_options = options
                    self.tasks = {}; self.window_specs = {}
                self.window_specs[identifier] = {"target": target, "profile": profile,
                                                  "window": window, "connect_options": options,
                                                  "tmux_session": session_name(target, profile["id"], identifier) if keep_alive else None}
                self.tasks[identifier] = {"name": window["name"]}
                opening = self._prepare_window(window, password, uuid.uuid4().hex)
            self._launch_prepared(opening)
            return identifier
        finally:
            with self.lock: self.starting = False

    def all_tasks(self):
        return {**self.tasks, **self.shell_tasks}

    def _spawn_terminal(self, target, token, title):
        if self.terminal_groups is not None: return self.terminal_groups.open(target, self.broker.path, token, title)
        return self.open_fn(self.broker.path, token, title)

    def focus_window(self, identifier):
        with self.lock:
            task = self.all_tasks().get(identifier)
            token = task.get("token") if task else None
        return bool(token and self.terminal_groups is not None and self.terminal_groups.focus(token))

    def find_debug_terminal(self, target, slot, slot_index=None):
        with self.lock:
            tasks = self.poll(); closing = False
            for identifier, spec in self.shell_specs.items():
                same_slot = spec.get("slot_index") == slot_index if slot_index is not None else spec["debug_slot"] == slot
                if spec["debug_slot"] is not None and same_slot and target_key(spec["target"]) == target_key(target):
                    state = tasks[identifier]["state"]
                    if state == "closing": closing = True; continue
                    if state not in FINAL or (tasks[identifier].get("tmux_session") and not tasks[identifier].get("stopped")): return identifier
            if closing: raise ValueError("该调试会话正在关闭，请稍后重试")
        return None

    def open_debug_terminal(self, target, password, profile, slot, *, slot_index=None, force_new=False, expected_epoch=None):
        return self.open_remote_terminal(target, password, profile=profile, debug_slot=slot, slot_index=slot_index,
                                         force_new=force_new, expected_epoch=expected_epoch)

    def open_remote_terminal(self, target, password, *, profile=None, debug_slot=None, slot_index=None, force_new=False, expected_epoch=None):
        target = validate_target(target)
        profile = validate_profile(profile) if profile is not None else None
        if debug_slot is not None:
            debug_slot = validate_debug_slot(debug_slot)
            if not debug_slot["commands"].strip(): raise ValueError("请先配置该调试槽位的命令")
            if profile is None: raise ValueError("请选择启动配置，以继承公共初始化")
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            epoch = self.control_epoch
            if expected_epoch is not None and expected_epoch != epoch: raise ValueError("启动已取消")
            existing = self.find_debug_terminal(target, debug_slot, slot_index) if debug_slot is not None and not force_new else None
            options = dict(self.connect_options); keep_alive = self.keep_alive
        if existing is not None:
            current = self.poll().get(existing)
            if current is None: raise ValueError("会话已关闭，请重新打开")
            if current.get("tmux_session") and current["state"] in FINAL | {"unknown"}:
                self.reconnect_window(existing, password, expected_epoch=epoch)
            return existing
        if not password: raise ValueError("请输入SSH密码")
        self.preflight_fn(target, password, Path(self.known_hosts), **options)
        if keep_alive: self.tmux_check_fn(target, password, self.known_hosts, **options)
        with self.lock:
            if self.closed or self.control_epoch != epoch: raise ValueError("启动已取消")
            existing = self.find_debug_terminal(target, debug_slot, slot_index) if debug_slot is not None and not force_new else None
            if existing is not None:
                if self.shell_tasks[existing].get("tmux_session") and self.shell_tasks[existing]["state"] in FINAL | {"unknown"}: raise ValueError("该会话已断开，请使用重新连接")
                return existing
            if self.broker is None: self.broker = self.broker_factory()
            identifier = "shell_" + uuid.uuid4().hex
            self.shell_count += 1
            name = f"{target['name']} · {debug_slot['name']}" if debug_slot else f"{target['name']} · 远程终端 {self.shell_count}"
            self.shell_specs[identifier] = {"target": target, "connect_options": options, "name": name, "profile": profile, "debug_slot": debug_slot, "slot_index": slot_index,
                "tmux_session": (session_name(target, "debug", str(slot_index) if slot_index is not None else debug_slot["name"])
                    if debug_slot is not None and not force_new else session_name(target, "manual", identifier)) if keep_alive else None}
            self.shell_tasks[identifier] = {"name": name}
            opening = self._prepare_shell(identifier, password)
        self._launch_prepared(opening)
        return identifier

    def _prepare_shell(self, identifier, password, *, attach_only=False):
        spec = self.shell_specs[identifier]
        session_id = uuid.uuid4().hex; marker = secrets.token_hex(16)
        script_marker = "" if spec.get("tmux_session") else marker
        if spec["debug_slot"] is not None:
            script = build_script(spec["profile"], {"working_directory": "", "commands": spec["debug_slot"]["commands"]}, script_marker)
        else: script = build_interactive_script(spec["profile"], script_marker) if spec["profile"] is not None else "exec bash --noprofile --norc -i\n" if spec.get("tmux_session") else ""
        snapshot = {"run_id": session_id, "task_id": identifier, "target": spec["target"],
                    "password": password, "known_hosts": self.known_hosts, "mode": "debug" if spec["debug_slot"] is not None else "shell",
                    "script": script, "marker": marker, "connect_options": spec["connect_options"], "tmux_session": spec.get("tmux_session"), "tmux_attach_only": attach_only}
        token = self.broker.issue(snapshot)
        task = self.shell_tasks[identifier]
        task.clear()
        task.update(name=spec["name"], mode="shell", inherits_init=spec["profile"] is not None, state="opening", token=token,
                    session_id=session_id, opened_at=time.monotonic(), tmux_session=spec.get("tmux_session"))
        return {"target": spec["target"], "token": token, "title": spec["name"], "task": task,
                "identifier": identifier, "epoch": self.control_epoch, "raise_errors": True}

    def _launch_prepared(self, opening):
        token = opening["token"]; task = opening["task"]
        with self.lock:
            current = self.all_tasks().get(opening["identifier"])
            cancelled = (self.closed or self.control_epoch != opening["epoch"] or current is not task
                         or task.get("token") != token or task["state"] != "opening")
            if cancelled:
                self.broker.cancel(token)
                if opening["raise_errors"]: raise ValueError("启动已取消")
                return
        try: self._spawn_terminal(opening["target"], token, opening["title"])
        except Exception as exc:
            with self.lock:
                self.broker.cancel(token)
                if self.all_tasks().get(opening["identifier"]) is task and task.get("token") == token and task["state"] == "opening":
                    task.update(state="failed", error=error_text(exc))
            if opening["raise_errors"]: raise RuntimeError(error_text(exc)) from exc

    def close_window(self, identifier):
        with self.lock:
            if identifier not in self.all_tasks(): raise ValueError("窗口尚未启动")
            task = self.all_tasks()[identifier]
            if task.get("token"):
                self.broker.cancel(task["token"])
                task["state"] = ("detached" if task.get("tmux_session") and not task.get("stopped") else "closed") if self.broker.is_closed(task["token"]) else "closing"
            else: task["state"] = "closed"

    def close_all(self):
        with self.lock:
            self.control_epoch += 1
            self.cancel.set()
            if self.broker is not None: self.broker.cancel_all()
            for identifier in self.all_tasks(): self.close_window(identifier)

    def tmux_windows(self, *, reconnect_only=False):
        """Snapshot managed sessions, including delayed windows with a stable name."""
        with self.lock:
            tasks = self.poll(); result = {}
            for identifier, task in tasks.items():
                spec = self.shell_specs.get(identifier) or self.window_specs.get(identifier)
                name = task.get("tmux_session") or (spec.get("tmux_session") if spec else None)
                if not name or task.get("stopped"): continue
                if reconnect_only and task["state"] not in FINAL | {"unknown"}: continue
                result[identifier] = {"name": task["name"], "target": copy.deepcopy(spec["target"]),
                                      "state": task["state"], "tmux_session": name}
            return result

    def control_tmux_all(self, passwords, *, stopping=False, expected_epoch=None):
        """Try every managed session; passwords are keyed by exact SSH endpoint."""
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            epoch = self.control_epoch
            if expected_epoch is not None and epoch != expected_epoch: raise ValueError("批量操作已取消")
            if self.bulk_running: raise ValueError("已有批量操作，请稍后重试")
            windows = self.tmux_windows(); self.bulk_running = True
            if stopping:
                closing = {identifier: entry for identifier, entry in windows.items() if passwords.get(target_key(entry["target"]))}
                waiting = [identifier for identifier, task in self.tasks.items() if task["state"] == "waiting"]
                if waiting and all(identifier in closing for identifier in waiting): self.cancel.set()
                for identifier, entry in closing.items():
                    task = self.all_tasks()[identifier]
                    task["tmux_session"] = entry["tmux_session"]
                    self.close_window(identifier)
        report = {"requested": {}, "stopped": {}, "failed": {}, "skipped": {}}
        try:
            for identifier, entry in windows.items():
                if not stopping and entry["state"] not in FINAL | {"unknown"}:
                    report["skipped"][identifier] = entry; continue
                try:
                    with self.lock:
                        if self.closed or self.control_epoch != epoch: raise ValueError("批量操作已取消")
                    password = passwords.get(target_key(entry["target"]))
                    if not password: raise ValueError("未提供该目标的SSH密码")
                    action = self.stop_window if stopping else self.reconnect_window
                    action(identifier, password, expected_epoch=epoch)
                    report["stopped" if stopping else "requested"][identifier] = entry
                except Exception as exc:
                    report["failed"][identifier] = dict(entry, error=error_text(exc))
            return report
        finally:
            with self.lock: self.bulk_running = False

    def reconnect_window(self, identifier, password, *, expected_epoch=None):
        with self.lock:
            task = self.all_tasks().get(identifier)
            spec = self.shell_specs.get(identifier) or self.window_specs.get(identifier)
            if not task or not spec or not spec.get("tmux_session"): raise ValueError("该窗口未启用断线保活")
            task["tmux_session"] = spec["tmux_session"]
            if task.get("stopped"): raise ValueError("该会话已停止，请使用重启会话")
        return self.restart_window(identifier, password, expected_epoch=expected_epoch, reconnect=True)

    def stop_window(self, identifier, password, *, expected_epoch=None):
        if not password: raise ValueError("请输入该目标的SSH密码")
        with self.lock:
            if self.closed: raise ValueError("启动器已关闭")
            epoch = self.control_epoch
            if expected_epoch is not None and expected_epoch != epoch: raise ValueError("停止已取消")
            task = self.all_tasks().get(identifier)
            spec = self.shell_specs.get(identifier) or self.window_specs.get(identifier)
            if not task or not spec or not spec.get("tmux_session"): raise ValueError("该窗口未启用断线保活")
            if identifier in self.restarting: raise ValueError("窗口正在操作，请稍后重试")
            spec = copy.deepcopy(spec)
            token = task.get("token"); self.restarting.add(identifier)
            task["tmux_session"] = spec["tmux_session"]
            # Prevent an opening attachment from creating a session after the kill.
            self.close_window(identifier)
        try:
            deadline = time.monotonic() + 20
            while token and not self.broker.is_closed(token):
                if self.closed or self.control_epoch != epoch: raise ValueError("停止已取消")
                if time.monotonic() >= deadline: raise ValueError("旧窗口尚未确认断开，未停止远端会话")
                time.sleep(.05)
            with self.lock:
                if self.closed or self.control_epoch != epoch: raise ValueError("停止已取消")
            stop_session(spec["target"], password, self.known_hosts, spec["tmux_session"], **spec["connect_options"])
            with self.lock:
                if self.all_tasks().get(identifier) is not task or task.get("token") != token:
                    raise ValueError("窗口已发生变化")
                task["stopped"] = True
                self.close_window(identifier)
        finally:
            with self.lock: self.restarting.discard(identifier)

    def restart_window(self, identifier, password, *, expected_epoch=None, reconnect=False):
        with self.lock:
            if identifier in self.restarting: raise ValueError("窗口正在重启，请稍后重试")
            self.restarting.add(identifier)
        try:
            return self._restart_window(identifier, password, expected_epoch=expected_epoch, reconnect=reconnect)
        finally:
            with self.lock: self.restarting.discard(identifier)

    def _restart_window(self, identifier, password, *, expected_epoch=None, reconnect=False):
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
        if spec.get("tmux_session") and not reconnect:
            with self.lock:
                if self.closed or self.control_epoch != epoch: raise ValueError("重启已取消")
            stop_session(target, password, self.known_hosts, spec["tmux_session"], **options)
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
            opening = self._prepare_shell(identifier, password, attach_only=reconnect) if is_shell else self._prepare_window(window, password, uuid.uuid4().hex, attach_only=reconnect)
        self._launch_prepared(opening)

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
                        task["state"] = "detached" if task.get("tmux_session") and not task.get("stopped") else "closed"
                    elif task["state"] == "opening" and time.monotonic() - task["opened_at"] >= 60:
                        self.broker.cancel(task["token"])
                        task.update(state="unknown", error="终端未报告连接状态；请检查窗口后再启动")
                    elif task["state"] in {"connecting", "initializing", "running", "interactive"} and time.monotonic() - task["last_seen"] >= 20:
                        task.update(state="unknown", error="终端会话未报告心跳；请检查窗口和远端进程后再启动")
            for task in self.tasks.values():
                if task.get("tmux_session") and not task.get("stopped") and task.get("token") and self.broker.is_closed(task["token"]):
                    task["state"] = "detached"
            for identifier, task in list(self.shell_tasks.items()):
                if identifier not in self.restarting and self.broker is not None and self.broker.is_closed(task["token"]):
                    if task.get("tmux_session") and not task.get("stopped"):
                        task["state"] = "detached"
                    else:
                        self.shell_tasks.pop(identifier)
                        self.shell_specs.pop(identifier)
                        tasks.pop(identifier)
            return {key: dict(value) for key, value in tasks.items()}

    def cancel_pending(self):
        self.cancel.set()

    def close(self):
        with self.lock: self.closed = True; self.cancel.set()
        if self.thread is not None: self.thread.join(timeout=2)
        if self.broker is not None: self.broker.close()
