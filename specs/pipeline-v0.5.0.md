# pipeline v0.5.0 — Live Dispatch to Kiln

## Overview

Removes the observe-only constraint. Gate-approved proposals are dispatched to kiln for
execution. `pipeline` calls `kiln run --spec <path> --repo <target>` as a subprocess,
tracks the run via kiln's BeadStore, and emits `ExecutionStarted` / `ExecutionCompleted`
events. This milestone: dispatch only. Verification comes in v0.5.1.

## Goals

- **[P0]** Gate approve action now dispatches: `kiln run --spec <spec-path> --repo <target-repo>`
  invoked as subprocess; `ExecutionStarted` event emitted with `kiln_run_id`
- **[P0]** `ExecutionTracker` polls kiln's BeadStore for run completion; emits
  `ExecutionCompleted` event with outcome (success/failed/abandoned)
- **[P0]** `ProposalBead.status` updated: approved → dispatched → verified (or failed)
- **[P0]** `pipeline run --gate-mode review-all` (default) requires human approval;
  `--gate-mode observe-only` reverts to no dispatch
- **[P1]** `pipeline run --gate-mode full-auto` dispatches all proposals immediately
  (use with caution; for testing only)
- **[P1]** Concurrent execution cap: max N proposals in-flight at once (configurable,
  default 1 in this milestone)
- **[P2]** Execution cost tracked: `ExecutionCompleted` event includes `cost_usd` from
  kiln's cost ledger
- All tests pass; integration tests cover dispatch subprocess, tracker poll, event emission

## Constraints

- Kiln must be installed and on PATH (`uv tool install bread-forge/kiln` or editable)
- Target repo must have a valid `.kiln-scope` file (or `CLAUDE.md`)
- `--gate-mode full-auto` is hidden in help text; not for production use

## Dependencies

Add to `pyproject.toml` before building:
- `kiln @ git+https://github.com/bread-forge/kiln.git` — kiln CLI for dispatching approved proposals

```bash
uv add "kiln @ git+https://github.com/bread-forge/kiln.git"
```

## Modules

- `dispatch/kiln`: KilnDispatcher; subprocess invoker; run-id extractor
- `dispatch/tracker`: ExecutionTracker; BeadStore poller; completion event emitter
- `gate/actions`: approve handler updated to invoke KilnDispatcher
- `cli`: `--gate-mode` flag on `pipeline run`; gate TUI updated with dispatch feedback

## Validation

```bash


# Dispatch runs kiln (dry-run to avoid real LLM calls)
uv run pipeline run --repo bread-forge/kiln --gate-mode review-all
# (approve one proposal in TUI)

# ExecutionStarted event recorded
uv run python -c "
import json
from pathlib import Path
logs = sorted(Path('~/.pipeline/events/bread-forge-kiln').expanduser().glob('*.jsonl'))
events = [json.loads(l) for l in logs[-1].read_text().splitlines() if l.strip()]
types = [e['event_type'] for e in events]
assert 'execution_started' in types, f'Missing execution_started, got: {types}'
print('Dispatch events OK:', types)
"

# ProposalBead transitions to dispatched
uv run python -c "
from pipeline.store import get_store
store = get_store()
proposals = store.list_proposals('bread-forge/kiln', status='dispatched')
assert len(proposals) > 0, 'No dispatched proposals'
print(f'{len(proposals)} proposals dispatched')
"

# All tests pass
uv run python -m pytest tests/ -q
```
