# pipeline v1.0.0 — Policy Engine + Shadow Mode

## Overview

Adds a policy engine to `bread-forge/pipeline` that can automatically approve proposals
below a configured severity/blast-radius threshold. Ships in shadow mode first: the engine
runs but doesn't dispatch — its decisions are logged and compared against what a human
would decide. Shadow mode runs for at least 50 proposals before live auto-approve is
enabled. >90% agreement required.

## Goals

- **[P0]** `PolicyEngine` class: evaluates proposals against threshold rules from
  `~/.pipeline/policy.yaml`; returns `PolicyDecision(auto_approve: bool, reason: str)`
- **[P0]** Threshold rules: `max_severity` (default: low), `max_blast_radius_centrality`
  (default: 0.3), `max_modules_affected` (default: 2), `allowed_types` (default: docs, tests)
- **[P0]** Shadow mode: `pipeline run --shadow` runs PolicyEngine, logs decisions to
  `~/.pipeline/shadow/{repo}.jsonl`, presents all proposals to human gate regardless
- **[P0]** `pipeline shadow-report --repo <owner/repo>` shows: total proposals evaluated,
  auto-approve decisions, human decisions, agreement rate, disagreement breakdown
- **[P0]** >90% agreement required before `pipeline enable-auto --repo <owner/repo>` succeeds
- **[P1]** Live auto-approve: proposals below threshold dispatched immediately; mandatory
  post-execution verification; `PolicyAutoApproved` event emitted
- **[P1]** Per-type overrides in policy.yaml: security findings always `review-all`;
  self-modification always `review-all` (target repo == pipeline repo)
- **[P2]** `pipeline policy validate` lints `~/.pipeline/policy.yaml` for valid thresholds
- All tests pass; new tests cover PolicyEngine evaluation, shadow log, agreement computation

## Modules

- `policy/engine`: PolicyEngine; threshold evaluator; policy.yaml reader
- `policy/shadow`: shadow log writer; agreement rate computer
- `policy/report`: shadow report generator; agreement breakdown
- `cli`: `pipeline shadow-report`, `pipeline enable-auto`, `pipeline policy validate`;
  `--shadow` flag on `pipeline run`

## Validation

```bash


# Policy engine approves low-severity doc change
uv run python -c "
from pipeline.policy.engine import PolicyEngine
engine = PolicyEngine()
proposal = {'severity': 'low',
    'blast_radius': {'centrality': 0.1, 'modules_affected': ['README.md']},
    'type': 'docs'}
decision = engine.evaluate(proposal)
assert decision.auto_approve
print('Policy engine OK:', decision.reason)
"

# Policy engine blocks high-severity change
uv run python -c "
from pipeline.policy.engine import PolicyEngine
engine = PolicyEngine()
proposal = {'severity': 'high',
    'blast_radius': {'centrality': 0.8, 'modules_affected': ['executor.py', 'cli.py', 'config.py']},
    'type': 'impl'}
decision = engine.evaluate(proposal)
assert not decision.auto_approve
print('Policy block OK:', decision.reason)
"

# Shadow mode runs
uv run pipeline run --repo bread-forge/kiln --shadow

# Shadow report
uv run pipeline shadow-report --repo bread-forge/kiln

# enable-auto blocked if <90% agreement
uv run pipeline enable-auto --repo bread-forge/kiln 2>&1 | grep -i "insufficient\|agreement\|50 proposals"

# All tests pass
uv run python -m pytest tests/ -q
```
