"""SSH connection and explicitly managed server trust."""
import base64
import hashlib
import os
import tempfile
import socket
from pathlib import Path

import paramiko

from .models import validate_target


class UnknownHost(Exception):
    def __init__(self, hostname, key):
        self.hostname = hostname; self.key = key
        self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        super().__init__(f"未信任的主机 {hostname}，指纹 {self.fingerprint}")


class AskHostPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        raise UnknownHost(hostname, key)


def connect(target: dict, password: str, known_hosts: Path, *, connect_timeout=10, auth_timeout=15):
    validate_target(target)
    client = paramiko.SSHClient()
    try:
        if Path(known_hosts).exists(): client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(AskHostPolicy())
        client.connect(target["host"], port=target["port"], username=target["username"], password=password,
                       look_for_keys=False, allow_agent=False, timeout=connect_timeout,
                       banner_timeout=connect_timeout, auth_timeout=auth_timeout, channel_timeout=connect_timeout)
        client.get_transport().set_keepalive(15)
        return client
    except BaseException:
        client.close(); raise


def preflight(target, password, known_hosts, **options):
    client = connect(target, password, known_hosts, **options)
    client.close()


def inspect_host(target, timeout=10):
    validate_target(target)
    connection = socket.create_connection((target["host"], target["port"]), timeout=timeout)
    transport = paramiko.Transport(connection)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        hostname = target["host"] if target["port"] == 22 else f"[{target['host']}]:{target['port']}"
        return UnknownHost(hostname, key)
    finally: transport.close(); connection.close()


def trust_host(path: Path, unknown: UnknownHost):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    hosts = paramiko.HostKeys()
    if path.exists(): hosts.load(str(path))
    hosts.add(unknown.hostname, unknown.key.get_name(), unknown.key)
    fd, temp = tempfile.mkstemp(prefix=".hosts-", dir=path.parent)
    os.close(fd)
    try:
        hosts.save(temp)
        if os.name != "nt": os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def error_text(error):
    if isinstance(error, paramiko.AuthenticationException): return "SSH密码或用户名验证失败"
    if isinstance(error, paramiko.BadHostKeyException): return "主机指纹发生变化；请核验设备身份后在主机信任管理中更新"
    if isinstance(error, UnknownHost): return str(error)
    if isinstance(error, TimeoutError): return "SSH连接或认证超时，请检查设备与网络"
    if isinstance(error, paramiko.SSHException): return "SSH会话失败，请检查服务器、信任记录及网络"
    if isinstance(error, OSError): return "连接或本机资源失败，请检查网络、权限及文件路径"
    return str(error)
