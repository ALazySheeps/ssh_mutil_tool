import queue
import threading
import time
import unittest
from unittest.mock import patch

from ssh_multi_tool.broker import TokenVault
from ssh_multi_tool.launcher import Launcher, schedule
from test_models import profile


class FakeBroker:
    path = "/local/sock"
    def __init__(self): self.vault = TokenVault(); self.events = queue.Queue()
    def issue(self, snapshot): return self.vault.issue(snapshot)
    def cancel(self, token): self.vault.cancel(token)
    def close(self): self.vault.close()


class LauncherTests(unittest.TestCase):
    def target(self): return {"id": "t", "name": "T", "host": "localhost", "port": 22, "username": "u"}

    def test_schedule_uses_common_epoch_and_stable_tie_order(self):
        data = profile(); base = data["windows"][0]
        data["windows"] = [dict(base, id="a", start_delay_seconds=3),
                           dict(base, id="b", start_delay_seconds=0),
                           dict(base, id="c", start_delay_seconds=3)]
        self.assertEqual([(delay, w["id"]) for delay, w in schedule(data)], [(0, "b"), (3, "a"), (3, "c")])

    def test_preflight_failure_does_not_open_windows(self):
        calls = []
        def fail(*args): raise ValueError("bad password")
        launch = Launcher("hosts", preflight_fn=fail, broker_factory=FakeBroker,
                          open_fn=lambda *args: calls.append(args))
        self.addCleanup(launch.close)
        with self.assertRaises(ValueError): launch.start(self.target(), "secret", profile())
        self.assertEqual(calls, [])

    def test_failure_does_not_cancel_other_window_and_snapshot_is_detached(self):
        calls = []; broker = FakeBroker()
        def open_window(path, token, title):
            snapshot = broker.vault.claim(token); calls.append(snapshot)
            if len(calls) == 1: raise OSError("terminal failure")
        launch = Launcher("hosts", preflight_fn=lambda *args: None, broker_factory=lambda: broker, open_fn=open_window)
        self.addCleanup(launch.close)
        data = profile(); data["windows"].append(dict(data["windows"][0], id="b"))
        launch.start(self.target(), "secret", data)
        data["common_init"] = "changed"
        launch.thread.join(timeout=2)
        self.assertEqual(len(calls), 2)
        self.assertIn("export A=10", calls[1]["script"])
        self.assertEqual(launch.tasks["a"]["state"], "failed")
        self.assertEqual(launch.tasks["b"]["state"], "opening")
        with self.assertRaises(ValueError): launch.start(self.target(), "secret", profile())

    def test_cancel_pending_does_not_start_delayed_tasks(self):
        calls = []; data = profile(); data["windows"][0]["start_delay_seconds"] = 10
        launch = Launcher("hosts", preflight_fn=lambda *args: None, broker_factory=FakeBroker,
                          open_fn=lambda *args: calls.append(args))
        self.addCleanup(launch.close)
        launch.start(self.target(), "secret", data); launch.cancel_pending(); launch.thread.join(timeout=1)
        self.assertEqual(calls, []); self.assertEqual(launch.tasks["a"]["state"], "cancelled")

    def test_lost_helper_becomes_unknown_and_no_longer_blocks_start(self):
        broker = FakeBroker(); launch = Launcher("hosts", preflight_fn=lambda *args: None,
                                               broker_factory=lambda: broker, open_fn=lambda *args: None)
        self.addCleanup(launch.close)
        launch.start(self.target(), "secret", profile()); launch.thread.join(timeout=1)
        broker.events.put({"run_id": launch.run_id, "task_id": "a", "state": "running"})
        with patch("ssh_multi_tool.launcher.time.monotonic", return_value=100): launch.poll()
        self.assertTrue(launch.active())
        with patch("ssh_multi_tool.launcher.time.monotonic", return_value=121):
            self.assertEqual(launch.poll()["a"]["state"], "unknown")
        self.assertFalse(launch.active())
