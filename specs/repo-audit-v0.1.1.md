# repo-audit v0.1.1 — Four-Layer Comparator + Verdict + Findings CLI

## Overview

Adds the comparator and verdict layers to `bread-forge/repo-audit`. The comparator runs the
four-layer diff against collector + analyzer outputs and produces raw gap signals. The
verdict layer aggregates signals into `FindingBead` records with severity, staleness class,
confidence, and evidence chains. Adds `repo-audit list` to show findings and
`repo-audit run` now produces a complete findings inventory stored via BeadStore.

## Goals

- **[P0]** Four-layer comparator — each layer emits gap signals:
  1. Declared (docs) vs Structural (AST): symbols documented but not in AST, or in AST but undocumented
  2. Structural (import graph) vs Behavioral (reachable): modules imported but unreachable from entry points
  3. Behavioral (reachable) vs Activated (default CLI paths): code reachable but not wired to any CLI command
  4. Tests vs Declared: `## Goals` / `## Validation` assertions not covered by any test
- **[P0]** Verdict layer assigns each signal: `severity` (critical/high/medium/low),
  `staleness_class` (critical/dependency/structural/architectural), `confidence` (0.0–1.0),
  `evidence_chain` (list of strings explaining the finding), `reasoning` (one paragraph)
- **[P0]** `FindingBead` written via BeadStore for each verdict
- **[P0]** `repo-audit run` end-to-end: collect → analyze → compare → verdict → store
- **[P0]** `repo-audit list <repo-path>` prints findings table: id, severity, staleness, summary
- **[P1]** `--min-severity` flag on `run` and `list` (default: low — show all)
- **[P1]** Delta mode: `--since <cycle-id>` shows only findings new since that cycle
- **[P2]** False positive rate <30% when run against a known-good reference repo
- All tests pass; new tests cover all four comparator layers, verdict scoring

## Modules

- `comparator`: four-layer diff engine; each layer is a function returning `list[GapSignal]`
- `verdict`: aggregates GapSignals into FindingBead records; severity/staleness/confidence heuristics
- `cli`: `repo-audit list` command; `--min-severity` and `--since` flags on `run`

## Open Questions

- **[P2]** Confidence scoring: `min(evidence_count * 0.2, 0.95)` — calibrate against known gaps

## Validation

```bash
cd bread-forge/repo-audit

# Full run produces findings
uv run repo-audit run ../kiln
uv run repo-audit list ../kiln

# At least 5 findings against kiln
uv run python -c "
from repo_audit.store import get_store
store = get_store('bread-forge/kiln')
findings = store.list_findings('bread-forge/kiln')
assert len(findings) >= 5, f'Only {len(findings)} findings'
print(f'{len(findings)} findings found')

required = {'id','agent','staleness_class','confidence','evidence_chain','severity'}
for f in findings:
    d = f.model_dump()
    missing = required - set(d.keys())
    assert not missing, f'{f.id} missing {missing}'
print('All findings have required fields')
"

# Delta mode shows only new findings
FIRST_CYCLE=$(uv run repo-audit run ../kiln --print-cycle-id)
uv run repo-audit list ../kiln --since $FIRST_CYCLE  # should show 0 new

# All tests pass
uv run python -m pytest tests/ -q
```
