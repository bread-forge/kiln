# pipeline v0.1.0 — Event Log + Cycle State Machine

## Overview

Bootstrap `bread-forge/pipeline` — the ASDLC control plane. This milestone builds the
core plumbing: an append-only event log (JSONL), the cycle state machine that drives
phase transitions, and BeadStore integration for cycle/proposal state. No gate UI, no
synthesis agents, no dispatch yet — just the engine that will coordinate everything else.

## Goals

- **[P0]** Append-only event log at `~/.pipeline/events/{owner}-{repo}/{cycle-id}.jsonl`;
  `EventLog` class with typed `append(event)` and `replay(cycle_id)` methods
- **[P0]** Typed event dataclasses for all cycle events: `CycleStarted`, `AgentDispatched`,
  `AgentCompleted`, `SynthesisStarted`, `ProposalSubmitted`, `GateDecision`,
  `ExecutionStarted`, `VerificationVerdict`, `CycleCompleted`; all have `event_type`,
  `cycle_id`, `timestamp`
- **[P0]** `CycleStateMachine` drives phase transitions with explicit completion criteria:
  ANALYSIS → SYNTHESIS (all dispatched agents completed), SYNTHESIS → GATE (all synthesis
  done), GATE → EXECUTION (per-proposal), EXECUTION → VERIFICATION (kiln run complete),
  VERIFICATION → NEXT_CYCLE (all verdicts recorded)
- **[P0]** `CycleBead` written to BeadStore on each phase transition (depends on `beads>=0.2.0`)
- **[P0]** `pipeline cycle start --repo <owner/repo>` starts a new cycle; prints cycle-id
- **[P1]** `pipeline cycle status <cycle-id>` shows current phase, events so far
- **[P1]** Event log is replayable: `pipeline cycle replay <cycle-id>` prints all events
- **[P1]** Concurrent cycle protection: one active cycle per repo at a time (flock on
  `~/.pipeline/locks/{repo}.lock`, same pattern as kiln's OrchestratorLock)
- All tests pass; unit tests cover event log, state machine, phase transitions

## Constraints

- Python 3.11+, uv-managed; depends on `beads>=0.2.0`
- Event log: append-only JSONL; existing records are never modified
- State machine: explicit phase enum, not string comparisons

## Modules

- `events`: event dataclasses; EventLog class; JSONL append/replay
- `cycle`: CycleStateMachine; phase enum; transition logic; CycleBead writer
- `lock`: OrchestratorLock (copy pattern from kiln's `graph/lock.py`)
- `store`: BeadStore wiring (`~/.pipeline/beads/`)
- `cli`: `pipeline cycle start`, `pipeline cycle status`, `pipeline cycle replay`

## Validation

```bash


# Start a cycle
CYCLE_ID=$(uv run pipeline cycle start --repo bread-forge/kiln)
echo "Cycle: $CYCLE_ID"

# Event log exists and has CycleStarted event
uv run python -c "
import json
from pathlib import Path
logs = list(Path('~/.pipeline/events/bread-forge-kiln').expanduser().glob('*.jsonl'))
assert logs, 'No event logs'
events = [json.loads(l) for l in logs[-1].read_text().splitlines() if l.strip()]
assert events[0]['event_type'] == 'cycle_started'
assert all({'event_type','cycle_id','timestamp'} <= set(e.keys()) for e in events)
print(f'{len(events)} events, all valid')
"

# CycleBead written to BeadStore
uv run python -c "
from pipeline.store import get_store
store = get_store()
from pathlib import Path
import json
cycle_id = '$CYCLE_ID'
bead = store.read_cycle(cycle_id)
assert bead is not None
assert bead.phase == 'analysis'
print('CycleBead OK:', bead.phase)
"

# Status command works
uv run pipeline cycle status $CYCLE_ID

# Replay shows events
uv run pipeline cycle replay $CYCLE_ID

# Concurrent start blocked by lock
uv run pipeline cycle start --repo bread-forge/kiln && echo "SHOULD NOT PRINT" || echo "Lock worked"

# All tests pass
uv run python -m pytest tests/ -q
```
