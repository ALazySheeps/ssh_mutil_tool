"""Strict, detached configuration validation."""
import copy
import math
import re


def _fields(data, required, optional=()):
    if not isinstance(data, dict):
        raise ValueError("配置必须是对象")
    missing = set(required) - data.keys()
    unknown = data.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f"缺少字段：{sorted(missing)}；未知字段：{sorted(unknown)}")


def _text(data, key, required=False):
    value = data[key]
    if not isinstance(value, str) or "\0" in value or (required and not value.strip()):
        raise ValueError(f"{key} 必须是{'非空' if required else ''}文本，且不能含空字符")
    return value


def _id(data):
    value = _text(data, "id", True)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("ID仅允许字母、数字、下划线和横线（最多128字符）")


def validate_profile(data: dict) -> dict:
    _fields(data, ("schema_version", "id", "name", "description", "common_init", "windows"))
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("不支持的配置版本，仅支持版本1")
    _id(data); _text(data, "name", True)
    _text(data, "description"); _text(data, "common_init")
    windows = data["windows"]
    if not isinstance(windows, list) or not 1 <= len(windows) <= 32:
        raise ValueError("每个配置须包含1至32个窗口")
    seen = set(); enabled = False
    for window in windows:
        _fields(window, ("id", "name", "enabled", "working_directory", "commands", "start_delay_seconds"))
        _id(window); _text(window, "name", True)
        if window["id"] in seen:
            raise ValueError("窗口ID重复")
        seen.add(window["id"])
        if type(window["enabled"]) is not bool:
            raise ValueError("enabled 必须是布尔值")
        enabled |= window["enabled"]
        _text(window, "commands", window["enabled"])
        directory = _text(window, "working_directory")
        if directory and not (directory.startswith("/") or directory.startswith("~/")):
            raise ValueError("工作目录须为空、绝对路径或以 ~/ 开头")
        delay = window["start_delay_seconds"]
        if type(delay) not in (int, float) or not 0 <= delay <= 3600 or not math.isfinite(delay):
            raise ValueError("启动延迟须为0至3600秒的有限数值")
    if not enabled:
        raise ValueError("至少启用一个窗口")
    return copy.deepcopy(data)


def is_local(target):
    return isinstance(target, dict) and target.get("mode") == "local"


def local_target():
    return {"id": "__local__", "name": "本机", "mode": "local"}


def validate_target(data: dict) -> dict:
    if is_local(data):
        _fields(data, ("id", "name", "mode"))
        _id(data); _text(data, "name", True)
        return copy.deepcopy(data)
    _fields(data, ("id", "name", "host", "port", "username"))
    _id(data)
    for field in ("name", "host", "username"):
        _text(data, field, True)
    if any(char.isspace() for char in data["host"]) or data["host"].startswith("-"):
        raise ValueError("主机名或IP不能包含空白或以横线开头")
    if type(data["port"]) is not int or not 1 <= data["port"] <= 65535:
        raise ValueError("SSH端口须为1至65535的整数")
    return copy.deepcopy(data)
