# pipeline v2.0.0 — Meta-Analysis Agent

## Overview

Adds the `MetaAnalysisAgent` to `bread-forge/pipeline`. This agent reads accumulated
telemetry and computes operational health metrics: approval rate, verification pass rate,
false positive rate per analysis agent, PE/QE calibration accuracy, human modification
rate at gate, suppression creation rate. It identifies drift patterns and produces
diagnostic reports. Configuration patches (v2.1.0) come next.

## Goals

- **[P0]** `MetaAnalysisAgent` reads `~/.pipeline/telemetry/{repo}.jsonl`; computes
  `HealthMetrics` dataclass with all tracked metrics per agent and overall
- **[P0]** Drift detection: flags metrics that have degraded >20% vs 30-day baseline;
  returns `list[DriftAlert]` each with: metric_name, current_value, baseline_value,
  delta_pct, description
- **[P0]** Minimum data requirement: returns `InsufficientData` if fewer than 30 complete
  cycles available; `pipeline meta-analyze` prints a clear message
- **[P0]** `pipeline meta-analyze --repo <owner/repo>` prints health report: current
  metrics table, drift alerts, recommended next actions
- **[P1]** Metrics dashboard page in `pipeline dashboard`: trends chart (sparklines) for
  approval rate, verification pass rate, false positive rate over last 30 cycles
- **[P1]** `pipeline meta-analyze --export /tmp/health.json` exports full HealthMetrics
  as JSON for external analysis
- **[P2]** Comparative analysis: `pipeline meta-analyze --compare <other-repo>` shows
  side-by-side metrics for two repos (requires both have sufficient telemetry)
- All tests pass; new tests cover HealthMetrics computation, drift detection, InsufficientData

## Constraints

- Requires `beads>=0.2.0`; uses BeadStore for cycle/finding/proposal reads
- Telemetry computed from event logs + telemetry store; no external APIs needed
- Drift detection uses 30-cycle rolling baseline; shorter history = narrower baseline window

## Modules

- `meta/agent`: MetaAnalysisAgent; HealthMetrics dataclass; InsufficientData
- `meta/metrics`: metric computation functions; per-agent false positive rate; calibration
- `meta/drift`: DriftAlert; drift detection; 30-day baseline computation
- `dashboard/app`: updated with metrics trends page
- `cli`: `pipeline meta-analyze` command; `--export`, `--compare` flags

## Validation

```bash
cd bread-forge/pipeline

# Meta-analyze runs (returns InsufficientData on fresh install)
uv run pipeline meta-analyze --repo bread-forge/kiln

# With synthetic telemetry, drift is detected
uv run python -c "
from pipeline.meta.agent import MetaAnalysisAgent
from pipeline.meta.metrics import HealthMetrics

agent = MetaAnalysisAgent()
# Synthesize metrics showing arch-review false positive rate degraded
metrics = HealthMetrics(
    approval_rate=0.6,
    verification_pass_rate=0.55,
    false_positive_rates={'repo-audit': 0.75, 'security-scan': 0.1},
    pe_calibration_accuracy=0.5,
    qe_calibration_accuracy=0.65,
    human_modification_rate=0.4,
    suppression_creation_rate=0.3,
    total_cycles=35,
)
alerts = agent.detect_drift(metrics, baseline=HealthMetrics(
    approval_rate=0.75, verification_pass_rate=0.80,
    false_positive_rates={'repo-audit': 0.25},
    pe_calibration_accuracy=0.65, qe_calibration_accuracy=0.70,
    human_modification_rate=0.2, suppression_creation_rate=0.1,
    total_cycles=35,
))
assert len(alerts) > 0, 'Expected drift alerts'
print(f'{len(alerts)} drift alerts detected:')
for a in alerts: print(f'  {a.metric_name}: {a.current_value:.0%} vs baseline {a.baseline_value:.0%}')
"

# Export works
uv run pipeline meta-analyze --repo bread-forge/kiln --export /tmp/health.json || true
ls /tmp/health.json 2>/dev/null && python -c 'import json; json.load(open(\"/tmp/health.json\"))' && echo 'Export valid'

# All tests pass
uv run python -m pytest tests/ -q
```
