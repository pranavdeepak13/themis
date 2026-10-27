"""Best-effort clipboard copy through the OS (pbcopy on macOS). No network."""

from __future__ import annotations

import shutil
import subprocess


def copy_text(text: str) -> bool:
    """True if an OS clipboard tool took the text. The UI also tries the terminal (OSC 52) route."""
    tool = shutil.which("pbcopy")
    if not tool:
        return False
    try:
        subprocess.run([tool], input=text.encode(), check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return True
