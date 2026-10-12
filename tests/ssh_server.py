"""Small real SSH protocol server for client integration tests."""
import socket
import threading
import time
import logging

import paramiko

# Expected early disconnects during host-key rejection should not use lastResort stderr.
logging.getLogger("paramiko").addHandler(logging.NullHandler())


class Server(paramiko.ServerInterface):
    def __init__(self, owner): self.owner = owner
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if username == "user" and password == "test-password" else paramiko.AUTH_FAILED
    def get_allowed_auths(self, username): return "password"
    def check_channel_request(self, kind, channelid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return True
    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        self.owner.sizes.append((width, height)); return True
    def check_channel_shell_request(self, channel):
        threading.Thread(target=self.owner.shell, args=(channel,), daemon=True).start()
        return True
    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=self.owner.execute, args=(channel, command), daemon=True).start()
        return True


class SSHServer:
    def __init__(self, key=None, exit_code=0, output_before_script=b"", hold=False, phase="running"):
        self.key = key or paramiko.RSAKey.generate(2048); self.exit_code = exit_code
        self.listener = socket.socket(); self.listener.bind(("127.0.0.1", 0)); self.listener.listen()
        self.listener.settimeout(.1); self.port = self.listener.getsockname()[1]
        self.stop = threading.Event(); self.transports = []; self.sizes = []
        self.command = b""; self.script = b""; self.output = "hello 中文\n".encode()
        self.output_before_script = output_before_script; self.hold = hold; self.phase = phase
        self.shell_input = bytearray(); self.shell_received = threading.Event()
        self.thread = threading.Thread(target=self.accept, daemon=True); self.thread.start()

    def target(self):
        return {"id": "test", "name": "test", "host": "127.0.0.1", "port": self.port, "username": "user"}

    def accept(self):
        while not self.stop.is_set():
            try: connection, _ = self.listener.accept()
            except socket.timeout: continue
            except OSError: break
            transport = paramiko.Transport(connection, default_window_size=32768); self.transports.append(transport)
            transport.add_server_key(self.key)
            try: transport.start_server(server=Server(self))
            except (EOFError, OSError, paramiko.SSHException): transport.close()

    def execute(self, channel, command):
        import re
        time.sleep(.04)
        self.command = command
        marker = re.search(rb"([a-f0-9]{32}):ready", command).group(1)
        count = int(re.search(rb"head -c ([0-9]+)", command).group(1))
        channel.sendall(b"\x1e" + marker + b":ready\x1f")
        if self.output_before_script: channel.sendall(self.output_before_script)
        while len(self.script) < count:
            data = channel.recv(min(32768, count - len(self.script)))
            if not data: break
            self.script += data
        channel.sendall(b"\x1e" + marker + b":initializing\x1f")
        channel.sendall(b"\x1e" + marker + b":" + self.phase.encode() + b"\x1f" + self.output)
        while self.hold and not channel.closed and not self.stop.wait(.01): pass
        if channel.closed: return
        if self.exit_code is not None: channel.send_exit_status(self.exit_code)
        channel.close()

    def shell(self, channel):
        try:
            channel.sendall(self.output)
            if self.hold:
                channel.settimeout(.1)
                while not channel.closed and not self.stop.is_set():
                    try: data = channel.recv(4096)
                    except socket.timeout: continue
                    if not data: return
                    self.shell_input.extend(data); self.shell_received.set()
                    channel.sendall(data)
            else:
                channel.send_exit_status(0); channel.close()
        except (OSError, EOFError): pass

    def close(self):
        self.stop.set(); self.listener.close()
        for transport in self.transports: transport.close()
        self.thread.join(timeout=2)
