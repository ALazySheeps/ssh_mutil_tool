import threading
import time
import unittest
from ssh_multi_tool.reporting import Reporter


class ReporterTests(unittest.TestCase):
    def test_reports_heartbeat_and_terminal_result_without_blocking_emitter(self):
        sent = []; arrived = threading.Event()
        def send(event): sent.append(event); arrived.set()
        reporter = Reporter(send, heartbeat_interval=.03)
        self.addCleanup(reporter.close)
        reporter.emit({"state": "running"})
        self.assertTrue(arrived.wait(1)); time.sleep(.08)
        reporter.emit({"state": "succeeded", "exit_code": 0}); reporter.close()
        self.assertGreaterEqual(len(sent), 2)
        self.assertEqual(sent[-1]["state"], "succeeded")

    def test_gui_disappearance_does_not_interrupt_session(self):
        def unavailable(event): raise OSError("GUI closed")
        reporter = Reporter(unavailable, heartbeat_interval=.01)
        reporter.emit({"state": "connecting"})
        reporter.close()
        reporter.emit({"state": "running"})  # no exception to session after GUI is gone
