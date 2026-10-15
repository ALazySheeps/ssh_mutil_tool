"""Collect installed runtime licenses, failing rather than silently omitting one."""
import importlib.metadata
import shutil
import sys
import subprocess
import tkinter
from packaging.requirements import Requirement
from pathlib import Path


def main(destination):
    root = Path(destination); root.mkdir(parents=True, exist_ok=True)
    records = []
    for name in ("paramiko", "bcrypt", "cryptography", "invoke", "pynacl", "cffi", "pycparser", "pyinstaller"):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            # Paramiko 3 lists invoke under optional extras; Paramiko 4 requires it.
            requirements = [Requirement(raw) for raw in importlib.metadata.requires("paramiko") or []]
            required = any(req.name == name and (req.marker is None or req.marker.evaluate({"extra": ""}))
                           for req in requirements)
            if name == "invoke" and not required: continue
            raise
        directory = root / name; directory.mkdir(exist_ok=True)
        (directory / "METADATA.txt").write_text(distribution.read_text("METADATA") or "", encoding="utf-8")
        files = [entry for entry in distribution.files or []
                 if any(word in str(entry).upper() for word in ("LICENSE", "COPYING", "NOTICE"))]
        if not files: raise RuntimeError(f"未找到{name}的许可证文件，请补齐后再发布")
        for index, entry in enumerate(files):
            source = distribution.locate_file(entry)
            if source.is_file(): shutil.copyfile(source, directory / f"{index}-{source.name}")
        records.append(f"{name} {distribution.version}")
    # Python and Tcl/Tk library notices are not Python distribution metadata.
    license_candidates = [Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE",
                          Path("/usr/share/doc") / f"libpython{sys.version_info.major}.{sys.version_info.minor}-stdlib" / "copyright",
                          Path("/usr/share/doc/python3/copyright")]
    python_license = next((path for path in license_candidates if path.is_file()), None)
    if python_license is None: raise RuntimeError("未找到Python许可证文件")
    shutil.copyfile(python_license, root / "PYTHON_LICENSE.txt")
    for name in (f"libtcl{tkinter.TclVersion}", f"libtk{tkinter.TkVersion}"):
        path = Path("/usr/share/doc") / name / "copyright"
        if not path.is_file(): path = Path("/usr/share/doc") / name[3:] / "copyright"
        if not path.is_file(): raise RuntimeError(f"未找到{name}许可证，请在Ubuntu标准Tk构建环境执行")
        shutil.copyfile(path, root / f"{name}_COPYRIGHT.txt")
    # System libraries bundled by PyInstaller also need their distribution notices.
    cache = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, check=True).stdout
    libraries = {}
    for line in cache.splitlines():
        if " => " in line:
            source = Path(line.split(" => ", 1)[1].strip())
            libraries[source.name] = source
    native = root / "NATIVE_LIBRARY_NOTICES"; native.mkdir(exist_ok=True)
    for bundled in (root.parent / "_internal").glob("*.so*"):
        source = libraries.get(bundled.name)
        if source is None: continue  # Python extension modules are covered by package notices.
        result = subprocess.run(["dpkg-query", "-S", str(source.resolve())], capture_output=True, text=True)
        if result.returncode:
            result = subprocess.run(["dpkg-query", "-S", str(source)], capture_output=True, text=True)
        if result.returncode: raise RuntimeError(f"无法定位系统库许可：{bundled.name}")
        package = result.stdout.split(": ", 1)[0].split(":", 1)[0]
        notice = Path("/usr/share/doc") / package / "copyright"
        if not notice.is_file(): raise RuntimeError(f"缺少系统库许可：{package}")
        shutil.copyfile(notice, native / f"{package}_COPYRIGHT.txt")
        records.append(f"native {bundled.name}: {package}")
    (root / "INDEX.txt").write_text("\n".join(records) + "\nPython and Tcl/Tk notices included separately.\n", encoding="utf-8")


if __name__ == "__main__": main(sys.argv[1])
