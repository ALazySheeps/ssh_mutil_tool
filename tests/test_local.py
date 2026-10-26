import copy
import io
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from ssh_multi_tool.broker import Broker, request
from ssh_multi_tool.gui import App
from ssh_multi_tool.helper import run_session
from ssh_multi_tool.launcher import Launcher
from ssh_multi_tool.models import validate_target
from ssh_multi_tool.scripts import build_script, build_interactive_script
from ssh_multi_tool.storage import Store
from ssh_multi_tool.terminal import helper_argv, target_key
from test_models import profile

LOCAL = {'id': '__local__', 'name': '本机', 'mode': 'local'}


class LocalLauncherTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(); self.calls = []
        def no_ssh(*args, **kwargs):
            raise AssertionError('Local launch must not use SSH or tmux')
        self.launch = Launcher('unused-hosts', preflight_fn=no_ssh, tmux_check_fn=no_ssh,
                               broker_factory=lambda: self.broker,
                               open_fn=lambda *args: self.calls.append(args))
        self.addCleanup(self.launch.close)
        self.launch.keep_alive = True

    def claim(self):
        return request(self.broker.path, {'op': 'claim', 'token': self.calls[-1][1]})

    def test_local_target_has_no_ssh_fields_and_distinct_terminal_group(self):
        self.assertEqual(validate_target(LOCAL), LOCAL)
        remote = {'host': 'localhost', 'port': 22, 'username': 'u'}
        self.assertNotEqual(target_key(LOCAL), target_key(remote))
        with self.assertRaises(ValueError):
            validate_target(dict(LOCAL, host='localhost'))

    def test_batch_launch_without_password_restarts_snapshot_and_cancels_delay(self):
        data = profile(); data['windows'].append(dict(data['windows'][0], id='b', start_delay_seconds=30))
        self.launch.start(LOCAL, '', data)
        deadline = time.monotonic() + 2
        while not self.calls and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.launch.window_specs['a']['tmux_session'])
        data['windows'][0]['commands'] = 'changed'
        # An unclaimed old terminal can close immediately, allowing restart.
        self.launch.restart_window('a', '')
        snapshot = self.claim()
        self.assertEqual(snapshot['target'], LOCAL)
        self.assertNotIn('changed', snapshot['script'])
        self.assertFalse(snapshot['password'])
        self.launch.close_all(); self.launch.thread.join(2)
        self.assertEqual(self.launch.poll()['b']['state'], 'closed')
        self.assertEqual(len(self.calls), 2)

    def test_single_manual_and_debug_launch_without_password(self):
        self.launch.start_window(LOCAL, '', profile(), 'a')
        self.assertEqual(self.claim()['target'], LOCAL)
        identifier = self.launch.open_remote_terminal(LOCAL, '', profile=profile())
        snapshot = self.claim()
        self.assertIn('interactive', snapshot['script'])
        self.assertIsNone(snapshot['tmux_session'])
        self.assertIn('本机终端', self.launch.shell_tasks[identifier]['name'])
        slot = {'name': 'debug', 'commands': 'printf debug'}
        debug = self.launch.open_debug_terminal(LOCAL, '', profile(), slot, slot_index=1)
        snapshot = self.claim()
        self.assertIn('printf debug', snapshot['script'])
        self.assertEqual(self.launch.find_debug_terminal(LOCAL, slot, 1), debug)
        request(self.broker.path, {'op': 'released', 'token': snapshot['control_token']})
        self.launch.restart_window(debug, '')
        self.assertIn('printf debug', self.claim()['script'])


@unittest.skipUnless(os.name == 'posix', 'Local execution requires Ubuntu')
class LocalExecutionTests(unittest.TestCase):
    def snapshot(self, data):
        marker = 'e' * 32
        return {'target': copy.deepcopy(LOCAL), 'password': '', 'known_hosts': 'unused',
                'script': build_script(data, data['windows'][0], marker), 'marker': marker}

    def test_local_bash_inherits_initialization_directory_and_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / '中文 work'; work.mkdir()
            init = root / 'init.bash'; init.write_text('export FROM_SOURCE=yes\n')
            data = profile(); data['common_init'] = f"source '{init}'\nexport A=10"
            data['windows'][0].update(working_directory=str(work),
                commands='printf "RESULT=%s:%s:%s\\n" "$A" "$FROM_SOURCE" "$PWD"; exit 7')
            output = io.BytesIO(); events = []
            code = run_session(self.snapshot(data), events.append, output=output, interactive=False)
            self.assertEqual(code, 7, events)
            self.assertIn(f'RESULT=10:yes:{work}'.encode(), output.getvalue())
            self.assertNotIn(b'\x1e', output.getvalue())
            self.assertEqual([event['state'] for event in events], ['initializing', 'running', 'failed'])
            self.assertEqual(events[-1]['exit_code'], 7)

    def test_failed_initialization_stops_local_commands(self):
        data = profile(); data['common_init'] = 'false'; data['windows'][0]['commands'] = 'echo MUST_NOT_RUN'
        output = io.BytesIO(); events = []
        code = run_session(self.snapshot(data), events.append, output=output, interactive=False)
        self.assertNotEqual(code, 0)
        self.assertNotIn(b'MUST_NOT_RUN', output.getvalue())
        self.assertEqual([e['state'] for e in events], ['initializing', 'failed'])

    def test_large_local_script_avoids_argument_size_limit(self):
        data = profile(); data['common_init'] = '#' + 'x' * (256 * 1024)
        data['windows'][0].update(working_directory='', commands='echo LARGE_OK')
        output = io.BytesIO(); events = []
        code = run_session(self.snapshot(data), events.append, output=output, interactive=False)
        self.assertEqual(code, 0, events)
        self.assertIn(b'LARGE_OK', output.getvalue())

    def interactive_helper(self, snapshot, keys, wait_state):
        import pty
        broker = Broker(); self.addCleanup(broker.close)
        token = broker.issue(dict(snapshot, run_id='r', task_id='a'))
        master, slave = pty.openpty()
        process = subprocess.Popen(helper_argv(broker.path, token), stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True)
        os.close(slave)
        try:
            events = []
            while True:
                event = broker.events.get(timeout=5); events.append(event)
                if event['state'] == wait_state:
                    os.write(master, keys)
                if event['state'] in {'succeeded', 'failed'}: break
            output = bytearray()
            # The result page stays open, so only read currently available bytes.
            while select.select([master], [], [], .1)[0]:
                output.extend(os.read(master, 65536))
            self.assertIsNone(process.poll())
            broker.cancel(token)
            code = process.wait(timeout=5)
            self.assertTrue(broker.is_closed(token))
            return code, bytes(output), events
        finally:
            if process.poll() is None: process.kill(); process.wait()
            os.close(master)

    def test_ctrl_c_interrupts_local_foreground_command(self):
        data = profile(); data['windows'][0].update(working_directory='', commands='sleep 30')
        code, output, events = self.interactive_helper(self.snapshot(data), b'\x03', 'running')
        self.assertEqual(code, 130, (output, events))
        self.assertEqual(events[-1]['exit_code'], 130)

    def test_local_interactive_shell_preserves_environment_and_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = profile(); data['common_init'] = f"export LOCAL_VALUE=yes\ncd '{tmp}'"
            snapshot = self.snapshot(data)
            snapshot['mode'] = 'shell'
            snapshot['script'] = build_interactive_script(data, snapshot['marker'])
            code, output, events = self.interactive_helper(snapshot,
                b'printf "VALUE=%s:%s\\n" "$LOCAL_VALUE" "$PWD"\nexit\n', 'interactive')
            self.assertEqual(code, 0, events)
            self.assertIn(f'VALUE=yes:{tmp}'.encode(), output)

    def test_redirecting_all_stdio_does_not_end_local_task(self):
        data = profile(); data['windows'][0].update(working_directory='',
            commands='exec </dev/null >/dev/null 2>&1; sleep 2.3; exit 0')
        events = []
        code = run_session(self.snapshot(data), events.append, output=io.BytesIO(), interactive=False)
        self.assertEqual(code, 0, events)
        self.assertEqual(events[-1]['state'], 'succeeded')

    def test_closing_local_interactive_shell_kills_ordinary_background_job(self):
        import pty
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / 'pid'
            snapshot = self.snapshot(profile()); snapshot['mode'] = 'shell'
            snapshot['script'] = build_interactive_script(None, snapshot['marker'])
            broker = Broker(); self.addCleanup(broker.close)
            token = broker.issue(dict(snapshot, run_id='r', task_id='a'))
            master, slave = pty.openpty()
            process = subprocess.Popen(helper_argv(broker.path, token), stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True)
            os.close(slave); child_pid = None
            try:
                while broker.events.get(timeout=5)['state'] != 'interactive': pass
                os.write(master, f"sleep 30 & echo $! > '{pidfile}'\n".encode())
                deadline = time.monotonic() + 3
                while (not pidfile.exists() or not pidfile.read_text()) and time.monotonic() < deadline:
                    time.sleep(.01)
                child_pid = int(pidfile.read_text())
                broker.cancel(token)
                self.assertEqual(process.wait(timeout=5), 0)
                stat = Path(f'/proc/{child_pid}/stat')
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z',
                                'Ordinary interactive background job survived close')
            finally:
                if process.poll() is None: process.kill(); process.wait()
                if child_pid:
                    try: os.kill(child_pid, 9)
                    except ProcessLookupError: pass
                os.close(master)

    def test_close_running_local_helper_kills_program_and_releases_broker(self):
        import pty
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / 'pid'
            data = profile(); data['windows'][0].update(working_directory='',
                commands=f"sleep 30 &\nprintf '%s' $! > '{pidfile}'\nwait")
            broker = Broker(); self.addCleanup(broker.close)
            token = broker.issue(dict(self.snapshot(data), run_id='r', task_id='a'))
            master, slave = pty.openpty()
            process = subprocess.Popen(helper_argv(broker.path, token), stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True)
            os.close(slave)
            try:
                deadline = time.monotonic() + 5
                while not pidfile.exists() and time.monotonic() < deadline:
                    if process.poll() is not None: break
                    time.sleep(.01)
                self.assertTrue(pidfile.exists(), 'Local program did not run')
                child_pid = int(pidfile.read_text())
                broker.cancel(token)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertTrue(broker.is_closed(token))
                # A killed child can briefly remain a zombie until its reaper runs.
                stat = Path(f'/proc/{child_pid}/stat')
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z')
            finally:
                if process.poll() is None: process.kill(); process.wait()
                os.close(master)


class LocalGuiTests(unittest.TestCase):
    def setUp(self):
        try: self.root = tk.Tk()
        except tk.TclError: self.skipTest('No desktop display available')
        self.root.withdraw(); self.addCleanup(self.root.destroy)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)); self.store.save_profile(profile())

    def test_local_selection_launches_without_target_or_password_and_restores(self):
        app = App(self.root, self.store); self.addCleanup(app.launcher.close)
        app.execution_mode.set('local'); app._execution_mode_changed()
        self.assertEqual(app.target(), LOCAL)
        self.assertTrue(app.password_entry.instate(['disabled']))
        self.assertTrue(app.keep_alive_button.instate(['disabled']))
        calls = []
        app.launcher.open_fn = lambda *args: calls.append(args)
        app.launcher.terminal_groups = None
        with patch('ssh_multi_tool.gui.check_terminal'), patch('ssh_multi_tool.gui.messagebox.showerror') as error:
            app.connection(True)
            deadline = time.monotonic() + 3
            while app.busy and time.monotonic() < deadline:
                self.root.update(); time.sleep(.01)
            error.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertFalse((Path(self.tmp.name) / 'targets.json').exists())
        app.launcher.close_all()
        self.assertEqual(self.store.preferences()['execution_mode'], 'local')
        app.destroy()
        restored = App(self.root, self.store); self.addCleanup(restored.launcher.close)
        self.assertEqual(restored.execution_mode.get(), 'local')
        restored.execution_mode.set('remote'); restored._execution_mode_changed()
        self.assertFalse(restored.password_entry.instate(['disabled']))
        with self.assertRaises(ValueError): restored.target()
