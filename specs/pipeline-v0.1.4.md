# pipeline v0.1.4 — Budget Tracker + Telemetry + Status CLI

## Overview

Adds operational infrastructure to `bread-forge/pipeline`: a budget tracker (per-agent
and per-cycle cost caps), an append-only telemetry store (operational metrics written
after each cycle), and a status CLI that shows active cycles, suppression counts, and
recent cycle outcomes. With this milestone, `pipeline` is a complete observe-only system
ready for 10+ validation cycles.

## Goals

- **[P0]** `BudgetTracker`: accumulates cost per cycle; stops dispatching analysis agents
  once `--max-analysis-cost` is exceeded; emits `BudgetExceeded` event
- **[P0]** `--max-analysis-cost <usd>` flag on `pipeline run` (default: no limit)
- **[P0]** Telemetry store: append-only JSONL at `~/.pipeline/telemetry/{owner}-{repo}.jsonl`;
  one record per completed cycle with: `cycle_id`, `timestamp`, `approval_rate`,
  `rejection_rate`, `deferral_rate`, `median_review_seconds`, `suppression_count`,
  `total_analysis_cost_usd`, `finding_count`, `proposal_count`
- **[P0]** Telemetry record written at cycle completion (VERIFICATION → NEXT_CYCLE or
  GATE → COMPLETE in observe-only mode)
- **[P1]** `pipeline status` shows: active cycles per repo, pending proposal counts,
  suppression counts, last cycle outcome
- **[P1]** `pipeline telemetry --repo <owner/repo>` shows telemetry table for last N cycles
  (default 10); columns: date, proposals, approved%, rejected%, cost
- **[P2]** Alert when median review time exceeds 5 min for 3 consecutive cycles: print
  warning recommending gate UX review
- All tests pass; new tests cover budget enforcement, telemetry writing, status output

## Modules

- `budget`: BudgetTracker; per-cycle accumulator; `BudgetExceeded` event emitter
- `telemetry`: TelemetryStore; append-only JSONL writer; metric computations from EventLog
- `cli`: `pipeline status`, `pipeline telemetry` commands; `--max-analysis-cost` flag on `run`
- `cycle`: updated to run BudgetTracker during analysis, TelemetryStore at completion

## Validation

```bash
cd bread-forge/pipeline

# Budget cap stops agent dispatch
uv run pipeline run --repo bread-forge/kiln --max-analysis-cost 0.001 2>&1 | grep -i "budget\|exceeded"

# Telemetry written after cycle
uv run pipeline run --repo bread-forge/kiln
ls ~/.pipeline/telemetry/
uv run python -c "
import json
from pathlib import Path
lines = Path('~/.pipeline/telemetry/bread-forge-kiln.jsonl').expanduser().read_text().splitlines()
record = json.loads(lines[-1])
required = {'cycle_id','timestamp','approval_rate','finding_count','proposal_count'}
missing = required - set(record.keys())
assert not missing, f'Missing: {missing}'
print('Telemetry record OK:', record)
"

# Status command
uv run pipeline status

# Telemetry table
uv run pipeline telemetry --repo bread-forge/kiln

# All tests pass
uv run python -m pytest tests/ -q
```
