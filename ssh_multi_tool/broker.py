"""Restricted local IPC. Passwords are never command-line arguments."""
import copy
import json
import os
import queue
import secrets
import select
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

from .compat import process_identity, process_is_alive

MAX_FRAME = 2 * 1024 * 1024
STATES = {"connecting", "initializing", "running", "succeeded", "failed", "disconnected", "unknown", "closed", "interactive"}


class TokenVault:
    def __init__(self, clock=time.monotonic):
        self.clock = clock; self.lock = threading.Lock()
        self.pending = {}; self.event_tokens = {}
        self.sessions = {}; self.controls = {}

    def issue(self, snapshot):
        with self.lock:
            self._expire()
            token = secrets.token_urlsafe(32)
            self.pending[token] = (self.clock() + 60, copy.deepcopy(snapshot))
            return token

    def _expire(self):
        for token, (deadline, _) in list(self.pending.items()):
            if deadline <= self.clock(): del self.pending[token]

    def claim(self, token, pid=None):
        with self.lock:
            self._expire()
            item = self.pending.pop(token, None)
            if item is None: raise ValueError("启动令牌无效或已过期")
            snapshot = item[1]; event_token = secrets.token_urlsafe(32)
            snapshot["event_token"] = event_token
            self.event_tokens[event_token] = (snapshot["run_id"], snapshot["task_id"])
            control_token = secrets.token_urlsafe(32)
            snapshot["control_token"] = control_token
            self.sessions[token] = control_token
            pidfd = None
            if pid is not None and hasattr(os, "pidfd_open"):
                try: pidfd = os.pidfd_open(pid)
                except OSError: pass
            self.controls[control_token] = {"close": False, "event_token": event_token,
                                            "launch_token": token, "pidfd": pidfd, "pid": pid,
                                            "process_identity": process_identity(pid) if pid is not None and pidfd is None else None}
            return snapshot

    def cancel(self, token):
        with self.lock:
            self.pending.pop(token, None)
            control = self.sessions.get(token)
            if control in self.controls: self.controls[control]["close"] = True

    def cancel_all(self):
        with self.lock:
            self.pending.clear()
            for item in self.controls.values(): item["close"] = True

    def _release(self, token):
        item = self.controls.pop(token)
        self.sessions.pop(item["launch_token"], None)
        self.event_tokens.pop(item["event_token"], None)
        if item["pidfd"] is not None: os.close(item["pidfd"])

    def is_closed(self, token):
        with self.lock:
            self._expire()
            control = self.sessions.get(token)
            if control:
                pidfd = self.controls[control]["pidfd"]
                if pidfd is not None and select.select([pidfd], [], [], 0)[0]: self._release(control)
                elif pidfd is None:
                    item = self.controls[control]
                    if item["pid"] is not None and not process_is_alive(item["pid"], item["process_identity"]):
                        self._release(control)
            return token not in self.pending and token not in self.sessions

    def control(self, token, released=False):
        with self.lock:
            item = self.controls.get(token)
            if item is None: raise ValueError("控制认证失败")
            if released:
                self._release(token)
            return {"close": item["close"]}

    def event(self, token, event):
        with self.lock:
            expected = self.event_tokens.get(token)
            if not isinstance(event, dict) or expected is None:
                raise ValueError("事件认证失败")
            if (event.get("run_id"), event.get("task_id")) != expected:
                raise ValueError("任务身份不一致")
            if event.get("state") not in STATES:
                raise ValueError("未知任务状态")
            allowed = {"run_id", "task_id", "state", "exit_code", "error", "time"}
            if event.keys() - allowed: raise ValueError("未知事件字段")
            if event.get("state") in {"succeeded", "failed", "disconnected", "closed"}:
                del self.event_tokens[token]
            return True

    def close(self):
        with self.lock:
            for token in list(self.controls): self._release(token)
            self.pending.clear(); self.event_tokens.clear()


def _recv_exact(stream, length):
    result = bytearray()
    while len(result) < length:
        part = stream.recv(length - len(result))
        if not part: raise ValueError("本机通信中断")
        result.extend(part)
    return bytes(result)


def _receive(stream):
    size = struct.unpack("!I", _recv_exact(stream, 4))[0]
    if not 0 < size <= MAX_FRAME: raise ValueError("本机通信消息过长")
    data = json.loads(_recv_exact(stream, size).decode("utf-8"))
    if not isinstance(data, dict): raise ValueError("通信消息必须是对象")
    return data


def _send(stream, data):
    content = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(content) > MAX_FRAME: raise ValueError("本机通信消息过长")
    stream.sendall(struct.pack("!I", len(content)) + content)


def request(path, message):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5); client.connect(path)
        _send(client, message); reply = _receive(client)
    if not reply.get("ok"): raise ValueError(reply.get("error", "本机通信失败"))
    return reply.get("data", {})


class Broker:
    def __init__(self):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("安全凭据通信及多终端启动目前仅支持Linux")
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        # /tmp is safe here because mkdtemp creates an unpredictable private directory.
        self.directory = Path(tempfile.mkdtemp(prefix="sshmt-", dir=runtime or "/tmp"))
        self.directory.chmod(0o700); self.path = str(self.directory / "broker.sock")
        self.vault = TokenVault(); self.events = queue.Queue(maxsize=1024)
        self.stop = threading.Event(); self.lock = threading.Lock(); self.connections = set()
        self.slots = threading.BoundedSemaphore(16)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.server.bind(self.path); Path(self.path).chmod(0o600)
            self.server.listen(32); self.server.settimeout(0.2)
        except BaseException:
            self.server.close(); Path(self.path).unlink(missing_ok=True); self.directory.rmdir(); raise
        self.thread = threading.Thread(target=self._accept, daemon=True); self.thread.start()

    def issue(self, snapshot): return self.vault.issue(snapshot)
    def cancel(self, token): self.vault.cancel(token)
    def is_closed(self, token): return self.vault.is_closed(token)
    def cancel_all(self): self.vault.cancel_all()

    def _accept(self):
        while not self.stop.is_set():
            try: stream, _ = self.server.accept()
            except socket.timeout: continue
            except OSError: break
            if not self.slots.acquire(blocking=False): stream.close(); continue
            with self.lock: self.connections.add(stream)
            threading.Thread(target=self._handle, args=(stream,), daemon=True).start()

    def _handle(self, stream):
        try:
            stream.settimeout(5)
            pid, uid, _ = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid(): raise ValueError("用户身份不匹配")
            message = _receive(stream); op = message.get("op"); token = message.get("token")
            if not isinstance(token, str) or len(token) > 128: raise ValueError("认证令牌无效")
            if op == "claim":
                data = self.vault.claim(token, pid=pid)
            elif op in {"control", "released"}:
                data = self.vault.control(token, released=op == "released")
            elif op == "event":
                event = message.get("event")
                self.vault.event(token, event)
                try: self.events.put_nowait(event)
                except queue.Full:
                    try: self.events.get_nowait()
                    except queue.Empty: pass
                    self.events.put_nowait(event)
                data = {}
            else: raise ValueError("未知操作")
            _send(stream, {"ok": True, "data": data})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            try: _send(stream, {"ok": False, "error": "本机认证或通信失败"})
            except (OSError, ValueError): pass
        finally:
            stream.close()
            with self.lock: self.connections.discard(stream)
            self.slots.release()

    def close(self):
        if self.stop.is_set(): return
        self.stop.set(); self.server.close()
        with self.lock:
            for stream in self.connections:
                try: stream.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        self.thread.join(timeout=1)
        self.vault.close(); Path(self.path).unlink(missing_ok=True)
        self.directory.rmdir()
