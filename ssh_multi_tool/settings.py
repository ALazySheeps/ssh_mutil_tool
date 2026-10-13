"""Validated connection settings shared by preflight and helpers."""


def connection_options(preferences):
    result = {}
    for key, default in (("connect_timeout", 10), ("auth_timeout", 15)):
        try: value = int(preferences.get(key, str(default)))
        except (ValueError, TypeError): raise ValueError("超时设置须为1至120秒的整数")
        if not 1 <= value <= 120: raise ValueError("超时设置须为1至120秒的整数")
        result[key] = value
    return result


def validate_debug_slot(slot):
    if not isinstance(slot, dict) or set(slot) != {"name", "commands"}:
        raise ValueError("调试槽位须包含名称和命令")
    if any(not isinstance(value, str) or "\0" in value for value in slot.values()):
        raise ValueError("调试槽位名称和命令须为文本，且不能含空字符")
    if not slot["name"].strip(): raise ValueError("调试槽位名称不能为空")
    return dict(slot)


def debug_slots(preferences):
    result = []
    for index in range(1, 5):
        result.append(validate_debug_slot({
            "name": preferences.get(f"debug_slot_{index}_name", "HTOP" if index == 1 else f"调试窗口 {index}"),
            "commands": preferences.get(f"debug_slot_{index}_commands", "htop" if index == 1 else "")}))
    return result


def debug_preferences(slots):
    if not isinstance(slots, list) or len(slots) != 4: raise ValueError("特别调试窗口须保留四个槽位")
    result = {}
    for index, slot in enumerate(slots, 1):
        for key, value in validate_debug_slot(slot).items(): result[f"debug_slot_{index}_{key}"] = value
    return result
