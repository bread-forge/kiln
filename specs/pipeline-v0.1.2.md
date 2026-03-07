# pipeline v0.1.2 — Gate TUI (Observe-Only)

## Overview

Adds the approval gate to `bread-forge/pipeline`. The gate is a Textual TUI that presents
proposals to the human with full context. In this milestone the gate is observe-only:
approve/reject/defer actions are logged as events and written to BeadStore as ProposalBeads,
but nothing is dispatched to kiln. The gate must be fast to use — <2 min median review
time per proposal.

## Goals

- **[P0]** Textual TUI with two-pane layout: left = proposal list, right = proposal detail
- **[P0]** Proposal detail shows: why-now (agent, timestamp, staleness class), acceptance
  criteria (from spec `## Validation`), blast radius (modules affected, centrality score),
  source findings (ids + summaries), PE assessment if available
- **[P0]** Gate actions: `a` = Approve, `r` = Reject (prompts for reason), `d` = Defer
  (prompts for date); all log `GateDecision` event and update `ProposalBead`
- **[P0]** Rejection and deferral lower-friction than approval: one keystroke + reason,
  vs two keystrokes for approve (confirm prompt)
- **[P0]** `pipeline gate --repo <owner/repo>` opens gate for pending proposals from
  most recent cycle
- **[P1]** Review time tracked: `GateDecision` event includes `review_seconds`
- **[P1]** Proposal list shows priority order (from PM organizer when available, else
  severity sort)
- **[P1]** `pipeline gate --cycle <cycle-id>` targets a specific cycle
- **[P2]** Keyboard shortcuts shown in footer; `?` opens help overlay
- All tests pass; unit tests cover ProposalBead state transitions, event emission

## Constraints

- Textual >= 0.70; no other TUI frameworks
- Gate runs in terminal; no web UI
- Observe-only: no subprocess dispatch in this milestone

## Modules

- `gate/app`: Textual App class; two-pane layout; keybindings
- `gate/widgets`: ProposalList, ProposalDetail, ActionPrompt widgets
- `gate/actions`: approve/reject/defer handlers; event emitter; ProposalBead updater
- `cli`: `pipeline gate` command; `--repo`, `--cycle` flags

## Validation

```bash


# Gate launches (smoke test — exits after 2s in headless mode)
timeout 5 uv run pipeline gate --repo bread-forge/kiln --headless-test || true

# Gate actions update ProposalBead correctly (unit test)
uv run python -c "
from pipeline.gate.actions import GateActions
from pipeline.store import get_store
store = get_store()

# Create test proposal
from beads import ProposalBead
from datetime import datetime, UTC
p = ProposalBead(proposal_id='p-test', cycle_id='c-test', repo='bread-forge/kiln',
    spec_hash='abc', spec_path='/tmp/spec.md', status='pending',
    created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
store.write_proposal(p)

# Reject it
actions = GateActions(store=store, event_log=None)
actions.reject('p-test', reason='Not now', review_seconds=45)

p2 = store.read_proposal('p-test')
assert p2.status == 'rejected', p2.status
print('Gate action OK')
"

# All tests pass
uv run python -m pytest tests/ -q
```
