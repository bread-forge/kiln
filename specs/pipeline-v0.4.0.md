# pipeline v0.4.0 — PM Proposal Organizer

## Overview

Adds the PM (Proposal Manager) synthesis agent to `bread-forge/pipeline`. PM's scope is
narrow by design: deduplication (don't re-propose suppressed findings), dependency ordering
(topological sort from PE's dependency graph), and staleness-aware ranking. PM does NOT do
product prioritization. The gate queue is now clean, ordered, and suppression-filtered.

## Goals

- **[P0]** `PMAgent` class: accepts pending proposal list, active suppressions from
  BeadStore, PE dependency graph; returns ordered, deduplicated proposal queue
- **[P0]** Deduplication: proposals whose `source_findings` overlap with any active
  `SuppressionBead.finding_class` prefix are excluded
- **[P0]** Dependency ordering: topological sort using PE's `depends_on` graph;
  proposals with unresolved deps held until deps are gate-approved
- **[P0]** Staleness-aware ranking within severity bands: critical > dependency >
  structural > architectural; within same staleness class, newest timestamp first
- **[P1]** >90% true duplicate detection (measured against manually labeled set)
- **[P1]** <10% false deduplication (valid proposals incorrectly suppressed)
- **[P1]** Conflict detection: if two proposals touch the same modules, PM annotates
  both with `conflict_with` field; gate shows them adjacent with warning
- **[P2]** `pipeline pm-report --repo <owner/repo>` shows dedup decisions for last cycle:
  which proposals were suppressed and why
- All tests pass; new tests cover dedup, topological sort, staleness ranking, conflict detection

## Dependencies

Add to `pyproject.toml` before building:
- `anthropic>=0.40` — Anthropic SDK for PMAgent LLM calls (ensure present from v0.2.0)

## Modules

- `synthesis/pm`: PMAgent; dedup logic; topological sorter; staleness ranker
- `synthesis/pm_conflict`: conflict detector for overlapping blast radii
- `cycle`: SYNTHESIS phase runs PM last; gate queue replaced by PM-ordered output
- `cli`: `pipeline pm-report` command; `--no-pm` flag on `pipeline run`

## Validation

```bash


# PM deduplicates suppressed finding
uv run python -c "
from pipeline.synthesis.pm import PMAgent
from beads import SuppressionBead
from datetime import datetime, UTC

agent = PMAgent()
proposals = [{'id': 'p1', 'source_findings': ['repo-audit.integration-gap.executor'],
              'staleness_class': 'structural', 'severity': 'high'}]
suppressions = [SuppressionBead(suppression_id='s1',
    finding_class='repo-audit.integration-gap',
    decision='deferred', reason='later', created_by='test',
    created_at=datetime.now(UTC))]
result = agent.organize(proposals, suppressions=suppressions, dependency_graph={})
assert len(result) == 0, f'Expected 0, got {len(result)}'
print('Dedup OK')
"

# PM orders by dependency
uv run python -c "
from pipeline.synthesis.pm import PMAgent
agent = PMAgent()
proposals = [
    {'id': 'p-b', 'source_findings': [], 'staleness_class': 'structural', 'severity': 'high'},
    {'id': 'p-a', 'source_findings': [], 'staleness_class': 'structural', 'severity': 'high'},
]
result = agent.organize(proposals, suppressions=[], dependency_graph={'p-b': ['p-a']})
assert result[0]['id'] == 'p-a', result
print('Ordering OK')
"

# Full synthesis: QE + PE + PM
uv run pipeline run --repo bread-forge/kiln

# All tests pass
uv run python -m pytest tests/ -q
```
