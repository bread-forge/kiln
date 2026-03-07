# repo-audit v0.2.0 — LLM Enrichment Layer

## Overview

Adds LLM-based reasoning to `bread-forge/repo-audit`. The enrichment layer runs after
the code-analysis verdict layer and augments each `FindingBead` with: extended reasoning
(why this matters, what breaks if unfixed), a remediation sketch (what change would close
the gap), and an enriched evidence chain. The code layer remains ground truth; LLM only
adds explanation. Uses `claude-haiku-4-5-20251001` by default for cost-effectiveness on
batch enrichment.

## Goals

- **[P0]** `Enricher` class: batches findings, calls Anthropic API, writes
  `reasoning_extended` and `remediation_sketch` back to FindingBead via BeadStore
- **[P0]** `repo-audit run --enrich` runs enrichment after verdict; `--no-enrich` skips
  (default: enrich if ANTHROPIC_API_KEY set, skip otherwise)
- **[P0]** Enriched fields persisted to FindingBead; `repo-audit list` shows them
- **[P1]** `--model` flag (default: `claude-haiku-4-5-20251001`)
- **[P1]** Batch size configurable (default 10 findings per API call) to stay within
  context limits
- **[P1]** Cost tracked per enrichment run and stored in FindingBead `enrichment_cost_usd`
- **[P2]** Enrichment idempotent: re-running `--enrich` skips already-enriched findings
  unless `--re-enrich` flag set
- All tests pass; new tests cover enricher with mocked Anthropic API

## Constraints

- Uses `anthropic` SDK directly (not kiln's runner)
- Enrichment runs after all code-analysis is complete — never before
- If Anthropic API unavailable, `--enrich` warns and continues with unenriched findings

## Modules

- `enricher`: Enricher class; system prompt; batch API caller; FindingBead patcher
- `cli`: `--enrich` / `--no-enrich` / `--model` / `--re-enrich` flags on `run`

## Validation

```bash


# Enrichment adds required fields (requires ANTHROPIC_API_KEY)
uv run repo-audit run ../kiln --enrich
uv run python -c "
from repo_audit.store import get_store
store = get_store('bread-forge/kiln')
findings = store.list_findings('bread-forge/kiln')
enriched = [f for f in findings if f.reasoning_extended]
assert len(enriched) > 0, 'No enriched findings'
for f in enriched:
    assert f.remediation_sketch, f.id
print(f'{len(enriched)} findings enriched')
"

# Enrichment is idempotent
uv run repo-audit run ../kiln --enrich  # second run skips already-enriched
uv run repo-audit run ../kiln --enrich --re-enrich  # re-enriches all

# Graceful skip without API key
ANTHROPIC_API_KEY="" uv run repo-audit run ../kiln --enrich 2>&1 | grep -i "skip\|warn"

# All tests pass
uv run python -m pytest tests/ -q
```
