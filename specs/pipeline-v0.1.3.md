# pipeline v0.1.3 — Suppression Records

## Overview

Adds the suppression record system to `bread-forge/pipeline`. When the gate rejects or
defers a proposal, a `SuppressionBead` is written. The synthesis layer reads active
suppressions and excludes matching findings from future proposal queues. Without this,
the gate fills with the same proposals every cycle.

## Goals

- **[P0]** On gate reject/defer: `SuppressionBead` written to BeadStore with
  `finding_class` derived from source finding ids, `decision`, `reason`, `expires_at`
- **[P0]** `SuppressionsFilter` reads `BeadStore.list_active_suppressions(repo)` and
  removes matching findings from the proposal queue before presenting to gate
- **[P0]** `finding_class` matching: suppression applies if any source finding's
  `id` starts with the suppression's `finding_class` prefix
- **[P0]** `SuppressionBead.is_active()`: returns False if `expires_at` is set and in
  the past
- **[P1]** `pipeline suppressions list --repo <owner/repo>` shows active suppressions:
  id, finding_class, decision, reason, expires_at
- **[P1]** `pipeline suppressions expire <suppression-id>` manually expires a suppression
- **[P2]** Conditions field: `conditions` string on SuppressionBead; displayed in gate
  when a finding is suppressed but conditions field is set ("Re-surface if: ...")
- All tests pass; new tests cover suppression creation, expiry, filter logic

## Modules

- `suppression`: SuppressionsFilter; SuppressionBead writer (wires gate actions to BeadStore)
- `cycle`: updated to run SuppressionsFilter before gate presentation
- `cli`: `pipeline suppressions list`, `pipeline suppressions expire`

## Validation

```bash
cd bread-forge/pipeline

# Reject creates suppression record
uv run pipeline gate --repo bread-forge/kiln --headless-test --auto-reject-first
uv run pipeline suppressions list --repo bread-forge/kiln

# Suppression excludes finding on next cycle
uv run python -c "
from pipeline.suppression import SuppressionsFilter
from pipeline.store import get_store
from beads import SuppressionBead
from datetime import datetime, UTC
import tempfile
from pathlib import Path

store = get_store()
# Create a suppression
s = SuppressionBead(suppression_id='sup-test',
    finding_class='repo-audit.integration-gap',
    decision='deferred', reason='test',
    created_by='test', created_at=datetime.now(UTC))
store.write_suppression(s)

# Filter removes matching findings
findings = [{'id': 'repo-audit.integration-gap.executor', 'severity': 'high'}]
filt = SuppressionsFilter(store=store)
result = filt.filter(findings, repo='bread-forge/kiln')
assert len(result) == 0, f'Expected 0, got {len(result)}'
print('Suppression filter OK')
"

# Expired suppression does not filter
uv run python -c "
from beads import SuppressionBead
from datetime import datetime, UTC, timedelta
s = SuppressionBead(suppression_id='s-expired',
    finding_class='test', decision='deferred', reason='test',
    created_by='test', created_at=datetime.now(UTC),
    expires_at=datetime.now(UTC) - timedelta(days=1))
assert not s.is_active()
print('Expiry OK')
"

# All tests pass
uv run python -m pytest tests/ -q
```
