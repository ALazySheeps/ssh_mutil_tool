"""Native GNOME smoke test. Run on a private bus: xvfb-run -a dbus-run-session -- python packaging/check-terminal.py."""
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ssh_multi_tool.terminal import TerminalGroups


def main():
    groups = TerminalGroups()
    target = dict(host='smoke-board', port=22, username='tester')
    # Short-lived local processes test Terminal's API without a remote device.
    command = [sys.executable, '-c', 'import time; time.sleep(8)']
    with patch('ssh_multi_tool.terminal.helper_argv', return_value=command):
        first = groups.open(target, '', 'first', 'Compatibility smoke 1')
        second = groups.open(target, '', 'second', 'Compatibility smoke 2')
        assert first['service'] == second['service']
        assert first['screen'] != second['screen']
        assert groups._live(first) and groups._live(second)
        print('Native GNOME Terminal: two managed tabs opened successfully')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not groups._live(first) and not groups._live(second):
            print('Native GNOME Terminal: finished tabs removed successfully')
            return
        time.sleep(.2)
    raise RuntimeError('Finished GNOME Terminal tabs did not close')


if __name__ == '__main__':
    main()
