# pipeline v3.0.0 — Kill Switch + Circuit Breakers

## Overview

Adds safety infrastructure to `bread-forge/pipeline` required for autonomous operation:
a kill switch (single command reverts all repos to observe-only), and circuit breakers
(automatic revert to `review-all` when auto-approved verification failure rate spikes above
threshold). These are the safety rails that make full-auto trustworthy.

## Goals

- **[P0]** Kill switch: `pipeline halt` writes `~/.pipeline/halt.lock`; all pipeline
  processes check this file before dispatching; clears on `pipeline resume`
- **[P0]** Kill switch effect: (1) no new cycles start, (2) running cycles complete
  current phase but do not advance, (3) no new auto-approves dispatched
- **[P0]** `KillSwitch.is_halted()` class method; checked at dispatch, cycle start, auto-approve
- **[P0]** Circuit breaker: `CircuitBreaker` tracks auto-approved proposal outcomes;
  trips when failure rate >20% over rolling 7-day window
- **[P0]** On trip: all repos automatically revert to `review-all`; `CircuitBreakerTripped`
  event emitted; alert dispatched; requires `pipeline reset-circuit --repo <owner/repo>`
  with explicit acknowledgment
- **[P0]** `pipeline circuit-status` shows: state (open/closed/tripped) per repo,
  current failure rate, days in current state
- **[P1]** Circuit breaker dashboard panel in `pipeline dashboard`
- **[P1]** Per-repo circuit breakers: tripping one repo does not affect others
- **[P2]** Gradual recovery: after `reset-circuit`, auto-approve re-enables at 50% of
  previous threshold for 7 days before returning to full threshold
- All tests pass; new tests cover kill switch, circuit breaker trigger/reset, gradual recovery

## Modules

- `safety/kill_switch`: KillSwitch; halt/resume file lock; dispatch guard
- `safety/circuit_breaker`: CircuitBreaker; failure rate tracker; trip/reset
- `cycle`: halt checks at cycle start and phase transitions
- `dispatch/kiln`: halt + circuit breaker checks before auto-dispatch
- `dashboard/app`: circuit breaker panel
- `cli`: `pipeline halt`, `pipeline resume`, `pipeline reset-circuit`,
  `pipeline circuit-status` commands

## Validation

```bash


# Kill switch halts new dispatches
uv run pipeline halt
uv run python -c "
from pipeline.safety.kill_switch import KillSwitch
assert KillSwitch.is_halted(), 'Should be halted'
print('Kill switch active')
"
uv run pipeline resume
uv run python -c "
from pipeline.safety.kill_switch import KillSwitch
assert not KillSwitch.is_halted(), 'Should be resumed'
print('Kill switch cleared')
"

# Circuit breaker trips at >20% failure rate
uv run python -c "
from pipeline.safety.circuit_breaker import CircuitBreaker
cb = CircuitBreaker(repo='bread-forge/kiln', threshold=0.20, window_days=7)
for _ in range(15): cb.record_success(proposal_id=f'p-{_}')
for _ in range(5): cb.record_failure(proposal_id=f'fail-{_}')
assert cb.is_tripped(), f'Expected tripped, rate={cb.failure_rate():.0%}'
print(f'Circuit tripped at {cb.failure_rate():.0%} failure rate')
"

# Circuit status command
uv run pipeline circuit-status

# Reset requires acknowledgment
uv run pipeline reset-circuit --repo bread-forge/kiln 2>&1 | grep -i "confirm\|acknowledge\|acknowledge"

# All tests pass
uv run python -m pytest tests/ -q
```
