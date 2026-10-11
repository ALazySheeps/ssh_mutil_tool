import tempfile
import unittest
from pathlib import Path

import paramiko

from ssh_multi_tool.ssh import UnknownHost, connect, trust_host, inspect_host
from ssh_server import SSHServer


class SSHTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.hosts = Path(self.tmp.name) / "known_hosts"
        self.server = SSHServer(); self.addCleanup(self.server.close)

    def test_unknown_host_has_fingerprint_and_can_be_explicitly_trusted(self):
        with self.assertRaises(UnknownHost) as caught:
            connect(self.server.target(), "test-password", self.hosts)
        self.assertTrue(caught.exception.fingerprint.startswith("SHA256:"))
        trust_host(self.hosts, caught.exception)
        client = connect(self.server.target(), "test-password", self.hosts)
        self.assertTrue(client.get_transport().is_authenticated()); client.close()

    def test_wrong_password_is_rejected(self):
        trust_host(self.hosts, UnknownHost(f"[127.0.0.1]:{self.server.port}", self.server.key))
        with self.assertRaises(paramiko.AuthenticationException):
            connect(self.server.target(), "wrong", self.hosts)

    def test_changed_key_is_rejected(self):
        other = paramiko.RSAKey.generate(2048)
        trust_host(self.hosts, UnknownHost(f"[127.0.0.1]:{self.server.port}", other))
        with self.assertRaises(paramiko.BadHostKeyException):
            connect(self.server.target(), "test-password", self.hosts)

    def test_inspect_does_not_require_password(self):
        found = inspect_host(self.server.target())
        self.assertEqual(found.key.asbytes(), self.server.key.asbytes())
