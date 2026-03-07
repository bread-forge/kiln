# pipeline v0.1.1 — Analysis Agent Dispatch + Trigger Engine

## Overview

Adds the ability to dispatch analysis agents (repo-audit) from within a pipeline cycle,
and adds a trigger engine that starts cycles in response to events (PR merges, daily
schedule, manual trigger). The cycle state machine transitions from ANALYSIS → SYNTHESIS
when all dispatched agents complete. Agents run as subprocesses and report findings back
via BeadStore.

## Goals

- **[P0]** `AgentDispatcher` runs `repo-audit run <repo-path>` as a subprocess within
  a cycle; emits `AgentDispatched` and `AgentCompleted` events to event log
- **[P0]** Agent output (FindingBeads) read from BeadStore after completion;
  cycle state machine transitions to SYNTHESIS when all agents complete
- **[P0]** `pipeline run --repo <owner/repo>` dispatches configured analysis agents and
  waits for completion; proceeds to synthesis stub (proposal list from raw findings)
- **[P1]** Trigger engine: `pipeline watch --repo <owner/repo>` polls GitHub API every
  60s for new PR merges and triggers a cycle; `--on push` or `--on pr_merge` or `--daily`
- **[P1]** Trigger configuration in `~/.pipeline/config.yaml`:
  ```yaml
  repos:
    bread-forge/kiln:
      triggers: [pr_merge, daily]
      analysis_agents: [repo-audit]
  ```
- **[P1]** `pipeline run --agents repo-audit,security-scan` selects agents to dispatch
- **[P2]** Agent cost tracking: `AgentCompleted` event includes `cost_usd` from agent output
- All tests pass; new tests cover dispatcher, trigger evaluation, cycle completion

## Constraints

- repo-audit must be installed and on PATH (`uv tool install` or editable install)
- GitHub polling uses `GH_TOKEN` env var; gracefully skips polling without it

## Modules

- `dispatch/agent`: AgentDispatcher; subprocess runner; event emitter
- `trigger`: TriggerEngine; GitHub PR poll; schedule evaluation; config reader
- `config`: `~/.pipeline/config.yaml` reader/writer
- `cli`: `pipeline run`, `pipeline watch` commands; `--agents`, `--on` flags

## Validation

```bash


# Full run dispatches repo-audit and collects findings
uv run pipeline run --repo bread-forge/kiln --agents repo-audit

# AgentDispatched + AgentCompleted events in log
uv run python -c "
import json
from pathlib import Path
logs = sorted(Path('~/.pipeline/events/bread-forge-kiln').expanduser().glob('*.jsonl'))
events = [json.loads(l) for l in logs[-1].read_text().splitlines() if l.strip()]
types = [e['event_type'] for e in events]
assert 'agent_dispatched' in types, types
assert 'agent_completed' in types, types
print('Events OK:', types)
"

# Findings available in BeadStore after run
uv run python -c "
from pipeline.store import get_store
store = get_store()
findings = store.list_findings('bread-forge/kiln')
assert len(findings) > 0, 'No findings'
print(f'{len(findings)} findings collected')
"

# All tests pass
uv run python -m pytest tests/ -q
```
