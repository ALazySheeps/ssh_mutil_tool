"""User-local JSON storage; all mutations use atomic replacement."""
import json
import os
import tempfile
import uuid
import shutil
from pathlib import Path
from typing import Optional

from .settings import debug_slots
from .models import validate_profile, validate_target

MAX_BYTES = 1024 * 1024


def config_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "ssh-multi-tool"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ssh-multi-tool"


def atomic_json(path: Path, data) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(content) > MAX_BYTES:
        raise ValueError("文件超过1 MiB")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def read_json(path: Path):
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES: raise ValueError("文件超过1 MiB")
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result: raise ValueError(f"重复字段：{key}")
                result[key] = value
            return result
        return json.loads(content.decode("utf-8-sig"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"非法数值：{value}")))
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise ValueError(f"JSON格式错误：{exc}") from exc


class Store:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else config_root()
        self.profiles_dir = self.root / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.root.chmod(0o700); self.profiles_dir.chmod(0o700)
        self.known_hosts = self.root / "known_hosts"

    def _profile_path(self, identifier):
        import re
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
            raise ValueError("无效配置ID")
        return self.profiles_dir / f"{identifier}.json"

    def save_profile(self, data):
        data = validate_profile(data)
        atomic_json(self._profile_path(data["id"]), data)

    def load_profile(self, identifier):
        data = validate_profile(read_json(self._profile_path(identifier)))
        if data["id"] != identifier: raise ValueError("文件名和配置ID不一致")
        return data

    def list_profiles(self):
        results, errors = [], []
        for path in sorted(self.profiles_dir.glob("*.json")):
            try: results.append(self.load_profile(path.stem))
            except (OSError, ValueError) as exc: errors.append(f"{path.name}：{exc}")
        return results, errors

    def delete_profile(self, identifier):
        self._profile_path(identifier).unlink()

    def import_profile(self, path, overwrite=False):
        data = validate_profile(read_json(Path(path)))
        if self._profile_path(data["id"]).exists() and not overwrite:
            data["id"] = uuid.uuid4().hex; data["name"] += "（导入副本）"
        self.save_profile(data)
        return data

    def export_profile(self, identifier, path):
        atomic_json(Path(path), self.load_profile(identifier))

    def targets(self):
        path = self.root / "targets.json"
        if not path.exists(): return []
        data = read_json(path)
        if not isinstance(data, list): raise ValueError("目标列表格式错误")
        result = [validate_target(target) for target in data]
        if len({target["id"] for target in result}) != len(result): raise ValueError("目标ID重复")
        return result

    def save_targets(self, targets):
        data = [validate_target(target) for target in targets]
        if len({target["id"] for target in data}) != len(data): raise ValueError("目标ID重复")
        self._backup_if_invalid(self.root / "targets.json", self.targets)
        atomic_json(self.root / "targets.json", data)

    def _backup_if_invalid(self, path, read):
        if not path.exists(): return
        try: read()
        except ValueError:
            backup = path.with_name(path.name + ".recovery-" + uuid.uuid4().hex)
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as destination, path.open("rb") as source:
                    shutil.copyfileobj(source, destination)
                    destination.flush(); os.fsync(destination.fileno())
            except BaseException:
                backup.unlink(missing_ok=True); raise

    def preferences(self):
        path = self.root / "preferences.json"
        if not path.exists(): return {}
        data = read_json(path)
        self._validate_preferences(data)
        return data

    @staticmethod
    def _validate_preferences(data):
        allowed = {"last_target_id", "last_profile_id", "geometry", "terminal", "connect_timeout", "auth_timeout", "keep_alive"}
        allowed |= {f"debug_slot_{index}_{key}" for index in range(1, 5) for key in ("name", "commands")}
        if not isinstance(data, dict) or data.keys() - allowed:
            raise ValueError("未知偏好字段")
        if any(not isinstance(value, str) for value in data.values()): raise ValueError("偏好值须为文本")
        debug_slots(data)
        if data.get("keep_alive", "0") not in {"0", "1"}: raise ValueError("断线保活设置须为0或1")

    def save_preferences(self, data):
        self._validate_preferences(data)
        self._backup_if_invalid(self.root / "preferences.json", self.preferences)
        atomic_json(self.root / "preferences.json", data)
