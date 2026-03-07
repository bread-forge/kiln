"""Lifecycle hooks — shell scripts fired at key workflow events.

Hook scripts live at ``~/.kiln/hooks/on-<event>`` and must be executable.
Each hook receives event data as ``KILN_*`` environment variables.

Missing hook = silent skip.  Non-zero exit = warning printed, never fatal.

Events
------
on-claim        KILN_REPO  KILN_ISSUE  KILN_TITLE  KILN_BRANCH  KILN_MILESTONE
on-pr-open      KILN_REPO  KILN_ISSUE  KILN_PR  KILN_BRANCH
on-merge        KILN_REPO  KILN_PR  KILN_ISSUE
on-abandon      KILN_REPO  KILN_ISSUE
on-preflight    KILN_REPO  KILN_SPEC  KILN_ROUTE  KILN_SCORE  KILN_CONFIDENCE
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HOOKS_DIR = Path.home() / ".kiln" / "hooks"


def fire(event: str, env: dict[str, str]) -> None:
    """Fire a lifecycle hook if installed.

    Executes ``~/.kiln/hooks/<event>`` with the given env vars merged into the
    current environment.  Never raises — hook failures are printed as warnings.
    """
    script = HOOKS_DIR / event
    if not script.exists() or not os.access(script, os.X_OK):
        return
    hook_env = {**os.environ, **{k: str(v) for k, v in env.items()}}
    try:
        result = subprocess.run(
            [str(script)],
            env=hook_env,
            timeout=30,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            import sys
            print(
                f"[kiln hooks] warning: {event} exited {result.returncode}",
                file=sys.stderr,
            )
            if result.stderr:
                print(result.stderr.strip(), file=sys.stderr)
    except Exception as exc:
        import sys
        print(f"[kiln hooks] warning: {event} failed: {exc}", file=sys.stderr)
