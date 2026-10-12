"""Construct one Bash program per window, preserving shell context."""
import shlex

from .models import validate_profile


def build_script(profile: dict, window: dict, marker: str = "") -> str:
    validate_profile(profile)
    directory = window["working_directory"]
    if directory.startswith("~/"):
        directory_expr = '"$HOME"/' + shlex.quote(directory[2:])
    else:
        directory_expr = shlex.quote(directory)
    lines = ["set -e", "set -o pipefail", 'cd -- "$HOME"']
    if marker:
        lines.append(f"printf '\\036{marker}:initializing\\037'")
    lines.append(profile["common_init"])
    if directory: lines.append("cd -- " + directory_expr)
    if marker:
        lines.append(f"printf '\\036{marker}:running\\037'")
    lines.extend([window["commands"], ""])
    return "\n".join(lines)


def build_interactive_script(profile: dict, marker: str) -> str:
    data = validate_profile(profile)
    return "\n".join(["set -e", "set -o pipefail", 'cd -- "$HOME"',
                      f"printf '\\036{marker}:initializing\\037'", data["common_init"],
                      f"printf '\\036{marker}:interactive\\037'",
                      "exec bash --noprofile --norc -i", ""])
