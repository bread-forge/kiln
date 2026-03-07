# pipeline v0.5.1 — QE Verifier + Pre-Merge Gate

## Overview

Adds post-execution verification to `bread-forge/pipeline`. The QE verifier runs the
spec's `## Validation` shell commands against the post-execution codebase and reports
pass/fail. A pre-merge verification gate registers a GitHub Commit Status that kiln's
MergeHandler polls before merging — blocking merge on verification failure.

## Goals

- **[P0]** `QEVerifier`: runs each shell command from spec's `## Validation` section
  against the target repo path; reports `VerificationResult` with pass/fail per assertion
- **[P0]** `VerificationResult` fields: `proposal_id`, `assertions_run`, `assertions_passed`,
  `assertions_failed`, `failure_details` (list of failed command + stderr), `verdict`
  (pass/fail/partial)
- **[P0]** Pre-merge gate: after kiln creates a PR, pipeline posts a GitHub Commit Status
  (`state: pending`) on the PR's head commit; QE verifier runs; status updated to
  `success` or `failure`
- **[P0]** Kiln's MergeHandler polls the commit status before merging; blocks on `pending`
  or `failure` (requires MergeHandler update in kiln repo)
- **[P0]** `VerificationVerdict` event emitted with verdict and cost
- **[P1]** >80% of known regressions caught at pre-merge gate
- **[P1]** `pipeline verify --proposal <id>` runs verifier on demand
- **[P2]** Verifier timeout: individual assertions time out after 120s; overall timeout 10m
- All tests pass; new tests cover QEVerifier, status API posting, MergeHandler poll

## Constraints

- GitHub Commit Status API requires `GH_TOKEN` with `repo` scope
- QEVerifier runs assertions in a subprocess with cwd=target-repo-path
- Kiln MergeHandler update is in-scope: modify `src/kiln/graph/handlers/merge.py` to
  poll for pipeline status check before merging

## Dependencies

No new dependencies beyond what is already in `pyproject.toml`.

## Modules

- `verification/qe_verifier`: QEVerifier; assertion runner; VerificationResult
- `verification/status_api`: GitHub Commit Status poster/poller
- `cli`: `pipeline verify` command
- **kiln** `src/kiln/graph/handlers/merge.py`: add status check polling before merge

## Validation

```bash


# QE verifier runs assertions from a spec
uv run python -c "
from pipeline.verification.qe_verifier import QEVerifier
v = QEVerifier(repo_path='../kiln')
spec = '''## Validation
\`\`\`bash
uv run python -m pytest tests/ -q --tb=no -q 2>&1 | tail -1
\`\`\`
'''
result = v.verify(spec)
print(result)
assert result.verdict in ('pass','fail','partial')
assert result.assertions_run > 0
"

# Status API integration (smoke test)
uv run python -c "
from pipeline.verification.status_api import CommitStatusAPI
api = CommitStatusAPI(repo='bread-forge/kiln')
# Will fail gracefully without a real PR SHA
try:
    api.post_pending(sha='abc123', context='pipeline/verify', description='Running')
except Exception as e:
    print(f'Expected error: {e}')
print('Status API instantiation OK')
"

# All tests pass (including kiln MergeHandler tests)
cd ../kiln && uv run python -m pytest tests/unit/test_executor.py tests/integration/test_handlers.py -q
cd ../pipeline && uv run python -m pytest tests/ -q
```
