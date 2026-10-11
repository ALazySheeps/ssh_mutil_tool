import concurrent.futures
import os
import tempfile
import unittest
import socket
import struct
from unittest.mock import patch
from pathlib import Path

from ssh_multi_tool.broker import Broker, TokenVault, request


class TokenTests(unittest.TestCase):
    def test_claim_once_and_event_token_cannot_claim_credentials(self):
        vault = TokenVault()
        token = vault.issue({"task_id": "one", "run_id": "run", "password": "secret"})
        snapshot = vault.claim(token)
        self.assertEqual(snapshot["password"], "secret")
        with self.assertRaises(ValueError): vault.claim(token)
        with self.assertRaises(ValueError): vault.claim(snapshot["event_token"])
        self.assertTrue(vault.event(snapshot["event_token"], {"state": "running", "task_id": "one", "run_id": "run"}))
        with self.assertRaises(ValueError):
            vault.event(snapshot["event_token"], {"state": "running", "task_id": "other", "run_id": "run"})

    def test_expiration_and_cancellation(self):
        clock = [0]; vault = TokenVault(clock=lambda: clock[0])
        token = vault.issue({"task_id": "one", "run_id": "r"})
        clock[0] = 60
        with self.assertRaises(ValueError): vault.claim(token)
        token = vault.issue({"task_id": "two", "run_id": "r"})
        vault.cancel(token)
        with self.assertRaises(ValueError): vault.claim(token)

    def test_simultaneous_claim_has_one_winner(self):
        vault = TokenVault(); token = vault.issue({"task_id": "one", "run_id": "r"})
        def claim(_):
            try: vault.claim(token); return True
            except ValueError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(16))), 1)


@unittest.skipUnless(os.name == "posix", "Unix socket permissions require Linux")
class SocketTests(unittest.TestCase):
    def test_roundtrip_permissions_and_close(self):
        broker = Broker(); self.addCleanup(broker.close)
        self.assertEqual(Path(broker.path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(broker.path).parent.stat().st_mode & 0o777, 0o700)
        token = broker.issue({"task_id": "a", "run_id": "r", "password": "secret"})
        reply = request(broker.path, {"op": "claim", "token": token})
        self.assertEqual(reply["password"], "secret")
        request(broker.path, {"op": "event", "token": reply["event_token"],
                              "event": {"task_id": "a", "run_id": "r", "state": "running"}})
        self.assertEqual(broker.events.get(timeout=2)["state"], "running")
        with self.assertRaises(ValueError): request(broker.path, {"op": "claim", "token": token})
        broker.close(); self.assertFalse(Path(broker.path).exists())

    def test_wrong_peer_identity_is_rejected_without_consuming_token(self):
        broker = Broker(); self.addCleanup(broker.close)
        token = broker.issue({"task_id": "a", "run_id": "r", "password": "secret"})
        with patch("ssh_multi_tool.broker.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(ValueError): request(broker.path, {"op": "claim", "token": token})
        self.assertEqual(request(broker.path, {"op": "claim", "token": token})["password"], "secret")

    def test_oversized_frame_rejected_before_payload(self):
        broker = Broker(); self.addCleanup(broker.close)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2); client.connect(broker.path)
            client.sendall(struct.pack("!I", 3 * 1024 * 1024))
            self.assertTrue(client.recv(4096))
