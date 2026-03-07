# beads v0.2.0 — Pipeline + Repo-Audit Types

## Overview

Extend `bread-forge/beads` with the bead types needed by the pipeline control plane and
repo-audit tool. All ASDLC components share beads for state persistence — BeadStore's
configurable `beads_dir` parameter means each tool uses its own directory
(`~/.pipeline/beads/`, `~/.repo-audit/beads/`). Adding types here keeps state models
co-located and pydantic-validated across the ecosystem.

## Goals

- **[P0]** `FindingBead` — persists a repo-audit gap finding: `id`, `agent`, `timestamp`,
  `staleness_class`, `confidence`, `evidence_chain`, `reasoning`, `severity`,
  `blast_radius`, `repo`, `cycle_id`
- **[P0]** `CycleBead` — tracks a pipeline cycle: `cycle_id`, `repo`, `phase`
  (analysis/synthesis/gate/execution/verification/complete), `trigger`, `started_at`,
  `completed_at`, `finding_count`, `proposal_count`, `total_cost_usd`
- **[P0]** `ProposalBead` — tracks a spec proposal through the gate: `proposal_id`,
  `cycle_id`, `repo`, `spec_hash`, `spec_path`, `status`
  (pending/approved/rejected/deferred/dispatched/verified), `gate_decision_at`,
  `decision_by`, `human_diff_hash` (set when human modifies spec before approving)
- **[P0]** `SuppressionBead` — persists a gate rejection/deferral: `suppression_id`,
  `finding_class`, `decision` (rejected/deferred), `reason`, `created_by`,
  `created_at`, `expires_at` (None = permanent), `conditions`
- **[P0]** `BeadStore` extended with read/write/list methods for all four new types
- **[P0]** New type exports added to `beads/__init__.py`
- **[P1]** `SuppressionBead.is_active()` method: returns False if expired by date
- **[P1]** `BeadStore.list_active_suppressions(repo)` — filters by is_active()
- All tests pass; new unit tests cover each bead type and store operations

## Constraints

- No breaking changes to existing types (WorkBead, PRBead, MergeQueue, CampaignBead,
  GraphNode, PlanArtifact)
- FindingBead `blast_radius` field: `{"modules_affected": [...], "centrality": float}`
- SuppressionBead `finding_class` is a dot-separated string: `"{agent}.{gap-type}.{location}"`

## Modules

- `beads/types.py` — add FindingBead, CycleBead, ProposalBead, SuppressionBead
- `beads/store.py` — add read/write/list/claim methods for new types
- `beads/__init__.py` — export new types

## Validation

```bash


# All new types instantiate and round-trip through JSON
uv run python -c "
from beads import FindingBead, CycleBead, ProposalBead, SuppressionBead
from datetime import datetime, UTC

f = FindingBead(id='f-001', agent='repo-audit', timestamp=datetime.now(UTC),
    staleness_class='structural', confidence=0.85,
    evidence_chain=['README declares X', 'X not found in AST'],
    reasoning='Gap between docs and code', severity='high',
    blast_radius={'modules_affected': ['executor.py'], 'centrality': 0.7},
    repo='bread-forge/kiln', cycle_id='cycle-001')
print(f.model_dump())

s = SuppressionBead(suppression_id='sup-001', finding_class='repo-audit.integration-gap.executor',
    decision='deferred', reason='waiting for v0.2', created_by='human:bread',
    created_at=datetime.now(UTC))
assert s.is_active()
print('All types OK')
"

# BeadStore round-trips new types
uv run python -c "
import tempfile
from pathlib import Path
from beads.store import BeadStore
from beads import FindingBead, SuppressionBead
from datetime import datetime, UTC

with tempfile.TemporaryDirectory() as tmp:
    store = BeadStore(beads_dir=Path(tmp), repo='bread-forge/kiln')
    f = FindingBead(id='f-001', agent='repo-audit', timestamp=datetime.now(UTC),
        staleness_class='structural', confidence=0.8, evidence_chain=['e1'],
        reasoning='test', severity='medium',
        blast_radius={'modules_affected': ['a.py'], 'centrality': 0.3},
        repo='bread-forge/kiln', cycle_id='c1')
    store.write_finding(f)
    f2 = store.read_finding('f-001')
    assert f2.id == f.id
    print('BeadStore round-trip OK')
"

# All tests pass
uv run python -m pytest tests/ -q
```
