# pipeline v0.5.2 — PE Verifier + Regression Checker

## Overview

Adds two more verification agents to `bread-forge/pipeline`: PE verifier (operational
readiness — new deps, config keys, CI pipeline impact) and regression checker (before/after
metrics via GitHub CI API — test count, coverage, build time). Together with QE verifier
from v0.5.1, these three agents form the complete verification tier.

## Goals

- **[P0]** `PEVerifier`: checks operational readiness post-execution:
  (1) new entries in `pyproject.toml [dependencies]` vs baseline — flags undocumented new deps,
  (2) new config env vars referenced in source vs baseline — flags undocumented new vars,
  (3) CI pipeline YAML parses cleanly after change (yaml.safe_load check)
- **[P0]** `RegressionChecker`: reads GitHub Actions run results for the PR branch;
  computes before/after: test count delta, coverage delta (if reported by codecov/pytest-cov),
  build time delta; flags regressions (test count down, coverage down >2%)
- **[P0]** Both verifiers emit `VerificationVerdict` events; pipeline combines all three
  verdicts into one overall `CycleVerdict`
- **[P0]** Baseline snapshots: `~/.pipeline/baselines/{owner}-{repo}.json` stores last
  known-good: dep count, env var count, test count, coverage pct; updated on successful verification
- **[P1]** `pipeline verify --proposal <id> --all` runs QE + PE + regression in sequence
- **[P1]** Zero false rollback recommendations on known-good changes
- **[P2]** Baseline auto-detect: on first run with no baseline, build baseline from current
  main branch state
- All tests pass; new tests cover PEVerifier, RegressionChecker with mock CI responses

## Constraints

- GitHub API requires `GH_TOKEN`; PE verifier runs locally, no API needed
- Coverage regression requires repo to report coverage (pytest-cov or codecov); skipped otherwise
- Test count delta: read from pytest JSON report (`--json-report` plugin); fallback: skip

## Modules

- `verification/pe_verifier`: PEVerifier; dep diff, env var diff, CI YAML check
- `verification/regression`: RegressionChecker; GitHub Actions API reader; metric comparator
- `verification/baseline`: baseline snapshot writer/reader
- `verification/verdict`: CycleVerdict aggregator (combines QE + PE + regression)
- `cli`: `--all` flag on `pipeline verify`

## Validation

```bash


# PE verifier runs against kiln
uv run python -c "
from pipeline.verification.pe_verifier import PEVerifier
v = PEVerifier(repo_path='../kiln')
result = v.verify()
print(result)
assert hasattr(result, 'new_deps')
assert hasattr(result, 'ci_parseable')
print('PEVerifier OK')
"

# Regression checker instantiates (needs GH_TOKEN for full test)
uv run python -c "
from pipeline.verification.regression import RegressionChecker
r = RegressionChecker(repo='bread-forge/kiln')
baseline = r.load_baseline()
print('Baseline:', baseline)
"

# Baseline written on successful verification
uv run python -c "
from pipeline.verification.baseline import BaselineStore
bs = BaselineStore()
bs.write('bread-forge/kiln', {'test_count': 583, 'dep_count': 8})
b = bs.read('bread-forge/kiln')
assert b['test_count'] == 583
print('Baseline round-trip OK')
"

# All tests pass
uv run python -m pytest tests/ -q
```
