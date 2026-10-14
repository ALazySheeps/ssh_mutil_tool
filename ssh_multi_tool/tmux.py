"""Remote tmux sessions on a dedicated server, without storing credentials."""
import hashlib
import json
import re
import secrets
import shlex
import time

from .ssh import connect
from .terminal import target_key

TMUX = 'env -u TMUX tmux -L ssh-multi-tool -f /dev/null'


def session_name(target, namespace, identifier):
    identity = json.dumps([target_key(target), namespace, identifier], ensure_ascii=False)
    return 'smt_' + hashlib.sha256(identity.encode()).hexdigest()[:32]


def _target(name):
    if not re.fullmatch(r'smt_[a-zA-Z0-9_]+', name): raise ValueError('无效tmux会话名称')
    return shlex.quote('=' + name)


def build_tmux_script(script, name, marker, *, attach_only=False):
    target = _target(name)
    if not re.fullmatch(r'[a-f0-9]{32}', marker): raise ValueError('无效阶段标识')
    delimiter = 'SMT_' + secrets.token_hex(24)
    # The pane unlinks its private script after Bash has opened it. No payload in argv.
    payload = '\n'.join(['set -e', 'rm -f -- "$0"',
        TMUX + ' set-window-option -t "$TMUX_PANE" remain-on-exit on', script, ''])
    check = 'command -v tmux >/dev/null 2>&1 || { printf "目标未安装 tmux，请先安装后启用断线保活。\\n" >&2; exit 127; }'
    if attach_only:
        commands = [f'{TMUX} has-session -t {target} 2>/dev/null || {{ echo "原tmux会话不存在，请使用重启会话重新启动。" >&2; exit 1; }}']
    else:
        commands = [
            f'if ! {TMUX} has-session -t {target} 2>/dev/null; then',
            '  script_file=$(mktemp /tmp/ssh-multi-tool.XXXXXXXXXX)',
            '  trap \'rm -f -- "$script_file"\' EXIT',
            f'  cat > "$script_file" <<\'{delimiter}\'', payload, delimiter,
            '  pane_command="bash --noprofile --norc $script_file"',
            f'  if ! {TMUX} new-session -d -s {shlex.quote(name)} "$pane_command"; then',
            '    rm -f -- "$script_file"',
            f'    {TMUX} has-session -t {target}',
            '  fi', 'fi']
    return '\n'.join(['set -e', check, *commands,
        f"printf '\\036{marker}:interactive\\037'",
        f'exec {TMUX} attach-session -t {target}', ''])


def stop_session(target, password, known_hosts, name, **options):
    """Destroy only the named managed session; absence is already stopped."""
    exact = _target(name)
    command = ('command -v tmux >/dev/null 2>&1 || { echo "目标未安装 tmux" >&2; exit 127; }; '
               f'if {TMUX} has-session -t {exact} 2>/dev/null; then {TMUX} kill-session -t {exact}; fi')
    _remote_command(target, password, known_hosts, command, '停止tmux会话失败，请检查目标连接及tmux安装', **options)


def check_tmux(target, password, known_hosts, **options):
    _remote_command(target, password, known_hosts, 'tmux -V >/dev/null 2>&1',
                    '目标未安装 tmux 或无法运行，请先安装后启用断线保活', **options)


def _remote_command(target, password, known_hosts, command, failure_message, **options):
    client = connect(target, password, known_hosts, **options)
    channel = None
    try:
        channel = client.get_transport().open_session(timeout=10)
        channel.exec_command(command)
        deadline = time.monotonic() + 15
        while not channel.exit_status_ready():
            if channel.recv_ready(): channel.recv(65536)
            if channel.recv_stderr_ready(): channel.recv_stderr(65536)
            if time.monotonic() >= deadline: raise TimeoutError('tmux远端操作超时')
            time.sleep(.01)
        if channel.recv_exit_status() != 0: raise RuntimeError(failure_message)
    finally:
        if channel is not None: channel.close()
        client.close()
