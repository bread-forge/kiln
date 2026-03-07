"""Integration tests for graph handlers — mocked run_agent."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beads import BeadStore, GraphNode, PRBead, WorkBead

from kiln.agents.runner import RunResult
from kiln.config import Config
from kiln.graph.handlers.build import BuildHandler
from kiln.graph.handlers.merge import MergeHandler
from kiln.graph.handlers.research import ResearchHandler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> Config:
    return Config.from_env("owner/repo")


@pytest.fixture
def store(tmp_path: Path) -> BeadStore:
    return BeadStore(tmp_path / "beads", "owner/repo")


def fake_run_result(
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = None,
    duration_ms: float = 100.0,
) -> RunResult:
    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr or "",
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# BuildHandler
# ---------------------------------------------------------------------------


_SETUP_WORKSPACE = "kiln.graph.handlers.build._setup_workspace"
_VERIFY_SCOPE = "kiln.graph.handlers.build._verify_pr_scope"


class TestBuildHandler:
    def test_success_no_issue(self, config: Config, store: BeadStore, tmp_path: Path) -> None:
        node = GraphNode(
            id="v1-build-core",
            type="build",
            context={
                "module": "core",
                "files": ["src/core.py"],
                "milestone": "v1.0",
            },
        )

        with (
            patch(_SETUP_WORKSPACE, return_value=None),
            patch(_VERIFY_SCOPE, return_value=[]),
            patch("kiln.graph.handlers.build.run_agent", new_callable=AsyncMock) as mock_run,
            patch("kiln.graph.handlers.build._get_pr_number", return_value=42),
            patch("kiln.graph.handlers.build._claim_issue"),
            patch("kiln.graph.handlers.build._unclaim_issue"),
            patch(
                "kiln.agents.assessor.assess_and_allocate", new_callable=AsyncMock
            ) as mock_assess,
        ):
            from kiln.agents.assessor import (
                AllocationResult,
                ComplexityEstimate,
                ComplexityTier,
            )

            mock_assess.return_value = (
                AllocationResult(model="claude-sonnet-4-6", tier=ComplexityTier.MEDIUM),
                ComplexityEstimate(
                    tier=ComplexityTier.MEDIUM, confidence=0.8, reasoning="test", model_used="test"
                ),
            )
            mock_run.return_value = fake_run_result(exit_code=0)

            handler = BuildHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert result.success
        assert result.output["pr_number"] == 42

    def test_agent_failure(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-build-core",
            type="build",
            context={"module": "core", "milestone": "v1.0"},
        )

        with (
            patch(_SETUP_WORKSPACE, return_value=None),
            patch("kiln.graph.handlers.build.run_agent", new_callable=AsyncMock) as mock_run,
            patch(
                "kiln.agents.assessor.assess_and_allocate", new_callable=AsyncMock
            ) as mock_assess,
        ):
            from kiln.agents.assessor import (
                AllocationResult,
                ComplexityEstimate,
                ComplexityTier,
            )

            mock_assess.return_value = (
                AllocationResult(model="claude-sonnet-4-6", tier=ComplexityTier.MEDIUM),
                ComplexityEstimate(
                    tier=ComplexityTier.MEDIUM, confidence=0.8, reasoning="test", model_used="test"
                ),
            )
            mock_run.return_value = fake_run_result(exit_code=1, stderr="error")

            handler = BuildHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert not result.success

    def test_no_pr_created(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-build-core",
            type="build",
            context={"module": "core", "milestone": "v1.0"},
        )

        with (
            patch(_SETUP_WORKSPACE, return_value=None),
            patch("kiln.graph.handlers.build.run_agent", new_callable=AsyncMock) as mock_run,
            patch("kiln.graph.handlers.build._get_pr_number", return_value=None),
            patch(
                "kiln.agents.assessor.assess_and_allocate", new_callable=AsyncMock
            ) as mock_assess,
        ):
            from kiln.agents.assessor import (
                AllocationResult,
                ComplexityEstimate,
                ComplexityTier,
            )

            mock_assess.return_value = (
                AllocationResult(model="claude-sonnet-4-6", tier=ComplexityTier.MEDIUM),
                ComplexityEstimate(
                    tier=ComplexityTier.MEDIUM, confidence=0.8, reasoning="test", model_used="test"
                ),
            )
            mock_run.return_value = fake_run_result(exit_code=0)

            handler = BuildHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert not result.success
        assert "no PR" in result.error

    def test_scope_violation_fails(self, config: Config, store: BeadStore) -> None:
        """PR that touches out-of-scope files should fail with an error."""
        node = GraphNode(
            id="v1-build-core",
            type="build",
            context={"module": "core", "files": ["src/core.py"], "milestone": "v1.0"},
        )

        with (
            patch(_SETUP_WORKSPACE, return_value=None),
            patch(_VERIFY_SCOPE, return_value=["src/other.py"]),
            patch("kiln.graph.handlers.build.run_agent", new_callable=AsyncMock) as mock_run,
            patch("kiln.graph.handlers.build._get_pr_number", return_value=42),
            patch("kiln.graph.handlers.build._gh"),
            patch(
                "kiln.agents.assessor.assess_and_allocate", new_callable=AsyncMock
            ) as mock_assess,
        ):
            from kiln.agents.assessor import (
                AllocationResult,
                ComplexityEstimate,
                ComplexityTier,
            )

            mock_assess.return_value = (
                AllocationResult(model="claude-sonnet-4-6", tier=ComplexityTier.MEDIUM),
                ComplexityEstimate(
                    tier=ComplexityTier.MEDIUM, confidence=0.8, reasoning="test", model_used="test"
                ),
            )
            mock_run.return_value = fake_run_result(exit_code=0)
            handler = BuildHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert not result.success
        assert "scope violation" in result.error

    def test_uses_plan_artifact_for_assessment(self, store: BeadStore) -> None:
        from beads.types import PlanArtifact

        # Use a config with no model override so risk_flags take effect
        config_no_override = Config(repo="owner/repo", model="")
        artifact = PlanArtifact(
            milestone="v1.0",
            modules=["core"],
            files_per_module={"core": ["src/core.py"]},
            approach="simple",
            confidence=0.9,
            risk_flags=["security"],  # should force opus
        )
        # Store the plan node bead so BuildHandler._assess() can load the artifact
        plan_node = GraphNode(
            id="v1-plan",
            type="plan",
            state="done",
            output={"artifact": artifact.model_dump(), "new_nodes": []},
        )
        store.write_node(plan_node)
        node = GraphNode(
            id="v1-build-core",
            type="build",
            context={
                "module": "core",
                "milestone": "v1.0",
                "plan_node_id": "v1-plan",
            },
        )

        with (
            patch(_SETUP_WORKSPACE, return_value=None),
            patch(_VERIFY_SCOPE, return_value=[]),
            patch("kiln.graph.handlers.build.run_agent", new_callable=AsyncMock) as mock_run,
            patch("kiln.graph.handlers.build._get_pr_number", return_value=99),
        ):
            mock_run.return_value = fake_run_result(exit_code=0)
            handler = BuildHandler(store=store)
            result = asyncio.run(handler.execute(node, config_no_override))

        assert result.success
        assert result.output["model"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# MergeHandler
# ---------------------------------------------------------------------------


class TestMergeHandler:
    def test_ci_passing_merges(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-build-core-merge",
            type="merge",
            context={"pr_number": 42, "issue_number": 1, "branch": "1-core"},
        )
        work_bead = WorkBead(issue_number=1, repo="owner/repo", title="Core")
        pr_bead = PRBead(pr_number=42, repo="owner/repo", issue_number=1, branch="1-core")
        store.write_work_bead(work_bead)
        store.write_pr_bead(pr_bead)

        def _gh_side_effect(*args):
            result = MagicMock()
            result.returncode = 0
            # gh pr view --json statusCheckRollup returns a dict (not array)
            # empty checks = CI passing
            result.stdout = json.dumps({"statusCheckRollup": []})
            result.stderr = ""
            return result

        with patch("kiln.graph.handlers.merge._gh", side_effect=_gh_side_effect):
            handler = MergeHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert result.success
        assert result.output["merged"] is True

        merged_pr = store.read_pr_bead(42)
        assert merged_pr is not None
        assert merged_pr.state == "merged"
        closed_work = store.read_work_bead(1)
        assert closed_work is not None
        assert closed_work.state == "closed"

    def test_ci_still_running_fails(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-merge",
            type="merge",
            context={"pr_number": 10},
        )

        with patch("kiln.graph.handlers.merge._pr_ci_passing", return_value=None):
            handler = MergeHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert not result.success
        assert "CI still running" in result.error

    def test_ci_failing(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-merge",
            type="merge",
            context={"pr_number": 10},
        )

        with patch("kiln.graph.handlers.merge._pr_ci_passing", return_value=False):
            handler = MergeHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert not result.success
        assert "CI failing" in result.error

    def test_no_pr_number(self, config: Config) -> None:
        node = GraphNode(id="v1-merge", type="merge", context={})
        handler = MergeHandler()
        result = asyncio.run(handler.execute(node, config))
        assert not result.success
        assert "no pr_number" in result.error


# ---------------------------------------------------------------------------
# ResearchHandler
# ---------------------------------------------------------------------------


class TestResearchHandler:
    def test_no_unknowns_succeeds(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-research-empty",
            type="research",
            context={"milestone": "v1.0", "unknowns": []},
        )
        handler = ResearchHandler(store=store)
        result = asyncio.run(handler.execute(node, config))
        assert result.success
        assert result.output["findings"] == ""

    def test_runs_agent_and_stores_findings(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-research-auth",
            type="research",
            context={"milestone": "v1.0", "unknowns": ["Which JWT library?"]},
        )

        with patch("kiln.graph.handlers.research.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_run_result(exit_code=0, stdout="# Research\n\nUse PyJWT.")
            handler = ResearchHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert result.success
        assert "PyJWT" in result.output["findings"]

        stored = store.read_research_findings("v1-research-auth")
        assert stored is not None
        assert "PyJWT" in stored

    def test_agent_failure(self, config: Config, store: BeadStore) -> None:
        node = GraphNode(
            id="v1-research-fail",
            type="research",
            context={"milestone": "v1.0", "unknowns": ["Something?"]},
        )

        with patch("kiln.graph.handlers.research.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_run_result(exit_code=1, stderr="timeout")
            handler = ResearchHandler(store=store)
            result = asyncio.run(handler.execute(node, config))

        assert not result.success

    def test_restricted_tools_passed(self, config: Config, store: BeadStore) -> None:
        """ResearchHandler should pass WebSearch/WebFetch only."""
        node = GraphNode(
            id="v1-research-tools",
            type="research",
            context={"milestone": "v1.0", "unknowns": ["Q?"]},
        )

        with patch("kiln.graph.handlers.research.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_run_result(exit_code=0, stdout="findings")
            handler = ResearchHandler(store=store)
            asyncio.run(handler.execute(node, config))

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("allowed_tools") == ["WebSearch", "WebFetch", "Bash", "Glob", "Grep", "Read"]
        assert call_kwargs.get("timeout_minutes") == 15


# ---------------------------------------------------------------------------
# _read_codebase_summary tests
# ---------------------------------------------------------------------------


class TestReadCodebaseSummary:
    """Tests for the codebase context extractor used by the plan LLM."""

    from kiln.graph.handlers.plan import _read_codebase_summary as _rcs  # type: ignore[attr-defined]

    def _rcs(self, path: Path) -> str:
        from kiln.graph.handlers.plan import _read_codebase_summary
        return _read_codebase_summary(str(path))

    def test_returns_empty_for_none(self) -> None:
        from kiln.graph.handlers.plan import _read_codebase_summary
        assert _read_codebase_summary(None) == ""

    def test_returns_empty_for_missing_path(self, tmp_path: Path) -> None:
        from kiln.graph.handlers.plan import _read_codebase_summary
        assert _read_codebase_summary(str(tmp_path / "nonexistent")) == ""

    def test_includes_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# project instructions\nModule table here.")
        result = self._rcs(tmp_path)
        assert "project instructions" in result
        assert "CLAUDE.md" in result

    def test_includes_standards(self, tmp_path: Path) -> None:
        std = tmp_path / "standards"
        std.mkdir()
        (std / "councils.md").write_text("All councils must persist turns to DB.")
        result = self._rcs(tmp_path)
        assert "persist turns to DB" in result
        assert "Project standards" in result

    def test_standards_appear_before_inventory(self, tmp_path: Path) -> None:
        std = tmp_path / "standards"
        std.mkdir()
        (std / "api.md").write_text("API standard text.")
        src = tmp_path / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "mod.py").write_text("def run(): pass\n")
        result = self._rcs(tmp_path)
        assert result.index("API standard text") < result.index("Source inventory")

    def test_extracts_protocol_contract(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "base.py").write_text(
            "from typing import Protocol\n\n"
            "class CouncilPlugin(Protocol):\n"
            "    council_id: str\n"
            "    async def run(self, topic: dict) -> dict: ...\n"
        )
        result = self._rcs(tmp_path)
        assert "CouncilPlugin" in result
        assert "Contracts" in result

    def test_extracts_typeddict_contract(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "types.py").write_text(
            "from typing import TypedDict\n\n"
            "class CouncilOutput(TypedDict):\n"
            "    debate_id: str\n"
            "    status: str\n"
        )
        result = self._rcs(tmp_path)
        assert "CouncilOutput" in result
        assert "Contracts" in result

    def test_source_inventory_present(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "council.py").write_text("class RGBCouncil:\n    async def run(self): pass\n")
        result = self._rcs(tmp_path)
        assert "Source inventory" in result
        assert "RGBCouncil" in result

    def test_test_files_excluded_from_inventory(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "mod.py").write_text("def real_fn(): pass\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_mod.py").write_text("def test_real_fn(): assert True\n")
        result = self._rcs(tmp_path)
        assert "real_fn" in result
        assert "test_real_fn" not in result

    def test_test_coverage_summary_present(self, tmp_path: Path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text("def test_x(): pass\n")
        (tests / "test_b.py").write_text("def test_y(): pass\n")
        result = self._rcs(tmp_path)
        assert "Test coverage" in result
        assert "2 test file" in result
