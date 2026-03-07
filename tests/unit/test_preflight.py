"""Unit tests for kiln.preflight — routing decision logic."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kiln.preflight import PreflightResult, SignalScore, run_preflight


def _make_llm_response(
    volume: int = 1,
    novelty: int = 1,
    ambiguity: int = 1,
    cross_cutting: int = 1,
    summary: str = "Test summary.",
) -> str:
    return json.dumps({
        "volume": volume,
        "volume_reason": "volume reason",
        "novelty": novelty,
        "novelty_reason": "novelty reason",
        "ambiguity": ambiguity,
        "ambiguity_reason": "ambiguity reason",
        "cross_cutting": cross_cutting,
        "cross_cutting_reason": "cross_cutting reason",
        "summary": summary,
    })


@pytest.fixture
def patched_haiku():
    """Patch _call_haiku to return a controllable JSON string."""
    with patch("kiln.preflight._call_haiku", new_callable=AsyncMock) as mock:
        yield mock


class TestPreflightResult:
    def test_confidence_at_boundary(self) -> None:
        result = PreflightResult(
            route="kiln",
            total=4,
            volume=SignalScore(1, ""),
            novelty=SignalScore(1, ""),
            ambiguity=SignalScore(1, ""),
            cross_cutting=SignalScore(1, ""),
            summary="",
        )
        assert result.confidence == pytest.approx(0.5)

    def test_confidence_max_kiln(self) -> None:
        result = PreflightResult(
            route="kiln",
            total=0,
            volume=SignalScore(0, ""),
            novelty=SignalScore(0, ""),
            ambiguity=SignalScore(0, ""),
            cross_cutting=SignalScore(0, ""),
            summary="",
        )
        assert result.confidence == pytest.approx(1.0)

    def test_confidence_max_cc(self) -> None:
        result = PreflightResult(
            route="cc",
            total=8,
            volume=SignalScore(2, ""),
            novelty=SignalScore(2, ""),
            ambiguity=SignalScore(2, ""),
            cross_cutting=SignalScore(2, ""),
            summary="",
        )
        assert result.confidence == pytest.approx(1.0)


class TestRunPreflight:
    @pytest.mark.asyncio
    async def test_low_scores_route_to_kiln(self, patched_haiku: AsyncMock) -> None:
        patched_haiku.return_value = _make_llm_response(volume=0, novelty=0, ambiguity=0, cross_cutting=0)
        result = await run_preflight("build 10 test modules")
        assert result.route == "kiln"
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_high_scores_route_to_cc(self, patched_haiku: AsyncMock) -> None:
        patched_haiku.return_value = _make_llm_response(volume=2, novelty=2, ambiguity=2, cross_cutting=2)
        result = await run_preflight("align RGB to Prism standards")
        assert result.route == "cc"
        assert result.total == 8

    @pytest.mark.asyncio
    async def test_boundary_total_four_routes_to_kiln(self, patched_haiku: AsyncMock) -> None:
        patched_haiku.return_value = _make_llm_response(volume=1, novelty=1, ambiguity=1, cross_cutting=1)
        result = await run_preflight("some spec")
        assert result.route == "kiln"
        assert result.total == 4

    @pytest.mark.asyncio
    async def test_total_five_routes_to_cc(self, patched_haiku: AsyncMock) -> None:
        patched_haiku.return_value = _make_llm_response(volume=2, novelty=1, ambiguity=1, cross_cutting=1)
        result = await run_preflight("some spec")
        assert result.route == "cc"
        assert result.total == 5

    @pytest.mark.asyncio
    async def test_signal_scores_populated(self, patched_haiku: AsyncMock) -> None:
        patched_haiku.return_value = _make_llm_response(
            volume=2, novelty=1, ambiguity=0, cross_cutting=1, summary="Decisive factor."
        )
        result = await run_preflight("spec text")
        assert result.volume.score == 2
        assert result.volume.reason == "volume reason"
        assert result.novelty.score == 1
        assert result.ambiguity.score == 0
        assert result.cross_cutting.score == 1
        assert result.summary == "Decisive factor."

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self, patched_haiku: AsyncMock) -> None:
        raw = _make_llm_response(volume=0, novelty=0, ambiguity=0, cross_cutting=0)
        patched_haiku.return_value = f"```json\n{raw}\n```"
        result = await run_preflight("spec")
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_passes_spec_text_to_llm(self, patched_haiku: AsyncMock) -> None:
        patched_haiku.return_value = _make_llm_response()
        await run_preflight("my unique spec content xyz")
        call_args = patched_haiku.call_args
        assert "my unique spec content xyz" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_reads_codebase_context(self, patched_haiku: AsyncMock, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# project context")
        patched_haiku.return_value = _make_llm_response()
        await run_preflight("spec", repo_local_path=str(tmp_path))
        call_args = patched_haiku.call_args
        assert "project context" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_codebase_path_still_works(self, patched_haiku: AsyncMock) -> None:
        patched_haiku.return_value = _make_llm_response()
        result = await run_preflight("spec text", repo_local_path=None)
        assert result is not None
