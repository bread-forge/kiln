"""Thin subprocess wrappers for gh (GitHub CLI) and git.

All graph handlers, dispatch, and monitor modules import from here so the
invocation pattern is defined exactly once.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _gh(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
