"""Run local Bash in its own controlling PTY, with the usual session events."""
import errno
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def check_local():
    if not sys.platform.startswith('linux'):
        raise RuntimeError('本机启动目前支持Ubuntu桌面。')
    for command in ('bash', 'stty', 'head'):
        if not shutil.which(command): raise RuntimeError(f'本机启动缺少命令：{command}')


def child_main(command):
    # Popen creates a fresh session; acquire its terminal in this single-threaded
    # child rather than using preexec_fn/fork callbacks in the threaded helper.
    import fcntl
    import termios
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execvp('bash', ['bash', '--noprofile', '--norc', '-c', command])


def _session_groups(session_id):
    """Include background/stopped Bash jobs, which have their own PGIDs."""
    groups = set()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try:
            fields = (entry / 'stat').read_text(errors='replace').rsplit(')', 1)[1].split()
            if int(fields[3]) == session_id: groups.add(int(fields[2]))
        except (OSError, ValueError, IndexError):
            # Processes can exit between listing /proc and reading their stat.
            continue
    return groups


def run_local_session(snapshot, events, *, output=None, interactive=True, stop=None):
    import fcntl
    import pty
    import struct
    import termios
    import tty
    from .helper import PhaseParser, loader_command
    from .terminal import helper_argv

    output = output or sys.stdout.buffer
    process = None; master = None; slave = None; old_settings = None
    input_fd = None; previous_size = None
    parser = PhaseParser(snapshot['marker']); pending = bytearray(); started = False; terminal_open = True

    def resize():
        nonlocal previous_size
        size = os.get_terminal_size(input_fd) if input_fd is not None else os.terminal_size((100, 30))
        if size != previous_size:
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', size.lines, size.columns, 0, 0))
            previous_size = size

    try:
        check_local()
        if stop is not None and stop.is_set():
            events({'state': 'closed'}); return 0
        script = snapshot['script'].encode('utf-8')
        if interactive:
            input_fd = sys.stdin.fileno()
            if not os.isatty(input_fd): raise RuntimeError('helper必须运行在真实终端中')
            old_settings = termios.tcgetattr(input_fd)
        master, slave = pty.openpty(); resize()
        prefix = helper_argv('', '')
        args = prefix[:prefix.index('--helper')] + ['--local-child', '--command', loader_command(script, snapshot['marker'])]
        process = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave); slave = None
        os.set_blocking(master, False)
        if interactive: tty.setraw(input_fd)
        deadline = time.monotonic() + 15
        while True:
            if stop is not None and stop.is_set():
                events({'state': 'closed'}); return 0
            readers = [master] if terminal_open else []
            if terminal_open and input_fd is not None and started and not pending: readers.append(input_fd)
            readable, writable, _ = select.select(readers, [master] if terminal_open and pending else [], [], .02)
            if master in readable:
                try: data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO: raise
                    data = b''
                # Closing all stdio descriptors is legal while the process
                # continues. Keep observing cancellation and its actual exit.
                if not data: terminal_open = False
                visible, phases = parser.feed(data)
                if visible: output.write(visible); output.flush()
                for phase in phases:
                    if phase == 'ready' and not started:
                        pending.extend(script); started = True
                    elif phase in ('initializing', 'running', 'interactive'): events({'state': phase})
            if terminal_open and master in writable and pending:
                try: sent = os.write(master, pending[:32768])
                except BlockingIOError: sent = 0
                del pending[:sent]
            if terminal_open and input_fd is not None:
                if input_fd in readable:
                    keys = os.read(input_fd, 4096)
                    if keys: pending.extend(keys)
                    else: input_fd = None
                else: resize()
            if process.poll() is not None and (not terminal_open or master not in readable): break
            if not started and time.monotonic() > deadline: raise TimeoutError('本机脚本加载握手超时')
        tail = parser.finish()
        if tail: output.write(tail); output.flush()
        code = process.wait()
        if code < 0: code = 128 - code
        events({'state': 'succeeded' if code == 0 else 'failed', 'exit_code': code})
        return code
    except Exception as exc:
        events({'state': 'failed', 'error': str(exc)}); return 255
    finally:
        if process is not None:
            # The child owns a fresh session. All its ordinary jobs belong to
            # that session, even when Bash gives them separate process groups.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                groups = _session_groups(process.pid)
                groups.discard(0); groups.discard(os.getpgrp())
                for group in groups:
                    try: os.killpg(group, sig)
                    except ProcessLookupError: pass
                if sig == signal.SIGTERM:
                    try: process.wait(timeout=.5)
                    except subprocess.TimeoutExpired: pass
            process.wait()
        if master is not None: os.close(master)
        if slave is not None: os.close(slave)
        if old_settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
