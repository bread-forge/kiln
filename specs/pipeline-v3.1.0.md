# pipeline v3.1.0 — Trust Escalation + Full Auto

## Overview

Final milestone: graduated trust escalation and full autonomous operation for
`bread-forge/pipeline`. Trust escalation expands auto-approve thresholds tier by tier
(low → medium → high-non-security) based on rolling track record with human confirmation
at each tier. Full auto requires a 7-day clean run with zero unresolved regressions.
Security findings and self-modification always remain `review-all`.

## Goals

- **[P0]** Trust escalation: `pipeline expand-trust --repo <owner/repo> --tier <low|medium|high>`
  validates track record (30 days, <5% failure rate at current tier) and prompts for
  explicit human confirmation before expanding threshold
- **[P0]** Tier definitions:
  - `low`: severity=low, blast_radius_centrality<0.3, modules_affected<=2, type in [docs, tests]
  - `medium`: severity<=medium, centrality<0.5, modules_affected<=5, type in [docs, tests, impl]
  - `high`: severity<=high, centrality<0.7, modules_affected<=10, type in [docs, tests, impl, refactor]
  - Security + self-modification: always `review-all` regardless of tier
- **[P0]** `TrustRecord`: per-repo, per-tier: start_date, failure_count, total_count,
  failure_rate, current_tier; stored at `~/.pipeline/trust/{repo}.json`
- **[P0]** 7-day full-auto validation: `pipeline validate-full-auto --repo <owner/repo>`
  checks: 7 consecutive days of cycles with zero circuit breaks and zero human-required
  interventions; prints PASS/FAIL
- **[P1]** `pipeline trust-status --repo <owner/repo>` shows current tier, days until
  next expansion eligible, current track record
- **[P1]** Automatic threshold rollback: if failure rate exceeds 10% after expansion,
  automatically revert to previous tier and alert
- **[P2]** Multi-repo trust coordination: if repo A's changes affect repo B and repo B's
  circuit breaks, A's trust record is also flagged
- All tests pass; integration tests simulate full-auto week, trust escalation, automatic rollback

## Modules

- `safety/trust`: TrustRecord; TrustEscalation; eligibility checker; tier definitions
- `policy/engine`: updated with tier-aware threshold evaluation
- `cli`: `pipeline expand-trust`, `pipeline trust-status`, `pipeline validate-full-auto`

## Validation

```bash
cd bread-forge/pipeline

# Trust escalation eligibility check
uv run python -c "
from pipeline.safety.trust import TrustEscalation
te = TrustEscalation(repo='bread-forge/kiln')
eligible, reason = te.eligible_for_expansion(tier='medium')
print(f'Eligible: {eligible}, reason: {reason}')
assert not eligible, 'Fresh install should not be eligible'
"

# Trust status command
uv run pipeline trust-status --repo bread-forge/kiln

# Tier definitions are enforced
uv run python -c "
from pipeline.policy.engine import PolicyEngine
engine = PolicyEngine(tier='medium')

# Medium: severity=high blocked
proposal_high = {'severity': 'high', 'blast_radius': {'centrality': 0.3, 'modules_affected': ['a.py']}, 'type': 'impl'}
assert not engine.evaluate(proposal_high).auto_approve

# Medium: severity=medium, 3 modules — should pass
proposal_med = {'severity': 'medium', 'blast_radius': {'centrality': 0.3, 'modules_affected': ['a.py','b.py','c.py']}, 'type': 'impl'}
assert engine.evaluate(proposal_med).auto_approve

# Security always blocked
proposal_sec = {'severity': 'low', 'blast_radius': {'centrality': 0.1, 'modules_affected': ['a.py']}, 'type': 'security'}
assert not engine.evaluate(proposal_sec).auto_approve
print('Tier enforcement OK')
"

# Full-auto validation (will fail on fresh install — expected)
uv run pipeline validate-full-auto --repo bread-forge/kiln 2>&1 | grep -i "FAIL\|insufficient\|7.day"

# All tests pass
uv run python -m pytest tests/ -q
```
