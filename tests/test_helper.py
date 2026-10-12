import io
import tempfile
import unittest
import threading
from pathlib import Path

from ssh_multi_tool.helper import PhaseParser, run_session
from ssh_multi_tool.scripts import build_script
from ssh_multi_tool.ssh import UnknownHost, trust_host
from ssh_server import SSHServer
from test_models import profile


class HelperTests(unittest.TestCase):
    def test_cancelled_session_does_not_connect(self):
        stop = threading.Event(); stop.set(); events = []
        snapshot = {"marker": "a" * 32, "script": "echo hello"}
        self.assertEqual(run_session(snapshot, events.append, output=io.BytesIO(),
                                     interactive=False, stop=stop), 0)
        self.assertEqual(events[-1]["state"], "closed")

    def test_large_bidirectional_flow_does_not_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = SSHServer(output_before_script=b"x" * (3 * 1024 * 1024))
            try:
                hosts = Path(tmp) / "hosts"; trust_host(hosts, UnknownHost(f"[127.0.0.1]:{server.port}", server.key))
                data = profile(); data["common_init"] = "#" + "a" * (256 * 1024)
                marker = "c" * 32
                snapshot = {"target": server.target(), "password": "test-password", "known_hosts": str(hosts),
                            "script": build_script(data, data["windows"][0], marker), "marker": marker}
                result = []; output = io.BytesIO()
                worker = threading.Thread(target=lambda: result.append(run_session(snapshot, lambda e: None,
                                               output=output, interactive=False)), daemon=True)
                worker.start(); worker.join(timeout=5)
                self.assertFalse(worker.is_alive(), "client must drain output while script send window is blocked")
                self.assertEqual(result, [0]); self.assertEqual(len(server.script), len(snapshot["script"].encode()))
            finally: server.close()

    def test_missing_exit_status_is_disconnected(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = SSHServer(exit_code=None)
            try:
                hosts = Path(tmp) / "hosts"; trust_host(hosts, UnknownHost(f"[127.0.0.1]:{server.port}", server.key))
                data = profile(); marker = "d" * 32; events = []
                snapshot = {"target": server.target(), "password": "test-password", "known_hosts": str(hosts),
                            "script": build_script(data, data["windows"][0], marker), "marker": marker}
                result = run_session(snapshot, events.append, output=io.BytesIO(), interactive=False)
                self.assertEqual(result, 255); self.assertEqual(events[-1]["state"], "disconnected")
            finally: server.close()

    def test_split_control_events_and_ordinary_output(self):
        marker = "a" * 32; parser = PhaseParser(marker)
        visible = bytearray(); phases = []
        payload = b"normal initializing\n\x1e" + marker.encode() + b":running\x1fhello\x1eother"
        for byte in payload:
            output, events = parser.feed(bytes([byte])); visible.extend(output); phases.extend(events)
        visible.extend(parser.finish())
        self.assertEqual(bytes(visible), b"normal initializing\nhello\x1eother")
        self.assertEqual(phases, ["running"])

    def test_real_channel_transfers_script_and_reports_result(self):
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                server = SSHServer(exit_code=code)
                try:
                    hosts = Path(tmp) / "hosts"; trust_host(hosts, UnknownHost(f"[127.0.0.1]:{server.port}", server.key))
                    data = profile(); marker = "b" * 32
                    snapshot = {"target": server.target(), "password": "test-password", "known_hosts": str(hosts),
                                "script": build_script(data, data["windows"][0], marker), "marker": marker}
                    events = []; output = io.BytesIO()
                    result = run_session(snapshot, events.append, output=output, interactive=False)
                    self.assertEqual(result, code)
                    self.assertEqual(output.getvalue(), "hello 中文\n".encode())
                    self.assertEqual(server.script, snapshot["script"].encode())
                    self.assertEqual([e["state"] for e in events],
                                     ["connecting", "initializing", "running", "succeeded" if code == 0 else "failed"])
                finally: server.close()
