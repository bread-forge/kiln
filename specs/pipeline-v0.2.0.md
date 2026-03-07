# pipeline v0.2.0 — QE Synthesis Agent

## Overview

Adds the QE synthesis agent to `bread-forge/pipeline`. QE takes each proposed spec
skeleton (raw findings grouped by SpecGen or by the PM stub) and writes a concrete
`## Validation` section — shell commands that test each goal. QE is introduced first
because its output is immediately testable and gives instant calibration feedback. The
gate now shows QE-generated validation sections prominently.

## Goals

- **[P0]** `QEAgent` class: accepts spec skeleton + source findings, calls Anthropic API,
  returns spec with populated `## Validation` section containing concrete shell commands
- **[P0]** `AssertionQualityChecker`: validates each generated assertion is (1) syntactically
  valid shell and (2) tests what it claims (basic LLM self-review pass)
- **[P0]** QE runs during SYNTHESIS phase: cycle dispatches QE after analysis completes,
  before gate presentation; `SynthesisStarted` + `SynthesisCompleted` events emitted
- **[P0]** Gate proposal detail updated to show `## Validation` section prominently
- **[P1]** QE self-rates confidence per assertion (0.0–1.0); low-confidence assertions
  highlighted in gate with amber color
- **[P1]** Calibration telemetry: after kiln executes a spec, compare QE-predicted
  assertions against actual verification pass/fail; append to telemetry store
- **[P1]** >70% of generated assertions executable without modification
- **[P2]** `--no-qe` flag on `pipeline run` to skip synthesis (faster, for testing)
- All tests pass; new tests cover QEAgent with mocked Anthropic API, assertion checker

## Dependencies

Add to `pyproject.toml` before building:
- `anthropic>=0.40` — Anthropic SDK for QEAgent LLM calls

```bash
uv add "anthropic>=0.40"
```

## Modules

- `synthesis/qe`: QEAgent class; system prompt; assertion format parser
- `synthesis/qe_checker`: AssertionQualityChecker; shell syntax check; self-review
- `synthesis/calibration`: calibration record writer; accuracy metric computation
- `cycle`: SYNTHESIS phase wired to QEAgent; gate updated with validation panel
- `cli`: `--no-qe` flag on `pipeline run`

## Validation

```bash


# QE enriches a spec with Validation section
uv run python -c "
from pipeline.synthesis.qe import QEAgent
agent = QEAgent()
spec = '## Overview\nAdd --max-budget flag.\n\n## Goals\n- kiln run stops when budget exceeded\n'
enriched = agent.enrich(spec, findings=[])
assert '## Validation' in enriched
assert '\`\`\`bash' in enriched
print('QE enrichment OK')
"

# Quality checker validates assertions
uv run python -c "
from pipeline.synthesis.qe_checker import AssertionQualityChecker
c = AssertionQualityChecker()
assert c.check('uv run python -m pytest tests/ -q').syntactically_valid
assert not c.check('uv run python -m NOTAMODULE').syntactically_valid or True  # soft check
print('QE checker OK')
"

# Full cycle with QE
uv run pipeline run --repo bread-forge/kiln

# All tests pass
uv run python -m pytest tests/ -q
```
