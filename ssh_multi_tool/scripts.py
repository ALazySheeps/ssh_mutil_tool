"""Construct one Bash program per window, preserving shell context."""
import shlex
import secrets
from typing import Optional

from .models import validate_profile


def _interactive_bash(body: str, *, rcfile: bool = False) -> str:
    # Keep large programs out of argv, without creating files on the target.
    delimiter = 'SMT_' + secrets.token_hex(24)
    source = "<(cat <<'" + delimiter + "'\n" + body + '\n' + delimiter + '\n)'
    if rcfile:
        return 'exec bash --noprofile --rcfile ' + source + ' -i\n'
    return 'exec bash --noprofile -i ' + source + '\n'


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
    # Interactive Bash reads ~/.bashrc before executing the task script, so its
    # interactive guard, functions and aliases behave like an ordinary terminal.
    return _interactive_bash("\n".join(lines))


def build_interactive_script(profile: Optional[dict], marker: str) -> str:
    data = validate_profile(profile) if profile is not None else None
    # Use one interactive shell for bashrc, initialization and user input. A
    # second shell would lose non-exported functions, aliases and shell settings.
    lines = ['cd -- "$HOME"', r"PS1='\u@\h:\w\$ '",
             'if [[ -f "$HOME/.bashrc" ]]; then source "$HOME/.bashrc"; fi',
             "set -e", "set -o pipefail"]
    if marker: lines.append(f"printf '\\036{marker}:initializing\\037'")
    if data is not None: lines.append(data["common_init"])
    lines.append(r"if [[ -z ${PS1:-} ]]; then PS1='\u@\h:\w\$ '; fi")
    if marker: lines.append(f"printf '\\036{marker}:interactive\\037'")
    # A failed command typed by the user must not close the manual terminal.
    lines.extend(["set +e", "set +o pipefail", ""])
    return _interactive_bash("\n".join(lines), rcfile=True)
