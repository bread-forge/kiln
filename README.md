# kiln

Spec-driven agent executor — part of the breadforge platform.

kiln takes a spec file describing what to build, files GitHub issues, dispatches
Claude Code agents in parallel, tracks state with beads, and merges when CI passes.

No HLD/LLD/research pipeline. Agents reason about approach inline and build directly
from the spec. Docs are generated retroactively from built code.

## Quick Start

```bash
# Install
uv add kiln  # or: pip install kiln

# Register your repo
kiln repo add bread-wood/myproject --local-path ~/dev/myproject

# Run a spec
kiln run specs/v1.0.0-feature.md --repo bread-wood/myproject

# Run with a cost cap
kiln run specs/v1.0.0-feature.md --repo bread-wood/myproject --max-budget 5.00

# Run a full campaign
kiln run specs/campaign.md --repo bread-wood/myproject

# Check status
kiln status --repo bread-wood/myproject

# Show cost summary
kiln cost

# Design a new spec interactively
kiln spec "add order history with export to CSV"
```

## Commands

| Command | Description |
|---------|-------------|
| `kiln run <spec.md>` | Parse spec, file issues, dispatch agents |
| `kiln run <spec.md> --max-budget <usd>` | Stop and report when cumulative spend exceeds cap |
| `kiln plan <spec.md>` | Seed issues without dispatching |
| `kiln run-issue --issue N` | Dispatch a single issue (used by GHA) |
| `kiln init --milestone v1.0.0` | Create a GitHub milestone |
| `kiln status` | Show live bead state table |
| `kiln beads` | Show all beads for a repo |
| `kiln monitor` | Run anomaly detection and repair loop |
| `kiln spec "description"` | Interactive spec-forge |
| `kiln cost` | Show LLM cost summary |
| `kiln health` | Preflight health checks |
| `kiln repo add/list/remove` | Manage platform repo registry |

## Architecture

```
kiln run spec.md
       │
       ▼
  parse spec → file GitHub issues → seed WorkBeads
       │
       ▼
  GraphExecutor (async DAG)
  ┌────────────────────────────────────────┐
  │  OrchestratorLock (fcntl per repo)    │
  │  plan node → expands build nodes      │
  │  research node → Gemini / GPT-4.1     │
  │  build node → Claude agent            │
  │  wait node → blocks on cross-repo dep │
  │  consensus node → votes on proposals  │
  │  design_doc node → LLM design output  │
  │                                        │
  │  concurrency=3  watchdog=60s           │
  │  budget cap checked between dispatches │
  └────────────────────────────────────────┘
       │
       ▼
  MergeQueue → squash merge → close WorkBead
       │
       ▼
  CostLedger → ~/.kiln/runs/{run_id}.jsonl
```

### DAG Executor

The `GraphExecutor` drives an async event loop over an `ExecutionGraph` DAG. Key properties:

- **Dynamic expansion**: `plan` nodes emit new build/merge/readme nodes at runtime; the executor wires overlap edges between build nodes touching the same files.
- **Crash recovery**: nodes found in `running` state on restart are handed to the handler's `recover()` method before re-dispatching.
- **Dry-run mode**: skips build/merge dispatch; creates `WorkBead`s so the plan can be reviewed before agents run.
- **Budget cap**: when `--max-budget` is set, the executor accumulates spend from completed nodes and refuses to dispatch new nodes once the cap is exceeded, marking remaining pending nodes abandoned.
- **Orchestrator lock**: an exclusive `fcntl.flock` on `~/.kiln/locks/{owner}-{repo}.lock` is held for the duration of `GraphExecutor.run()`. A second concurrent invocation against the same repo prints an error and exits 1.
- **BackendRouter**: routes node types to LLM backends — `research`/`plan` nodes to `research_model` (Gemini or GPT-4.1), `build`/`merge`/`readme` nodes to `build_model` (Claude), `wait`/`consensus`/`design_doc` to `design_model`.

### Node Types

| Type | Handler | Description |
|------|---------|-------------|
| `plan` | `PlanHandler` | LLM-driven planning; expands graph with build nodes |
| `research` | `ResearchHandler` | Investigation node; routes to configurable backend |
| `build` | `BuildHandler` | Dispatches a Claude Code agent for one issue |
| `merge` | `MergeHandler` | Squash-merges a PR after CI passes |
| `readme` | `ReadmeHandler` | Generates or updates module README |
| `wait` | `WaitHandler` | Polls until a cross-repo milestone ships |
| `consensus` | `ConsensusHandler` | Selects the best proposal from upstream nodes |
| `design_doc` | `DesignDocHandler` | Generates a design document via LLM |

### Bead System

Beads are the canonical source of truth. All state lives in `~/.kiln/beads/`.

- `WorkBead` — issue lifecycle: `open → claimed → pr_open → merge_ready → closed`
- `PRBead` — PR state: `open → reviewing → merge_ready → merged`
- `MergeQueue` — sequential squash merge ordering
- `CampaignBead` — multi-milestone campaign progress; carries `blocked_by` for cross-repo deps
- `AnomalyBead` — monitor anomalies and repair state

### Cost Tracking

Every completed `run_agent` call appends a record to `~/.kiln/runs/{run_id}.jsonl`:

```json
{"run_id": "...", "node_id": "...", "model": "...", "input_tokens": 1234, "output_tokens": 456, "cost_usd": 0.0123, "timestamp": "2026-03-05T..."}
```

`kiln cost` reads these files and prints per-run and aggregate spend. Token counts and cost are extracted from the `usage` field of the stream-json `result` event emitted by `claude --output-format stream-json --print`.

Errors are classified from the same event into four types: `rate_limit`, `billing_error`, `auth_failure`, `error_max_turns`. On `rate_limit` or `overload`, the agent is retried once with `claude-haiku-4-5-20251001` before the retry budget is decremented.

### Multi-Backend Support

Research and plan nodes can be routed to alternative LLM backends:

- `anthropic` (default) — uses `run_agent` subprocess via Claude
- `gemini` — Google Gemini via `GeminiBackend`
- `openai` — GPT-4.1 via `OpenAIBackend`

Configure via `KILN_RESEARCH_BACKEND` / `KILN_PLAN_BACKEND`.

### Credential Proxy

The loopback credential proxy (`kiln.proxy`) prevents raw API key injection into
agent subprocesses. It starts an HTTP server on `127.0.0.1` at a random port, issues
scoped HMAC tokens (one per node, scoped to `anthropic`/`openai`/`google`), validates
tokens on each request, and forwards traffic to the real upstream API with the real key
injected server-side.

### Cross-Repo Blocking

Declare `blocked_by: ["owner/otherrepo:v1"]` in a `CampaignBead` milestone plan. The
graph builder inserts `wait` nodes that poll until the upstream milestone status reaches
`"shipped"` before the plan node is allowed to run.

### GitHub Actions Integration

`.github/workflows/pipeline.yml` triggers `kiln run-issue` automatically when the
`stage/impl` label is added to a milestoned issue:

```yaml
on:
  issues:
    types: [labeled]
```

### Assessor / Allocator

Before dispatching each agent, kiln estimates task complexity and selects
an appropriate model tier:

- `LOW` → cheap model (haiku) — docs, formatting, config changes
- `MEDIUM` → standard model (sonnet) — feature work, tests
- `HIGH` → capable model (opus) — security changes, multi-module coordination

### Monitor

The monitor runs as a background loop detecting:

- `zombie_pr` — PR with CI failing for too long
- `stuck_issue` — claimed issue with no PR after timeout
- `conflict_pr` — PR with merge conflicts
- `stale_label` — `in-progress` label with no matching claimed bead

Auto-repairs stale labels and rebases conflict branches. Dispatches repair agents
for zombie PRs and stuck issues.

### Spec Forge

`kiln spec "description"` runs an interactive session that:

1. Scans all registered repos' CLAUDE.md files for platform context
2. Conducts a structured interview (repo home, interface, cross-repo deps, unknowns)
3. Drafts a spec file following TEMPLATE.md format
4. Validates required sections are present
5. Checks for architecture violations
6. Updates the platform campaign

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KILN_CONCURRENCY` | `3` | Max parallel agents |
| `KILN_MODEL` | `claude-sonnet-4-6` | Default model for build/merge nodes |
| `KILN_RESEARCH_BACKEND` | `anthropic` | Backend for research nodes (`anthropic`/`gemini`/`openai`) |
| `KILN_PLAN_BACKEND` | `anthropic` | Backend for plan nodes |
| `KILN_RESEARCH_MODEL` | _(backend default)_ | Model override for research nodes |
| `KILN_PLAN_MODEL` | _(backend default)_ | Model override for plan nodes |
| `KILN_BUILD_MODEL` | `claude-sonnet-4-6` | Model for build/merge/readme nodes |
| `KILN_AGENT_TIMEOUT_MINUTES` | `60` | Agent timeout before watchdog kills |
| `KILN_WATCHDOG_INTERVAL_SECONDS` | `60` | Watchdog check interval |
| `KILN_MAX_RETRIES` | `3` | Max retries per node before abandoning |
| `KILN_BEADS_DIR` | `~/.kiln/beads` | Bead storage directory |
| `KILN_GH_TOKEN` | — | GitHub token forwarded to build agents |
| `KILN_PROXY_SECRET` | _(ephemeral)_ | HMAC secret for credential proxy tokens |
| `ANTHROPIC_API_KEY` | — | Required for build/merge/readme nodes |
| `OPENAI_API_KEY` | — | Required when `research_backend=openai` |
| `GOOGLE_API_KEY` | — | Required when `research_backend=gemini` |

## Module Overview

| Module | Description |
|--------|-------------|
| `kiln.cli` | Typer CLI; entry point for all commands including `run-issue` and `cost` |
| `kiln.config` | Runtime `Config` dataclass and platform repo `Registry` |
| `kiln.spec` | Spec and campaign file parsing |
| `kiln.graph.executor` | `ExecutionGraph` and async `GraphExecutor` DAG engine; budget cap enforcement |
| `kiln.graph.builder` | Graph construction helpers and cross-repo blocking wiring |
| `kiln.graph.lock` | `OrchestratorLock` — per-repo exclusive file lock via `fcntl.flock` |
| `kiln.graph.node` | `GraphNode`, `NodeHandler` protocol, `BackendRouter`, `CredentialProxy` facade |
| `kiln.graph.handlers` | One handler per node type: build, merge, plan, research, readme, wait, consensus, design_doc |
| `kiln.backends` | Pluggable LLM backends: `AnthropicBackend`, `GeminiBackend`, `OpenAIBackend` |
| `kiln.proxy` | Loopback credential proxy server and HMAC token issuance/validation |
| `kiln.beads` | `BeadStore` and bead types (`WorkBead`, `PRBead`, `CampaignBead`, …) |
| `kiln.agents.runner` | `run_agent` subprocess runner; `RunResult` with token counts, cost, and error classification |
| `kiln.agents.ledger` | `CostLedger` — append-only JSONL writer at `~/.kiln/runs/` |
| `kiln.monitor` | Anomaly detection, repair loop, and watchdog |
| `kiln.forge` | Interactive spec-forge (interview, draft, validate) |
| `kiln.health` | Preflight health checks |
| `kiln.logger` | Structured logger |

## Tests

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
```

## Spec Format

```markdown
# Project vX.Y.Z — Milestone Name

## Overview
What and why. 1-3 paragraphs.

## Success Criteria
- [ ] Measurable acceptance criterion
- [ ] Another criterion

## Scope
### Included
- Concrete deliverable

### Excluded
- Explicit non-goal

## Key Unknowns
- **[P1]** Open question requiring investigation before impl

## Modules
- module-name: one-line description
```

## Multi-repo Campaign

```markdown
# Platform Campaign

\`\`\`bash
kiln run \
  specs/myproject/v1.0.0-foundation.md \
  specs/myproject/v1.1.0-api.md \
  specs/other-service/v0.1.0-client.md \
  --repo bread-wood/myproject
\`\`\`
```
