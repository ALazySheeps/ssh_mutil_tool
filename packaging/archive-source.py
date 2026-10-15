"""Produce a portable source archive without virtual environments or local state."""
import hashlib
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    output = root / "dist" / "ssh-multi-tool-0.1.0-source.zip"
    output.parent.mkdir(exist_ok=True)
    files = [root / name for name in ("README.md", "LICENSE", "pyproject.toml", "requirements.txt", "run.py", "start.sh", "install-ubuntu.sh", ".gitignore")]
    for directory in ("ssh_multi_tool", "examples", "tests", "docs", "packaging", ".github"):
        files.extend(path for path in (root / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo.from_file(path, "ssh-multi-tool/" + path.relative_to(root).as_posix())
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix == ".sh" else 0o100644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print(f"{output}\n{output.stat().st_size} bytes\nSHA256 {digest}")


if __name__ == "__main__": main()
