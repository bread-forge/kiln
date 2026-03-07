# kiln — Low-Level Design

## Module Reference

### `config.py` — Runtime configuration

```python
@dataclass
class Config:
    repo: str                          # owner/repo
    concurrency: int = 3               # parallel agent slots
    model: str = "claude-sonnet-4-6"   # default model
    agent_timeout_minutes: int = 60    # watchdog threshold
    max_retries: int = 3
    max_research_rounds: int = 2       # r1, r2 before hard-fail
    beads_dir: Path                    # ~/.kiln/beads/
    github_token: str | None           # forwarded to agents as GH_TOKEN
    plan_backend: str = "anthropic"    # "anthropic" | "gemini" | "openai"
    plan_model: str | None             # override for plan LLM
    research_backend: str = "anthropic"
    research_model: str | None
```

All fields also settable via `KILN_*` env vars. `Config.from_env(repo)` is the primary factory.

**`Registry`** — persists `owner/repo → RepoEntry` to `~/.kiln/kiln.toml`. Each entry carries
`local_path`, `spec_dir`, and `default_branch`. Used by the CLI to resolve repo paths without
requiring the operator to repeat `--repo-path` on every run.

---

### `graph/executor.py` — DAG executor

**`ExecutionGraph`**

Holds a `dict[str, GraphNode]`. Three key operations:

- `get_ready()` — returns `pending` nodes whose every dependency is in `done`/`already-done`,
  or `abandoned` AND in `_GATE_TYPES` (`wait`, `consensus`, `research`). Gate abandonment
  means "condition not met, proceed anyway" — not "work failed".
- `add_nodes(nodes)` — dynamic expansion; called after `plan` emits new nodes.
- `has_pending()` — drain condition for the executor loop.

**`GraphExecutor`**

Rolling dispatcher loop:
```
while graph.has_pending():
    ready = graph.get_ready()
    while len(running) < concurrency and ready:
        node = ready.pop(0)
        task = asyncio.create_task(handler.execute(node, config))
        running[task] = node
    done_task, _ = await asyncio.wait(running, return_when=FIRST_COMPLETED)
    result = done_task.result()
    if result.new_nodes:
        graph.add_nodes(result.new_nodes)   # plan expansion
    update bead state
```

Watchdog: a separate task cancels agent tasks that exceed `agent_timeout_minutes`.

**File overlap detection** — `_add_overlap_edges()` adds sequential deps between build nodes
that touch the same files. This prevents two agents from opening PRs that modify the same
file in parallel, causing merge conflicts.

---

### `graph/handlers/plan.py` — Plan handler

**`_read_codebase_summary(repo_local_path)`**

Static extraction (no LLM). Priority order:
1. `standards/*.md` — project invariants (1200 chars each, up to 8 files)
2. `CLAUDE.md` — module table, key design decisions (2500 chars)
3. `pyproject.toml` — deps and entry points (800 chars)
4. Contracts — `Protocol`/`TypedDict`/`ABC` class bodies via AST (up to 12, 800 chars each)
5. Reference implementations — files matching `def run/execute/handle` + contract base
6. Source inventory — `path: classes=[...] fns=[...]` per file
7. Test coverage summary — counts per `tests/` directory

Total cap: 16,000 chars fed into the plan LLM.

**`_call_plan_llm(spec_text, codebase_ctx, research_findings, model, ...)`**

Routing priority:
1. Non-anthropic `plan_backend` → `BackendRegistry.get_backend(plan_backend)`
2. anthropic + `breadmin_llm` available → `ProviderRegistry.complete()`
3. Fallback → `anthropic.AsyncAnthropic().messages.create()`

Returns a `PlanArtifact`. Strips markdown fences before JSON parsing.

**Research round logic in `execute()`**

```
research_round = node.context.get("research_round", 0)

if confidence < 0.85 and has_unknowns and round < max_rounds and unknowns_changed:
    emit research_nodes (per group) + plan_refine_node
elif confidence < 0.75 and round >= max_rounds:
    hard fail (abandon=True)
else:
    emit build + merge + readme nodes
```

**`_emit_research_nodes(unknowns, research_groups, ...)`**

One node per group (not per unknown). `research_groups` from `PlanArtifact` maps
`group_name → [unknowns]`. Each node's context carries:
- `research_group`: group slug
- `sibling_groups`: other group names (to avoid duplication)
- `prior_findings`: findings from the same group in the previous round
- `repo_local_path`: pre-cloned path to avoid redundant cloning

**`_gather_research_findings(ids, store, groups)`**

If `groups` is present (new-format beads): emits `## Research Group: <name>` sections.
If absent (old-format beads): flat `=== Research: <id> ===` fallback.

---

### `graph/handlers/research.py` — Research handler

Spawns a restricted `claude --print` subprocess per group. Allowed tools:
`WebSearch`, `WebFetch`, `Bash`, `Glob`, `Grep`, `Read`.

On completion: calls `store.write_research_findings(node_id, text)` so plan-refine can
read findings without re-running the subprocess.

Confidence extraction: `re.search(r"Overall confidence[^:]*:\s*([\d.]+)", text)`.

---

### `graph/handlers/build.py` — Build handler

1. Reads `issue_number` from node context (filed by plan handler via `gh issue create`)
2. Reads `files` (allowed scope) and `new_dependencies`
3. Installs new deps: `uv add <pkg>` on the target repo before spawning the agent
4. Spawns `claude --print` subprocess with build prompt
5. Agent creates a branch, implements the module, pushes, opens a PR
6. Writes a `WorkBead` and `PRBead`

Build prompt enforces: only touch files in `files`, reference the issue number in the commit,
do not merge (stop after `gh pr create`).

---

### `graph/handlers/merge.py` — Merge handler

Polling loop (up to `max_retries=20`, 60s sleep between attempts):
1. `gh pr checks <N> --watch` — wait for CI
2. On CI pass: `gh pr merge <N> --squash --delete-branch`
3. On CI fail: dispatch a Haiku repair sub-agent to fix lint/test failures
4. On conflict: dispatch a Haiku rebase sub-agent
5. On review request: dispatch a Haiku review-response sub-agent

Writes `PRBead.state` transitions: `open → ci_failing / conflict / merge_ready → merged`.

---

### `graph/handlers/readme.py` — Readme handler

Runs after all merge nodes complete. Spawns a Sonnet subprocess with instructions to:
- Read all changed files in the milestone
- Update `README.md` at the repo root and any package-level READMEs
- Commit and push directly to the default branch

No PR for readme — it's always a fast-forward commit on top of the merged milestone.

---

### `agents/prompts.py` — Prompt templates

**`PLAN_PROMPT`** — Sonnet plan prompt. Includes:
- Spec text (8000 chars)
- Codebase context (16000 chars)
- Research findings (24000 chars)
- Prior plan for refine runs (12000 chars)

Expected JSON output schema (enforced via prompt, not structured outputs):
```json
{
  "milestone": "...",
  "modules": ["mod-a", "mod-b"],
  "files_per_module": {"mod-a": ["src/..."]},
  "approach": "...",
  "module_approaches": {"mod-a": "..."},
  "confidence": 0.92,
  "unknowns": [],
  "research_groups": {"group": ["unknown"]},
  "empirical_unknowns": [],
  "risk_flags": [],
  "module_dependencies": {"mod-b": ["mod-a"]},
  "new_dependencies": []
}
```

**`RESEARCH_PROMPT`** — Haiku research prompt. Instructs the agent to:
- Answer every unknown in the group with evidence
- Note sibling groups it should NOT cover
- Include `**Overall confidence: 0.xx**` at the top of the response

---

### `agents/assessor.py` — Model selection

`assess_from_plan_artifact(artifact, module)` returns a `ModelAllocation` with `.model`.

Signals that push toward Sonnet over Haiku:
- module has > 3 files
- module is in `risk_flags` (security, novel-domain, multi-module-coordination)
- approach description mentions architectural patterns

---

### `preflight.py` — Routing decision

```python
@dataclass
class PreflightResult:
    route: Literal["cc", "kiln"]   # "cc" if total > 4
    total: int                      # 0-8
    volume: SignalScore             # 0-2
    novelty: SignalScore            # 0-2
    ambiguity: SignalScore          # 0-2
    cross_cutting: SignalScore      # 0-2
    summary: str
```

Single Haiku SDK call via `_call_haiku()`. JSON response stripped of markdown fences.
`confidence` property: `1.0 - abs(total - 4) / 4` — highest confidence at boundary.

---

### Bead types (`beads/types.py`)

| Type | Key fields |
|------|-----------|
| `WorkBead` | `issue_number`, `state` (open→claimed→pr_open→merged), `node_id` |
| `PRBead` | `pr_number`, `state`, `ci_attempts`, `review_attempts` |
| `MergeQueue` | ordered list of `MergeQueueItem` |
| `CampaignBead` | `milestones: list[MilestonePlan]`, wave-based ordering |
| `GraphNode` | `id`, `type`, `state`, `depends_on`, `context`, `output` |
| `PlanArtifact` | full plan output, persisted in `GraphNode.output` |
| `ExecutionReport` | post-run summary: outcome, costs, node counts |

---

### Key invariants

- Node IDs are `{milestone_slug}-{type}-{module_slug}` or `{milestone_slug}-research-r{N}-{group_slug}`
- `depends_on` uses node IDs — never issue/PR numbers
- Build nodes always depend on infra-module merge nodes (`pyproject.toml`, `uv.lock`)
- Research nodes are gate types: downstream plan-refine runs even if a research node is abandoned
- `PlanArtifact.research_groups` must cover every unknown in `unknowns` (enforced by prompt rule)
