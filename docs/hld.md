# kiln — High-Level Design

## Overview

kiln is a spec-driven, bead-tracked autonomous build system. Given a markdown spec file and a
target GitHub repo, kiln plans the work, researches unknowns, dispatches parallel build agents,
monitors CI, and merges PRs — with no human involvement once the run starts.

**Diagram:** [kiln-overview.drawio](kiln-overview.drawio) | [kiln-graph.drawio](kiln-graph.drawio)

---

## System Context

```
operator
  └─ kiln run <spec.md> --repo owner/repo
       └─ /preflight  →  score? > 4 → Claude Code (interactive)
                             ≤ 4 → kiln (automated)
```

kiln is the automated tier. It suits well-understood, bounded tasks where the plan can be
constructed from the spec + codebase context alone. Claude Code is used for tasks with
architectural unknowns, P0/P1 risk flags, or multi-domain cross-cutting concerns.

---

## Architecture

### Execution Model

kiln builds a DAG of typed graph nodes from the spec, then runs a rolling dispatcher that
fills concurrency slots as nodes complete. The DAG is dynamic: the `plan` node emits
`research`, `build`, `merge`, and `readme` nodes at runtime after reading the spec.

```
spec.md
  └─ plan node (Sonnet, SDK call)
       ├─ [if confidence < 0.85] research nodes A, B, … (Haiku, parallel)
       │    └─ plan-refine node (Sonnet, SDK call)
       │         └─ build nodes A, B, C, … (Sonnet, CLI subprocesses, parallel)
       │              └─ merge nodes A, B, C (Haiku, CI polling, sequential per module)
       │                   └─ readme node (Sonnet, CLI subprocess)
       └─ [if confidence ≥ 0.85] build nodes directly
```

### Bead Store

Every node's state is written to `~/.kiln/beads/<owner>/<repo>/` before and after execution.
Beads are the single source of truth — they survive process restarts, and the executor skips
nodes already in `done`/`already-done` state on re-run. GitHub issues and PRs are derived
from beads, not the other way around.

### Rolling Dispatcher

The executor loop:
1. Collects all `pending` nodes whose dependencies are satisfied (`done` or `abandoned` gate nodes).
2. Fills concurrency slots (default: 3) by dispatching ready nodes.
3. Awaits the first completion, updates the bead, expands the graph if needed.
4. Repeats until no pending or running nodes remain.

A watchdog sends SIGTERM → SIGKILL to agents that exceed `agent_timeout_minutes` (default: 60).

---

## Node Types

| Type | Model | Mechanism | Purpose |
|------|-------|-----------|---------|
| `plan` | Sonnet | SDK call | Parse spec → emit DAG |
| `research` | Haiku | CLI subprocess | Web research for unknowns |
| `build` | Sonnet | CLI subprocess | Implement a module, open PR |
| `merge` | Haiku (sub-agents) | gh CLI polling | Monitor CI, resolve conflicts, merge |
| `readme` | Sonnet | CLI subprocess | Update README after all merges |
| `wait` | — | polling | Block until external condition met |
| `consensus` | Sonnet | n voters | Multi-agent deliberation |
| `design_doc` | Sonnet | CLI subprocess | Generate design documents |

---

## Key Design Decisions

**Spec-driven, not pipeline-driven.** There are no fixed HLD/LLD/research stages. The plan
node decides which stages are needed based on spec complexity and confidence. Simple specs
skip research entirely.

**Beads lead, GitHub follows.** Operators query bead state (`kiln graph nodes`) to assess
progress — never `gh issue list`. GitHub issues are filing artifacts; beads are operational state.

**One agent per module.** Each build node owns exactly one set of files (its module scope).
Agents cannot touch files outside their scope. The plan node declares `files_per_module` and
the build agent prompt enforces the boundary.

**Research groups.** Unknowns are grouped by semantic domain (e.g., `warden`, `screen_capture`,
`ui_mechanics`). Each group runs as one research agent. The plan-refine node receives findings
organized by group header, not a flat concatenated blob.

**Preflight routing.** Before any kiln run, the operator (or `/preflight` skill) scores the
spec on four signals: volume, novelty, ambiguity, and cross-cutting concerns. Score ≤ 4 → kiln;
score > 4 → Claude Code. This prevents kiln from being dispatched on tasks that need interactive
deliberation.

---

## Data Flow

```
spec.md  →  PlanHandler  →  PlanArtifact
                                ├─ modules, files_per_module
                                ├─ research_groups, unknowns
                                ├─ module_dependencies
                                └─ new_dependencies

PlanArtifact  →  _emit_research_nodes  →  ResearchHandler (per group)
                                               └─ BeadStore.write_research_findings()

ResearchHandler outputs  →  _gather_research_findings (grouped by domain)
                               └─  plan-refine  →  revised PlanArtifact  →  build nodes

BuildHandler  →  gh issue create  +  claude CLI subprocess
                    └─ PR  →  MergeHandler  →  gh pr merge
                                 └─  ReadmeHandler
```

---

## Configuration

| Key | Default | Source |
|-----|---------|--------|
| `KILN_CONCURRENCY` | 3 | env |
| `KILN_MODEL` | `claude-sonnet-4-6` | env |
| `KILN_AGENT_TIMEOUT_MINUTES` | 60 | env |
| `KILN_MAX_RESEARCH_ROUNDS` | 2 | env |
| `KILN_RESEARCH_BACKEND` | `anthropic` | env |
| `KILN_PLAN_BACKEND` | `anthropic` | env |
| `KILN_GH_TOKEN` | — | env |

Repo registry: `~/.kiln/kiln.toml` — maps `owner/repo` → local checkout path + spec dir.

---

## Interfaces

**CLI entry points:**

```
kiln run <spec.md> --repo owner/repo [--dry-run] [--max-budget N]
kiln preflight <spec.md> [--repo-path PATH] [--json]
kiln graph nodes --repo owner/repo
kiln cost [--repo owner/repo]
```

**Python library** (for embedding in other tools):

```python
from kiln.graph.executor import GraphExecutor
from kiln.config import Config
```
