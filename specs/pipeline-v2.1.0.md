# pipeline v2.1.0 — Self-Improvement Config Patches

## Overview

Adds self-improvement capability to `bread-forge/pipeline`. The MetaAnalysisAgent (v2.0.0)
now generates `ConfigPatch` proposals based on drift alerts. These patches modify specific
whitelisted parameters in `~/.pipeline/policy.yaml` or agent configuration. Patches enter
the standard intake queue as `type: config-patch` proposals, always `review-all`. When
approved and applied, the agent re-runs meta-analysis to measure improvement and appends
a calibration record.

## Goals

- **[P0]** `ConfigPatch` dataclass: `param` (dot-path into policy.yaml), `current_value`,
  `proposed_value`, `rationale`, `expected_improvement`
- **[P0]** Parameter whitelist: only these params patchable: `staleness_thresholds.*`,
  `trigger_frequencies.*`, `policy.max_severity`, `policy.max_blast_radius_centrality`,
  `qe.model`, `pe.model`; all others rejected
- **[P0]** `ConfigPatchApplier`: reads policy.yaml, applies patch, writes back atomically;
  validates applied value is within allowed range
- **[P0]** `MetaAnalysisAgent.generate_patches(drift_alerts)` produces list of ConfigPatches;
  each patch targets the most impactful driftable parameter
- **[P0]** Config patches enter intake queue via `pipeline meta-analyze --apply`; presented
  to gate as `type: config-patch` with `review-all` mode regardless of policy threshold
- **[P1]** After patch applied: `MetaAnalysisAgent` re-runs after next N cycles; appends
  `PatchCalibrationRecord` to telemetry noting before/after metric delta
- **[P1]** At least 3 patch proposals that measurably improve metrics when applied
- **[P2]** Patch rollback: `pipeline patch rollback <patch-id>` reverts a config patch
  if the subsequent telemetry shows degradation
- All tests pass; new tests cover patch generation, whitelist enforcement, applier,
  calibration record

## Modules

- `meta/patches`: ConfigPatch dataclass; ConfigPatchApplier; whitelist enforcement
- `meta/calibration`: PatchCalibrationRecord; before/after metric computation
- `meta/agent`: updated to call `generate_patches()`; `--apply` flag wiring
- `cli`: `--apply` flag on `pipeline meta-analyze`; `pipeline patch rollback` command

## Validation

```bash


# Patch generation from drift alerts
uv run python -c "
from pipeline.meta.patches import ConfigPatch
from pipeline.meta.agent import MetaAnalysisAgent
from pipeline.meta.drift import DriftAlert

agent = MetaAnalysisAgent()
alerts = [
    DriftAlert(metric_name='false_positive_rates.repo-audit',
               current_value=0.75, baseline_value=0.25, delta_pct=2.0,
               description='repo-audit FP rate tripled')
]
patches = agent.generate_patches(alerts)
assert len(patches) > 0
print('Generated patches:')
for p in patches:
    print(f'  {p.param}: {p.current_value} -> {p.proposed_value}')
    print(f'  Rationale: {p.rationale}')
"

# Whitelist enforcement
uv run python -c "
from pipeline.meta.patches import ConfigPatch, ConfigPatchApplier
applier = ConfigPatchApplier()

# Whitelisted param — OK
p1 = ConfigPatch(param='staleness_thresholds.repo-audit', current_value=14, proposed_value=7,
    rationale='reduce staleness window', expected_improvement='lower FP rate')
assert applier.is_whitelisted(p1), 'Should be whitelisted'

# Non-whitelisted — rejected
p2 = ConfigPatch(param='policy.gate_mode', current_value='review-all', proposed_value='full-auto',
    rationale='', expected_improvement='')
assert not applier.is_whitelisted(p2), 'Should NOT be whitelisted'
print('Whitelist enforcement OK')
"

# All tests pass
uv run python -m pytest tests/ -q
```
