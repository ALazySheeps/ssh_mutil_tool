"""Validated connection settings shared by preflight and helpers."""


def connection_options(preferences):
    result = {}
    for key, default in (("connect_timeout", 10), ("auth_timeout", 15)):
        try: value = int(preferences.get(key, str(default)))
        except (ValueError, TypeError): raise ValueError("超时设置须为1至120秒的整数")
        if not 1 <= value <= 120: raise ValueError("超时设置须为1至120秒的整数")
        result[key] = value
    return result
