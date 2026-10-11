"""One independent SSH session, attached to a real local terminal."""
import os
import re
import select
import shlex
import sys
import time
import socket

from .broker import request
from .ssh import connect, error_text
from .reporting import Reporter


class PhaseParser:
    """Decode nonce-tagged control frames, never ordinary log text."""
    def __init__(self, marker):
        if not re.fullmatch(r"[a-f0-9]{32}", marker): raise ValueError("无效阶段标识")
        self.prefix = b"\x1e" + marker.encode() + b":"
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data); output = bytearray(); phases = []
        while self.buffer:
            index = self.buffer.find(self.prefix)
            if index >= 0:
                output.extend(self.buffer[:index]); del self.buffer[:index]
                end = self.buffer.find(b"\x1f", len(self.prefix))
                if end < 0:
                    if len(self.buffer) > len(self.prefix) + 32:
                        output.append(self.buffer.pop(0)); continue
                    break
                phase = bytes(self.buffer[len(self.prefix):end])
                if phase in (b"ready", b"initializing", b"running"):
                    phases.append(phase.decode()); del self.buffer[:end + 1]
                else:
                    output.extend(self.buffer[:end + 1]); del self.buffer[:end + 1]
            else:
                keep = 0
                for length in range(min(len(self.buffer), len(self.prefix) - 1), 0, -1):
                    if self.buffer[-length:] == self.prefix[:length]: keep = length; break
                if keep:
                    output.extend(self.buffer[:-keep]); del self.buffer[:-keep]
                else:
                    output.extend(self.buffer); self.buffer.clear()
                break
        return bytes(output), phases

    def finish(self):
        output = bytes(self.buffer); self.buffer.clear(); return output


def loader_command(script: bytes, marker: str):
    # Read exactly the script bytes with echo/signals disabled, then restore PTY.
    # eval avoids OS argument-length limits and does not create a remote script file.
    body = ("set -e; saved=$(stty -g); stty raw -echo; "
            f"printf '\\036{marker}:ready\\037'; "
            f"script=$(head -c {len(script)}); stty \"$saved\"; eval \"$script\"")
    return "bash --noprofile --norc -c " + shlex.quote(body)


def run_session(snapshot, events, *, output=None, interactive=True):
    output = output or sys.stdout.buffer
    client = None; channel = None; old_settings = None
    input_fd = None; previous_size = None; started = False; pending = bytearray()
    parser = PhaseParser(snapshot["marker"])
    script = snapshot["script"].encode("utf-8")
    def event(state, **extra): events({"state": state, **extra})
    event("connecting")
    try:
        client = connect(snapshot["target"], snapshot.pop("password"), snapshot["known_hosts"],
                         **snapshot.get("connect_options", {}))
        channel = client.get_transport().open_session(timeout=10)
        if interactive:
            import termios
            import tty
            input_fd = sys.stdin.fileno()
            if not os.isatty(input_fd): raise RuntimeError("helper必须运行在真实终端中")
            old_settings = termios.tcgetattr(input_fd)
            previous_size = os.get_terminal_size(input_fd)
        width, height = (previous_size.columns, previous_size.lines) if previous_size else (100, 30)
        channel.get_pty(term=os.environ.get("TERM", "xterm-256color"), width=width, height=height)
        channel.exec_command(loader_command(script, snapshot["marker"]))
        channel.settimeout(0)
        if interactive: tty.setraw(input_fd)
        ready_deadline = time.monotonic() + 15
        while True:
            for _ in range(16):
                if not channel.recv_ready(): break
                data = channel.recv(65536)
                visible, phases = parser.feed(data)
                if visible: output.write(visible); output.flush()
                for phase in phases:
                    if phase == "ready" and not started:
                        pending.extend(script); started = True
                    elif phase in ("initializing", "running"): event(phase)
            if pending and channel.send_ready():
                try:
                    sent = channel.send(bytes(pending[:32768]))
                    if sent == 0: raise ConnectionError("SSH发送通道关闭")
                    del pending[:sent]
                except socket.timeout: pass
            if interactive and started and input_fd is not None and len(pending) < 65536:
                ready, _, _ = select.select([input_fd], [], [], 0)
                if ready:
                    keys = os.read(input_fd, 4096)
                    if keys: pending.extend(keys)
                    else: channel.shutdown_write(); input_fd = None
                if input_fd is not None:
                    size = os.get_terminal_size(input_fd)
                    if size != previous_size:
                        channel.resize_pty(width=size.columns, height=size.lines); previous_size = size
            if channel.exit_status_ready() and not channel.recv_ready():
                tail = parser.finish()
                if tail: output.write(tail); output.flush()
                code = channel.recv_exit_status()
                if code == -1:
                    event("disconnected", error="连接中断，远端退出状态未知")
                    return 255
                event("succeeded" if code == 0 else "failed", exit_code=code)
                return code
            if not client.get_transport().is_active():
                event("disconnected", error="SSH连接断开，远端进程可能继续运行")
                return 255
            if not started and time.monotonic() > ready_deadline:
                raise TimeoutError("远端脚本加载握手超时")
            time.sleep(.001)
    except Exception as exc:
        event("failed", error=error_text(exc)); return 255
    finally:
        if old_settings is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        if channel is not None: channel.close()
        if client is not None: client.close()


def helper_main(socket_path, token):
    result = 255
    try:
        snapshot = request(socket_path, {"op": "claim", "token": token})
        def send_event(event):
            message = {**event, "run_id": snapshot["run_id"], "task_id": snapshot["task_id"], "time": time.time()}
            request(socket_path, {"op": "event", "token": snapshot["event_token"], "event": message})
        reporter = Reporter(send_event)
        def events(event):
            if event.get("error"): print("\r\n" + event["error"], flush=True)
            reporter.emit(event)
        try: result = run_session(snapshot, events)
        finally: reporter.close()
        print(f"\n任务已结束，退出码：{result}。")
    except Exception as exc:
        print("\n无法启动会话：" + error_text(exc))
    try: input("按回车关闭此窗口……")
    except (EOFError, KeyboardInterrupt): pass
    return result
