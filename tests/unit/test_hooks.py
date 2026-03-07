"""Unit tests for kiln.hooks."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from kiln.hooks import fire


@pytest.fixture
def hooks_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "hooks"
    d.mkdir()
    monkeypatch.setattr("kiln.hooks.HOOKS_DIR", d)
    return d


def _write_hook(hooks_dir: Path, event: str, script: str) -> Path:
    p = hooks_dir / event
    p.write_text(script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


class TestFire:
    def test_missing_hook_is_silent(self, hooks_dir: Path) -> None:
        # No script at hooks_dir/on-claim — should not raise
        fire("on-claim", {"KILN_REPO": "owner/repo", "KILN_ISSUE": "42"})

    def test_hook_receives_env(self, hooks_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.txt"
        _write_hook(
            hooks_dir,
            "on-claim",
            f"#!/bin/sh\necho \"$KILN_REPO $KILN_ISSUE\" > {out}\n",
        )
        fire("on-claim", {"KILN_REPO": "owner/repo", "KILN_ISSUE": "7"})
        assert out.read_text().strip() == "owner/repo 7"

    def test_non_executable_hook_is_skipped(self, hooks_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.txt"
        p = hooks_dir / "on-claim"
        p.write_text(f"#!/bin/sh\ntouch {out}\n")
        # intentionally NOT chmod +x
        fire("on-claim", {"KILN_REPO": "owner/repo", "KILN_ISSUE": "1"})
        assert not out.exists()

    def test_failing_hook_does_not_raise(
        self, hooks_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_hook(hooks_dir, "on-merge", "#!/bin/sh\nexit 1\n")
        fire("on-merge", {"KILN_REPO": "owner/repo", "KILN_PR": "5", "KILN_ISSUE": "3"})
        captured = capsys.readouterr()
        assert "warning" in captured.err

    def test_hook_env_vars_are_strings(self, hooks_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.txt"
        _write_hook(
            hooks_dir,
            "on-preflight",
            f"#!/bin/sh\necho \"$KILN_SCORE\" > {out}\n",
        )
        fire("on-preflight", {"KILN_REPO": "r/r", "KILN_SCORE": 7})  # int value
        assert out.read_text().strip() == "7"
