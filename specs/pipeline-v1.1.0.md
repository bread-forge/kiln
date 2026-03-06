# pipeline v1.1.0 — Auto-Approve Dashboard + Alerting

## Overview

Adds the operational dashboard and alerting system for auto-approved proposals to
`bread-forge/pipeline`. The dashboard (Textual app or Rich Live) shows all auto-approved
proposals, their outcomes, and the running agreement/failure rates. Alerting dispatches
notifications when auto-approved proposals fail verification. The policy engine is promoted
to support the `medium` severity tier after demonstrating sufficient `low` tier track record.

## Goals

- **[P0]** `pipeline dashboard` launches a Textual app showing:
  (1) active cycles, (2) auto-approved proposals + outcomes (pass/fail/pending),
  (3) current agreement rate, (4) current failure rate, (5) circuit-breaker state (placeholder)
- **[P0]** `AlertDispatcher`: notifies on auto-approved verification failure; configurable
  transports in `~/.pipeline/policy.yaml`: `print` (default), `webhook` (HTTP POST),
  `slack_webhook`
- **[P0]** Failure alert payload: proposal_id, spec path, failure_details, repo, timestamp
- **[P1]** Medium severity tier promotion: `pipeline enable-auto --tier medium` allowed
  after 30-day track record at `low` tier with <5% failure rate; requires explicit
  confirmation prompt
- **[P1]** Dashboard auto-refreshes every 30s; `r` key forces refresh
- **[P2]** `pipeline dashboard --repo <owner/repo>` filters to single repo
- **[P2]** Webhook alert format follows Slack incoming webhook schema (works with most
  chat tools via webhook)
- All tests pass; new tests cover AlertDispatcher, tier promotion guard, dashboard data

## Modules

- `dashboard/app`: Textual App; proposal table; metric panels; refresh loop
- `dashboard/data`: data loader from BeadStore + telemetry + event log
- `alert/dispatcher`: AlertDispatcher; print/webhook/slack_webhook transports
- `policy/tiers`: tier promotion guard; track record validator
- `cli`: `pipeline dashboard` command; `--tier` flag on `pipeline enable-auto`

## Validation

```bash
cd bread-forge/pipeline

# Dashboard launches (smoke test)
timeout 3 uv run pipeline dashboard || true

# Alert dispatcher fires on failure
uv run python -c "
import json
from pipeline.alert.dispatcher import AlertDispatcher

alerts = []
dispatcher = AlertDispatcher(transport='print')
dispatcher.on_failure(
    proposal_id='p-test',
    spec_path='/tmp/spec.md',
    failure_details=['test count regressed'],
    repo='bread-forge/kiln'
)
print('Alert dispatched')
"

# Tier promotion blocked without track record
uv run pipeline enable-auto --repo bread-forge/kiln --tier medium 2>&1 | grep -i "30.day\|track record\|insufficient"

# Webhook transport (dry-run)
uv run python -c "
from pipeline.alert.dispatcher import AlertDispatcher
d = AlertDispatcher(transport='webhook', webhook_url='http://localhost:9999/noop')
try:
    d.on_failure('p1', '/tmp/s.md', ['failed'], 'bread-forge/kiln')
except Exception as e:
    print(f'Expected connection error: {e}')
"

# All tests pass
uv run python -m pytest tests/ -q
```
