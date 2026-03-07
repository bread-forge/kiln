"""Preflight decision maker — route a spec to CC (interactive) or kiln (autonomous).

Uses a single Haiku call to score the task on four signals:
  volume       — how many independent units of work?
  novelty      — first implementation of this pattern in the codebase?
  ambiguity    — can it be implemented without architectural judgment mid-task?
  cross_cutting — does it require aligning to existing contracts/patterns?

Each signal scores 0–2. Total ≤ 4 → kiln. Total > 4 → CC.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


_PREFLIGHT_MODEL = "claude-haiku-4-5-20251001"

_PROMPT = """\
You are a routing agent. Given a task spec and codebase context, score the task
on four signals to decide whether it should be handled by an autonomous batch
system (kiln) or by an interactive human-in-the-loop session (Claude Code / CC).

Spec:
{spec_text}

Codebase context:
{codebase_context}
{research_block}
---

Score each signal from 0 to 2. Output ONLY valid JSON — no markdown fences, no prose.

{{
  "volume": <0-2>,
  "volume_reason": "<one sentence>",
  "novelty": <0-2>,
  "novelty_reason": "<one sentence>",
  "ambiguity": <0-2>,
  "ambiguity_reason": "<one sentence>",
  "cross_cutting": <0-2>,
  "cross_cutting_reason": "<one sentence>",
  "summary": "<one sentence: decisive factor>"
}}

Scoring rubric:

volume (how many independent, parallelisable units of work?):
  0 = 1-2 units — no parallelism advantage
  1 = 3-4 units — modest gain
  2 = 5+ units — clear batch advantage

novelty (is this the first implementation of this pattern in the codebase?):
  0 = direct copy of an existing pattern — reference impl is in the codebase
  1 = partially novel — some reference exists but gaps remain
  2 = fully novel — no prior pattern to follow

ambiguity (how much architectural judgment is required mid-task?):
  0 = mechanical — interfaces are fully specified, no design decisions
  1 = some judgment — minor gaps but nothing cross-cutting
  2 = high judgment — architectural decisions that cascade across modules

cross_cutting (does it require aligning to existing contracts/patterns across modules?):
  0 = self-contained — no existing contracts touched
  1 = touches one existing interface — contract is clear
  2 = aligns across multiple existing patterns — Prism-style standards, protocols, DB schema
"""


@dataclass
class SignalScore:
    score: int  # 0–2
    reason: str


@dataclass
class PreflightResult:
    route: Literal["cc", "kiln"]
    total: int          # 0–8
    volume: SignalScore
    novelty: SignalScore
    ambiguity: SignalScore
    cross_cutting: SignalScore
    summary: str

    @property
    def confidence(self) -> float:
        """How far from the decision boundary (4) normalised to 0–1."""
        distance = abs(self.total - 4)
        return min(1.0, distance / 4.0 + 0.5)


async def run_preflight(
    spec_text: str,
    repo_local_path: str | None = None,
    research_findings: str = "",
) -> PreflightResult:
    """Score the spec and return a routing recommendation."""
    from kiln.graph.handlers.plan import _read_codebase_summary

    codebase_ctx = _read_codebase_summary(repo_local_path)

    if research_findings:
        research_block = (
            "\nResearch findings (use these to inform novelty and ambiguity scores):\n"
            + research_findings[:8000]
            + "\n"
        )
    else:
        research_block = ""

    prompt = _PROMPT.format(
        spec_text=spec_text[:6000],
        codebase_context=codebase_ctx[:8000],
        research_block=research_block,
    )

    text = await _call_haiku(prompt)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.startswith("```")).strip()

    # Extract the first JSON object, ignoring trailing prose
    brace = text.find("{")
    if brace >= 0:
        depth = 0
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    text = text[brace : i + 1]
                    break

    data = json.loads(text)

    volume = SignalScore(score=int(data["volume"]), reason=data["volume_reason"])
    novelty = SignalScore(score=int(data["novelty"]), reason=data["novelty_reason"])
    ambiguity = SignalScore(score=int(data["ambiguity"]), reason=data["ambiguity_reason"])
    cross_cutting = SignalScore(score=int(data["cross_cutting"]), reason=data["cross_cutting_reason"])

    total = volume.score + novelty.score + ambiguity.score + cross_cutting.score
    # High scores mean CC signals are strong; low means kiln is appropriate.
    route: Literal["cc", "kiln"] = "cc" if total > 4 else "kiln"

    return PreflightResult(
        route=route,
        total=total,
        volume=volume,
        novelty=novelty,
        ambiguity=ambiguity,
        cross_cutting=cross_cutting,
        summary=data.get("summary", ""),
    )


async def run_preflight_with_proof(
    spec_text: str,
    repo_local_path: str | None = None,
    repo: str = "",
    store: object | None = None,
) -> tuple[PreflightResult, PreflightResult | None]:
    """Two-pass preflight: fast score → proof research → refined score.

    Returns (fast_result, refined_result).  refined_result is None if proof
    found no unknowns or the fast score was far from the boundary (≤ 2 or ≥ 6).

    When store is provided (a BeadStore), proof findings are persisted so they
    can be picked up by plan-refine without re-running the subprocess.
    """
    import uuid

    fast = await run_preflight(spec_text, repo_local_path)

    # Only run proof if score is on or near the boundary (3–5) or ambiguity ≥ 1.
    near_boundary = 3 <= fast.total <= 5
    has_ambiguity = fast.ambiguity.score >= 1
    if not (near_boundary or has_ambiguity):
        return fast, None

    from proof.core import research
    from proof.extractor import extract_unknowns

    groups = await extract_unknowns(spec_text)
    if not groups:
        return fast, None

    result = await research(
        groups,
        repo=repo,
        repo_local_path=repo_local_path,
    )

    # Persist findings to bead store so plan-refine can read them.
    if store is not None:
        run_id = uuid.uuid4().hex[:8]
        for finding in result.findings:
            node_id = f"preflight-{run_id}-{finding.group}"
            store.store_research_findings(node_id, finding.findings)  # type: ignore[attr-defined]

    refined = await run_preflight(
        spec_text,
        repo_local_path,
        research_findings=result.markdown,
    )
    return fast, refined


async def _call_haiku(prompt: str) -> str:
    try:
        from breadmin_llm.registry import ProviderRegistry
        from breadmin_llm.types import LLMCall, LLMMessage, MessageRole

        registry = ProviderRegistry.default()
        call = LLMCall(
            model=_PREFLIGHT_MODEL,
            messages=[LLMMessage(role=MessageRole.USER, content=prompt)],
            max_tokens=512,
            caller="kiln.preflight",
        )
        response = await registry.complete(call)
        return response.content
    except ImportError:
        import anthropic

        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=_PREFLIGHT_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text  # type: ignore[union-attr]
