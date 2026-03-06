# pipeline v0.5.3 — Rollback Spec Emission

## Overview

Adds post-merge regression detection and automatic rollback spec generation to
`bread-forge/pipeline`. When the verification tier detects a regression after a PR merges,
it generates a revert spec and adds it to the intake queue as a `severity: critical`
finding. In `review-all` mode a human decides whether to dispatch; in `auto-below-threshold`
mode the rollback policy determines automatic revert eligibility.

## Goals

- **[P0]** `RollbackSpecEmitter`: when `CycleVerdict.verdict == 'fail'` post-merge,
  generates a kiln spec with `## Overview: Revert PR #<N>` and adds it to the intake queue
  as a new `FindingBead` with `severity: critical`, `staleness_class: critical`
- **[P0]** Rollback spec format: valid kiln spec that kiln can execute; includes
  `## Validation` asserting the reverted state is restored
- **[P0]** `GateDecision` event for rollback proposals includes `is_rollback: true`;
  gate renders rollback proposals with red highlight
- **[P1]** Rollback policy in `~/.pipeline/policy.yaml`: `auto_rollback_blast_radius_max`
  (default 0.3); proposals above threshold always go to human gate even in auto mode
- **[P1]** Post-mortem finding: after rollback is approved/dispatched, pipeline generates
  a `FindingBead` with `agent: rollback-postmortem` summarizing what regressed and why
- **[P2]** Rollback loop prevention: if a rollback spec itself fails verification, do NOT
  generate another rollback; escalate to human with `severity: critical` alert
- All tests pass; new tests cover rollback spec generation, gate rendering, loop prevention

## Modules

- `verification/rollback`: RollbackSpecEmitter; rollback spec formatter
- `verification/postmortem`: PostMortemAgent; regression summary generator
- `gate/app`: updated to highlight rollback proposals; `is_rollback` rendering
- `cycle`: updated to run rollback emitter when CycleVerdict fails

## Validation

```bash
cd bread-forge/pipeline

# Rollback spec generated from failed verdict
uv run python -c "
from pipeline.verification.rollback import RollbackSpecEmitter
from pipeline.verification.verdict import CycleVerdict

verdict = CycleVerdict(verdict='fail', proposal_id='p-test', pr_number=42,
    failure_details=['test count dropped from 583 to 580'])
emitter = RollbackSpecEmitter()
spec = emitter.generate(verdict, repo='bread-forge/kiln')
assert '## Overview' in spec
assert 'Revert' in spec or 'revert' in spec
assert '## Validation' in spec
print('Rollback spec OK')
print(spec[:300])
"

# Rollback finding has critical severity
uv run python -c "
from pipeline.verification.rollback import RollbackSpecEmitter
from pipeline.verification.verdict import CycleVerdict
from pipeline.store import get_store

verdict = CycleVerdict(verdict='fail', proposal_id='p-test', pr_number=42,
    failure_details=['regression detected'])
store = get_store()
emitter = RollbackSpecEmitter()
finding = emitter.to_finding(verdict, repo='bread-forge/kiln', cycle_id='c-test')
assert finding.severity == 'critical'
assert finding.staleness_class == 'critical'
print('Rollback finding severity OK')
"

# All tests pass
uv run python -m pytest tests/ -q
```
