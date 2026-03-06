# pipeline v0.3.0 — PE Synthesis Agent

## Overview

Adds the PE (Product Engineering) synthesis agent to `bread-forge/pipeline`. PE annotates
each proposed spec with: estimated implementation complexity, regression risk, dependency
ordering, and a feasibility verdict. If a spec is deemed infeasible, PE can propose a
restructured `## Modules` section. PE plugs into the calibration loop — estimates are
measured against actual kiln plan node counts.

## Goals

- **[P0]** `PEAgent` class: accepts spec + codebase context (import graph, module sizes
  from repo-audit analysis), calls Anthropic API, returns `PEAssessment`
- **[P0]** `PEAssessment` fields: `complexity` (small/medium/large), `regression_risk`
  (low/medium/high), `depends_on` (list of spec ids), `verdict` (feasible/restructure/reject),
  `restructuring_notes` (str, if verdict=restructure)
- **[P0]** Dependency detection: if spec A and spec B share modules in `blast_radius`,
  PE annotates B as depending on A (alphabetical for determinism unless context suggests
  ordering)
- **[P0]** Gate proposal detail updated with PE assessment panel
- **[P0]** `verdict=reject`: spec archived (not shown in gate), `ProposalBead.status='rejected'`,
  reason logged to event log
- **[P1]** Calibration: after kiln execution, compare `complexity` against actual plan
  node count; write calibration record to telemetry
- **[P1]** Complexity estimates within 2x of actual >60% of the time
- **[P1]** PE can restructure specs: if `verdict=restructure`, PE rewrites `## Modules`
  and the restructured spec re-enters the synthesis queue
- All tests pass; new tests cover PEAgent, dependency detection, reject/restructure flows

## Modules

- `synthesis/pe`: PEAgent class; dependency detection; complexity heuristics
- `synthesis/pe_calibration`: calibration record writer; accuracy metric
- `cycle`: SYNTHESIS phase runs PE after QE; gate updated with PE panel; reject flow
- `cli`: `--no-pe` flag on `pipeline run`

## Validation

```bash
cd bread-forge/pipeline

# PE produces valid assessment
uv run python -c "
from pipeline.synthesis.pe import PEAgent
agent = PEAgent()
spec = open('../kiln/specs/kiln-v0.1.0.md').read()
assessment = agent.assess(spec, codebase_context={})
assert assessment.complexity in ('small','medium','large')
assert assessment.regression_risk in ('low','medium','high')
assert assessment.verdict in ('feasible','restructure','reject')
print('PE assessment:', assessment)
"

# Dependency detection
uv run python -c "
from pipeline.synthesis.pe import PEAgent
agent = PEAgent()
specs = [
    {'id': 'spec-a', 'blast_radius': {'modules_affected': ['cli.py', 'config.py']}},
    {'id': 'spec-b', 'blast_radius': {'modules_affected': ['cli.py']}},
]
deps = agent.detect_dependencies(specs)
assert 'spec-a' in deps.get('spec-b', []), deps
print('Dependency detection OK')
"

# Full cycle with QE + PE
uv run pipeline run --repo bread-forge/kiln

# All tests pass
uv run python -m pytest tests/ -q
```
