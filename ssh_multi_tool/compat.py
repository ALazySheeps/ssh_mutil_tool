"""Small standard-library fallbacks for supported Python/Ubuntu versions."""
import os
from pathlib import Path
import re
import shlex


def read_os_release(path=Path('/etc/os-release')):
    result = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key): continue
        words = shlex.split(value, comments=True)
        result[key] = words[0] if len(words) == 1 else value
    return result


def platform_tag(path=Path('/etc/os-release')):
    data = read_os_release(path)
    if not data.get('ID') or not data.get('VERSION_ID'): raise ValueError('发行版信息缺少ID或VERSION_ID')
    return re.sub(r'[^A-Za-z0-9._-]', '-', data['ID'] + data['VERSION_ID'])


def _process_stat(pid, proc_root):
    content = (Path(proc_root) / str(pid) / 'stat').read_text(encoding='utf-8')
    fields = content[content.rfind(')') + 1:].split()
    if len(fields) < 20: raise ValueError('Invalid /proc process metadata')
    return fields[0], fields[19]


def process_identity(pid, proc_root=Path('/proc')):
    try: return _process_stat(pid, proc_root)[1]
    except (OSError, ValueError): return None


def process_is_alive(pid, identity, proc_root=Path('/proc')):
    try:
        state, started = _process_stat(pid, proc_root)
        return state not in {'Z', 'X'} and (identity is None or started == identity)
    except FileNotFoundError: return False
    except (OSError, ValueError):
        # If /proc is inaccessible, do not mistake permission errors for process death.
        try: os.kill(pid, 0)
        except ProcessLookupError: return False
        except OSError: pass
        return True


if __name__ == '__main__':
    print(platform_tag())
