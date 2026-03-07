"""PlanHandler — SDK call that reads spec + codebase + research, emits new nodes.

Uses Anthropic SDK directly (not subprocess) — plan output is structured JSON,
faster and cheaper than claude --print for structured extraction.

Confidence < PLAN_CONFIDENCE_FLOOR   → hard fail; human must intervene.
Confidence < PLAN_RESEARCH_THRESHOLD → emit research nodes first (first pass only).
Confidence >= PLAN_RESEARCH_THRESHOLD → emit build + merge nodes directly.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from beads.types import GraphNode, PlanArtifact

from kiln.graph.node import NodeResult

if TYPE_CHECKING:
    from beads.store import BeadStore

    from kiln.config import Config
    from kiln.logger import Logger

PLAN_CONFIDENCE_FLOOR = 0.75       # hard fail — too uncertain to build anything
PLAN_RESEARCH_THRESHOLD = 0.85    # trigger research pass on first plan attempt


def _read_codebase_summary(repo_local_path: str | None) -> str:
    """Assess the current codebase: contracts, patterns, and what is already built.

    Priority order (highest signal first):
    1. standards/ files — project-level invariants every module must satisfy
    2. CLAUDE.md — project instructions and module table
    3. pyproject.toml — dependencies and entry points
    4. Contracts — Protocol, TypedDict, and ABC definitions (full bodies)
    5. Reference implementations — condensed patterns from existing similar modules
    6. Source inventory — file-level symbol map of what is already implemented
    7. Test coverage summary

    All extraction is static (regex/AST-free string ops) — no LLM calls.
    """
    if not repo_local_path:
        return ""
    import ast
    import re

    root = Path(repo_local_path)
    parts: list[str] = []

    # 1. standards/ — project-wide invariants the planner must enforce
    standards_dir = root / "standards"
    if standards_dir.is_dir():
        std_parts = []
        for std_file in sorted(standards_dir.glob("*.md"))[:8]:
            with contextlib.suppress(OSError):
                text = std_file.read_text(encoding="utf-8")[:1200]
                std_parts.append(f"### {std_file.name}\n{text}")
        if std_parts:
            parts.append("=== Project standards (all modules must satisfy) ===\n" + "\n\n".join(std_parts))

    # 2. CLAUDE.md
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        with contextlib.suppress(OSError):
            parts.append("=== CLAUDE.md ===\n" + claude_md.read_text(encoding="utf-8")[:2500])

    # 3. pyproject.toml
    for pyproject in sorted(root.rglob("pyproject.toml"))[:4]:
        with contextlib.suppress(OSError):
            parts.append(f"=== {pyproject.relative_to(root)} ===\n" + pyproject.read_text(encoding="utf-8")[:800])

    # Collect all non-test Python source files once — used by sections 4, 5, 6
    src_dirs = [d for name in ("src", "packages") if (d := root / name).is_dir()] or [root]
    py_files = sorted(
        f
        for sd in src_dirs
        for f in sd.rglob("*.py")
        if "test" not in f.parts and "__pycache__" not in f.parts
    )[:200]

    file_texts: dict[Path, str] = {}
    for py_file in py_files:
        with contextlib.suppress(OSError):
            file_texts[py_file] = py_file.read_text(encoding="utf-8")

    # 4. Contracts — Protocol / TypedDict / ABC class bodies
    # These define the interfaces every implementation must satisfy.
    def _extract_class_body(source: str, class_name: str) -> str:
        """Extract the body of a class definition using AST line numbers."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return ""
        lines = source.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                end = node.end_lineno or len(lines)
                body = "\n".join(lines[node.lineno - 1 : min(end, node.lineno + 40)])
                return body
        return ""

    contract_parts: list[str] = []
    _CONTRACT_BASES = re.compile(r"\b(Protocol|TypedDict|ABC|ABCMeta)\b")
    for py_file, text in file_texts.items():
        if not _CONTRACT_BASES.search(text):
            continue
        rel = py_file.relative_to(root)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases_src = ast.unparse(node) if hasattr(ast, "unparse") else ""
            is_contract = any(
                _CONTRACT_BASES.search(ast.unparse(b)) for b in node.bases
            ) if hasattr(ast, "unparse") else _CONTRACT_BASES.search(
                text[node.col_offset : text.find("\n", node.col_offset + 1) + 200]
            )
            if not is_contract:
                continue
            body = _extract_class_body(text, node.name)
            if body:
                contract_parts.append(f"# {rel}\n{body[:800]}")

    if contract_parts:
        parts.append("=== Contracts (Protocol / TypedDict / ABC — must be satisfied) ===\n" + "\n\n".join(contract_parts[:12]))

    # 5. Reference implementations — condensed patterns from existing similar modules
    # Heuristic: files that implement a known contract pattern (non-abstract, non-test)
    # and whose class names suggest they are concrete implementations.
    # Extract: class signature + first 30 lines of each method body.
    def _condense_implementation(source: str, max_chars: int = 600) -> str:
        """Extract class-level docstring + method signatures from a source file."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source[:max_chars]
        lines = source.splitlines()
        out: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Class header + docstring only
            header_end = node.lineno
            out.append("\n".join(lines[node.lineno - 1 : node.lineno + 1]))
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
            ):
                out.append(f'    """{node.body[0].value.s[:200]}"""')
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    sig_line = lines[item.lineno - 1].rstrip()
                    out.append(f"    {sig_line.strip()}")
                    # Include return annotation hint from signature line only
        return "\n".join(out)[:max_chars]

    # Files that look like concrete implementations: contain "def run" or "def execute"
    # and reference a contract base. Cap at 6 reference files.
    _IMPL_SIGNAL = re.compile(r"^\s*(async )?def (run|execute|handle|process)\b", re.MULTILINE)
    ref_parts: list[str] = []
    for py_file, text in file_texts.items():
        if not _IMPL_SIGNAL.search(text):
            continue
        if not _CONTRACT_BASES.search(text):
            continue
        rel = py_file.relative_to(root)
        condensed = _condense_implementation(text)
        if condensed:
            ref_parts.append(f"# {rel}\n{condensed}")
        if len(ref_parts) >= 6:
            break

    if ref_parts:
        parts.append("=== Reference implementations (existing patterns to follow) ===\n" + "\n\n".join(ref_parts))

    # 6. Source inventory — lightweight map of what exists
    inventory_lines: list[str] = []
    for py_file, text in file_texts.items():
        rel = py_file.relative_to(root)
        classes = re.findall(r"^class (\w+)", text, re.MULTILINE)
        funcs = re.findall(r"^(?:async )?def (\w+)", text, re.MULTILINE)
        public_funcs = [f for f in funcs if not f.startswith("_")]
        if not text.strip() or (not classes and not public_funcs):
            inventory_lines.append(f"  {rel}  (stub/empty)")
        else:
            line = str(rel)
            if classes:
                line += f"  classes=[{', '.join(classes[:6])}]"
            if public_funcs:
                line += f"  fns=[{', '.join(public_funcs[:8])}]"
            inventory_lines.append(f"  {line}")

    if inventory_lines:
        parts.append("=== Source inventory ===\n" + "\n".join(inventory_lines))

    # 7. Test coverage
    test_summary: list[str] = []
    for tests_dir in sorted(root.rglob("tests"))[:10]:
        if not tests_dir.is_dir():
            continue
        test_files = list(tests_dir.glob("test_*.py"))
        if test_files:
            test_summary.append(f"  {tests_dir.relative_to(root)}: {len(test_files)} test file(s)")
    if test_summary:
        parts.append("=== Test coverage ===\n" + "\n".join(test_summary))

    return "\n\n".join(parts)


def _gather_research_findings(
    research_node_ids: list[str],
    store: BeadStore | None,
    research_node_groups: dict[str, list[str]] | None = None,
) -> str:
    if not store or not research_node_ids:
        return ""
    if research_node_groups:
        parts = []
        for group, node_ids in research_node_groups.items():
            group_parts = []
            for nid in node_ids:
                findings = store.read_research_findings(nid)
                if findings:
                    group_parts.append(findings)
            if group_parts:
                parts.append(f"## Research Group: {group}\n\n" + "\n\n---\n\n".join(group_parts))
        return "\n\n".join(parts)
    # flat fallback for old beads without group info
    parts = []
    for nid in research_node_ids:
        findings = store.read_research_findings(nid)
        if findings:
            parts.append(f"=== Research: {nid} ===\n{findings}")
    return "\n\n".join(parts)


async def _call_plan_llm(
    spec_text: str,
    codebase_ctx: str,
    research_findings: str,
    model: str,
    plan_backend: str = "anthropic",
    plan_model_override: str | None = None,
    prior_plan: str = "",
) -> PlanArtifact:
    """Call an LLM backend to get a structured PlanArtifact.

    Routing priority:
    1. Non-anthropic backend (gemini / openai) → use backend registry directly.
    2. anthropic backend with breadmin_llm available → use ProviderRegistry.
    3. Fallback → Anthropic SDK directly.
    """
    from kiln.agents.prompts import PLAN_PROMPT

    prompt = PLAN_PROMPT.format(
        spec_text=spec_text[:8000],
        codebase_context=codebase_ctx[:16000],
        research_findings=research_findings[:24000],
        prior_plan=prior_plan[:12000],
    )

    effective_model = plan_model_override or model

    if plan_backend != "anthropic":
        from kiln.backends import get_backend

        backend = get_backend(plan_backend, model=plan_model_override)
        response = await backend.complete(prompt, max_tokens=16000)
        text = response.content
    else:
        try:
            from breadmin_llm.registry import ProviderRegistry
            from breadmin_llm.types import LLMCall, LLMMessage, MessageRole

            registry = ProviderRegistry.default()
            call = LLMCall(
                model=effective_model,
                messages=[LLMMessage(role=MessageRole.USER, content=prompt)],
                max_tokens=16000,
                caller="kiln.plan",
            )
            llm_response = await registry.complete(call)
            text = llm_response.content
        except ImportError:
            import anthropic

            client = anthropic.AsyncAnthropic()
            sdk_response = await client.messages.create(
                model=effective_model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = sdk_response.content[0].text  # type: ignore[union-attr]

    # Strip markdown fences
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.startswith("```")).strip()

    data = json.loads(text)
    return PlanArtifact.model_validate(data)


_RESEARCH_MODEL = "claude-haiku-4-5-20251001"
"""Research nodes do web search only — haiku is sufficient and cheap."""


def _emit_research_nodes(
    unknowns: list[str],
    research_groups: dict[str, list[str]],
    parent_node: GraphNode,
    milestone_slug: str,
    repo: str,
    round_num: int = 1,
    prior_findings_by_group: dict[str, str] | None = None,
    repo_local_path: str = "",
) -> list[GraphNode]:
    """Emit one research node per group so related unknowns are investigated together."""
    # If no groups provided, put all unknowns in a single general node.
    effective_groups = research_groups if research_groups else {"general": unknowns}
    all_group_names = list(effective_groups.keys())

    nodes = []
    for group, group_unknowns in effective_groups.items():
        if not group_unknowns:
            continue
        group_slug = _slug(group)
        node_id = f"{milestone_slug}-research-r{round_num}-{group_slug}"

        ctx: dict = {
            "milestone": milestone_slug,
            "repo": repo,
            "unknowns": group_unknowns,
            "research_group": group,
        }
        # Inform each agent which groups are covered by sibling agents
        sibling_groups = [g for g in all_group_names if g != group]
        if sibling_groups:
            ctx["sibling_groups"] = sibling_groups
        # Inject prior-round findings for this group so r2+ agents build on prior work
        prior_findings = (prior_findings_by_group or {}).get(group, "")
        if prior_findings:
            ctx["prior_findings"] = prior_findings
        # Share pre-cloned repo path to avoid redundant clones
        if repo_local_path:
            ctx["repo_local_path"] = repo_local_path

        nodes.append(
            GraphNode(
                id=node_id,
                type="research",
                assigned_model=_RESEARCH_MODEL,
                context=ctx,
            )
        )
    return nodes


def _emit_plan_refine_node(
    parent_node: GraphNode,
    artifact: PlanArtifact,
    research_nodes: list[GraphNode],
    spec_file: str,
    repo_local_path: str,
    repo: str,
    milestone_slug: str,
    research_round: int,
    prior_research_node_ids: list[str],
    prior_research_node_groups: dict[str, list[str]],
) -> GraphNode:
    """Emit a refined plan node that depends on the research nodes."""
    # Build group → [node_id] mapping for this round's nodes
    new_group_to_node_ids: dict[str, list[str]] = {}
    for n in research_nodes:
        group = n.context.get("research_group", "general")
        new_group_to_node_ids.setdefault(group, []).append(n.id)

    # Accumulate all research node IDs and groups across rounds
    all_research_node_ids = prior_research_node_ids + [n.id for n in research_nodes]
    all_research_node_groups = {**prior_research_node_groups, **new_group_to_node_ids}

    return GraphNode(
        id=f"{milestone_slug}-plan-refine-{research_round}",
        type="plan",
        depends_on=[n.id for n in research_nodes],
        context={
            "milestone": milestone_slug,
            "spec_file": spec_file,
            "repo": repo,
            "repo_local_path": repo_local_path,
            "research_node_ids": all_research_node_ids,
            "research_node_groups": all_research_node_groups,
            "research_round": research_round,
            "prior_artifact": artifact.model_dump(),
        },
    )


def _slug(s: str) -> str:
    """Sanitize a string for use in node IDs (no spaces, safe chars only)."""
    import re

    return re.sub(r"[^a-zA-Z0-9._-]", "-", s).strip("-")


def _file_module_issue(
    repo: str, module: str, milestone_slug: str, artifact: PlanArtifact
) -> int | None:
    """File a GitHub issue for a build module. Returns issue number or None on failure."""
    import subprocess

    files = artifact.files_per_module.get(module, [])

    # List other modules so the agent knows what is out of scope for this PR
    other_modules = [m for m in artifact.modules if m != module]
    other_scope_note = ""
    if other_modules:
        other_scope_note = (
            "\n\n**Other modules in this milestone (out of scope for this PR):** "
            + ", ".join(f"`{m}`" for m in other_modules)
            + "\n\nDo NOT implement work belonging to those modules even if the approach "
            "description mentions it. Each module is a separate PR."
        )

    module_approach = artifact.module_approaches.get(module) or artifact.approach
    body = (
        f"**Milestone:** {milestone_slug}\n"
        f"**Module:** `{module}`\n\n"
        f"**What to implement:** {module_approach}\n"
        f"{other_scope_note}\n\n"
        f"**Files to create/modify (your scope only):**\n" + "\n".join(f"- `{f}`" for f in files)
    )
    result = subprocess.run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            f"impl({milestone_slug}): {module} module",
            "--body",
            body,
            "--label",
            "stage/impl",
            "--milestone",
            milestone_slug,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    # gh issue create prints the URL on stdout; extract number from URL
    url = result.stdout.strip()
    try:
        return int(url.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
        return None


def _emit_build_nodes(
    artifact: PlanArtifact,
    repo: str,
    milestone_slug: str,
    plan_node_id: str,
    milestone_issue_number: int | None = None,
    override_model: str | None = None,
) -> list[GraphNode]:
    """Emit one build node per module, filing a GH issue for each.

    Model selection happens here — assessor runs once per module during planning
    so build handlers don't need to call the assessor at dispatch time.
    """
    from kiln.agents.assessor import assess_from_plan_artifact

    # The executor always emits a dedicated readme node after all merges — skip
    # any "readme" module the LLM may have included to avoid a duplicate issue.
    _RESERVED_NODE_TYPES = {"readme", "docs", "documentation"}

    # Infrastructure files that must exist on mainline before any other module's CI can run.
    _INFRA_FILES = {"pyproject.toml", "uv.lock", "setup.py", "setup.cfg", "Makefile", "requirements.txt"}

    # Modules whose scope includes infra files must be merged before all others.
    infra_modules = {
        mod
        for mod, files in artifact.files_per_module.items()
        if any(Path(f).name in _INFRA_FILES for f in files)
        and mod.lower().strip() not in _RESERVED_NODE_TYPES
    }

    nodes = []
    for module in artifact.modules:
        if module.lower().strip() in _RESERVED_NODE_TYPES:
            continue
        files = artifact.files_per_module.get(module, [])
        module_slug = _slug(module)
        issue_number = _file_module_issue(repo, module, milestone_slug, artifact)
        allocation = assess_from_plan_artifact(artifact, module, override_model=override_model)

        # Wire inter-module dependencies: this build node depends on the *merge*
        # nodes of any modules listed in artifact.module_dependencies[module].
        dep_modules = artifact.module_dependencies.get(module, [])
        depends_on = [
            f"{milestone_slug}-build-{_slug(dep)}-merge"
            for dep in dep_modules
            if dep.lower().strip() not in _RESERVED_NODE_TYPES
        ]

        # Any module that owns infrastructure files (pyproject.toml, uv.lock, etc.)
        # must be merged before this module can pass CI — inject automatically.
        if module not in infra_modules:
            for infra_mod in sorted(infra_modules):
                infra_merge_id = f"{milestone_slug}-build-{_slug(infra_mod)}-merge"
                if infra_merge_id not in depends_on:
                    depends_on.append(infra_merge_id)

        context: dict = {
            "milestone": milestone_slug,
            "module": module,
            "files": files,
            "repo": repo,
            "plan_node_id": plan_node_id,
            "milestone_issue_number": milestone_issue_number,
            "new_dependencies": artifact.new_dependencies,
        }
        if issue_number:
            context["issue_number"] = issue_number
            context["issue_title"] = f"impl({milestone_slug}): {module} module"
        nodes.append(
            GraphNode(
                id=f"{milestone_slug}-build-{module_slug}",
                type="build",
                assigned_model=allocation.model,
                depends_on=depends_on,
                context=context,
            )
        )
    return nodes


def _emit_readme_node(
    merge_nodes: list[GraphNode],
    artifact: PlanArtifact,
    milestone_slug: str,
    repo: str,
    plan_node_id: str,
    milestone_issue_number: int | None = None,
) -> GraphNode:
    """Emit a readme node that runs after all merges complete."""
    context: dict = {
        "milestone": milestone_slug,
        "repo": repo,
        "plan_node_id": plan_node_id,
    }
    if milestone_issue_number:
        context["milestone_issue_number"] = milestone_issue_number
    return GraphNode(
        id=f"{milestone_slug}-readme",
        type="readme",
        depends_on=[n.id for n in merge_nodes],
        context=context,
    )


def _emit_merge_nodes(build_nodes: list[GraphNode]) -> list[GraphNode]:
    """Emit one merge node per build node, depending on it."""
    nodes = []
    for build_node in build_nodes:
        nodes.append(
            GraphNode(
                id=f"{build_node.id}-merge",
                type="merge",
                depends_on=[build_node.id],
                max_retries=20,  # CI may take up to ~20 minutes; handler sleeps 60s between attempts
                context={
                    "build_node_id": build_node.id,
                },
            )
        )
    return nodes


class PlanHandler:
    """Calls LLM to produce a PlanArtifact, then expands the graph."""

    def __init__(
        self,
        store: BeadStore | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._store = store
        self._logger = logger

    async def execute(self, node: GraphNode, config: Config) -> NodeResult:
        spec_file = node.context.get("spec_file", "")
        repo_local_path = node.context.get("repo_local_path", "")
        research_node_ids: list[str] = node.context.get("research_node_ids", [])
        milestone = node.context.get("milestone", "")
        repo = config.repo

        # Read spec
        try:
            spec_text = Path(spec_file).read_text(encoding="utf-8") if spec_file else ""
        except OSError as e:
            return NodeResult(success=False, error=f"could not read spec: {e}")

        codebase_ctx = _read_codebase_summary(repo_local_path)
        research_node_groups: dict[str, list[str]] | None = node.context.get("research_node_groups")
        research_findings = _gather_research_findings(research_node_ids, self._store, research_node_groups)

        # Serialize prior plan artifact for plan-refine runs so the LLM refines
        # an existing plan rather than starting from scratch.
        prior_artifact_dict = node.context.get("prior_artifact")
        prior_plan = (
            "Previous plan (refine this — do not restart from scratch):\n"
            + __import__("json").dumps(prior_artifact_dict, indent=2)
            if prior_artifact_dict
            else ""
        )

        try:
            artifact = await _call_plan_llm(
                spec_text,
                codebase_ctx,
                research_findings,
                config.model,
                plan_backend=config.plan_backend,
                plan_model_override=config.plan_model,
                prior_plan=prior_plan,
            )
        except Exception as e:
            return NodeResult(success=False, error=f"plan LLM call failed: {e}")

        if self._logger:
            self._logger.info(
                f"plan {node.id}: confidence={artifact.confidence:.2f}, modules={artifact.modules}",
                node_id=node.id,
            )

        new_nodes: list[GraphNode] = []
        # Always use the slug from context for node IDs — the LLM may return the full title
        milestone_slug = milestone
        milestone_issue_number: int | None = node.context.get("milestone_issue_number")

        research_round: int = node.context.get("research_round", 0)
        prior_research_node_ids: list[str] = node.context.get("research_node_ids", [])
        prior_research_node_groups: dict[str, list[str]] = node.context.get("research_node_groups") or {}
        max_research_rounds: int = config.max_research_rounds

        # Empirical unknowns (require live testing) do not generate research nodes and do
        # not count against confidence. Only web-researchable unknowns trigger research.
        has_web_unknowns = bool(artifact.unknowns)

        # Hard fail only when web-researchable unknowns remain unresolved after all rounds.
        # If only empirical unknowns remain, confidence is already accounted for by the
        # planner — proceed to build with empirical unknowns surfaced as risk flags.
        if research_round >= max_research_rounds and artifact.confidence < PLAN_CONFIDENCE_FLOOR and has_web_unknowns:
            return NodeResult(
                success=False,
                abandon=True,  # skip retries — same LLM call will keep failing
                error=(
                    f"plan confidence {artifact.confidence:.2f} still below floor "
                    f"{PLAN_CONFIDENCE_FLOOR} after {research_round} research round(s); "
                    "human intervention required: "
                    + "; ".join(artifact.unknowns[:3])
                ),
            )

        # Skip a new research round if unknowns are identical to the prior round —
        # the same questions would produce the same answers.
        prior_unknowns: list[str] = []
        if prior_artifact_dict:
            prior_unknowns = prior_artifact_dict.get("unknowns", [])
        unknowns_unchanged = set(artifact.unknowns) == set(prior_unknowns) and bool(prior_unknowns)

        if (
            artifact.confidence < PLAN_RESEARCH_THRESHOLD
            and has_web_unknowns
            and research_round < max_research_rounds
            and not unknowns_unchanged
        ):
            # Gather r1 findings per group so r2+ nodes can build on prior work
            prior_findings_by_group: dict[str, str] = {}
            if research_round >= 1 and self._store:
                for group, node_ids in prior_research_node_groups.items():
                    group_parts = [
                        f
                        for nid in node_ids
                        if (f := self._store.read_research_findings(nid))
                    ]
                    if group_parts:
                        prior_findings_by_group[group] = "\n\n---\n\n".join(group_parts)

            research_nodes = _emit_research_nodes(
                artifact.unknowns, artifact.research_groups, node, milestone_slug, repo,
                round_num=research_round + 1,
                prior_findings_by_group=prior_findings_by_group,
                repo_local_path=repo_local_path,
            )
            refine_node = _emit_plan_refine_node(
                node,
                artifact,
                research_nodes,
                spec_file,
                repo_local_path,
                repo,
                milestone_slug,
                research_round=research_round + 1,
                prior_research_node_ids=prior_research_node_ids,
                prior_research_node_groups=prior_research_node_groups,
            )
            new_nodes = research_nodes + [refine_node]
        else:
            build_nodes = _emit_build_nodes(
                artifact,
                repo,
                milestone_slug,
                node.id,
                milestone_issue_number,
                override_model=config.model or None,
            )
            merge_nodes = _emit_merge_nodes(build_nodes)
            readme_node = _emit_readme_node(
                merge_nodes, artifact, milestone_slug, repo, node.id, milestone_issue_number
            )
            new_nodes = build_nodes + merge_nodes + [readme_node]

        return NodeResult(
            success=True,
            output={
                "artifact": artifact.model_dump(),
                "new_nodes": [n.model_dump(mode="json") for n in new_nodes],
            },
        )

    def recover(self, node: GraphNode, config: Config) -> NodeResult | None:
        """Plan nodes have no recoverable state — always re-dispatch."""
        return None
