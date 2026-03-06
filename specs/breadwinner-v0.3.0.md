# breadwinner v0.3.0 — Crossing

## Overview

Connects breadwinner to moot's financial council. High-stakes portfolio decisions
(large position changes, drawdown response, strategy shifts) are routed through moot
before execution. Breadwinner exposes a clean portfolio state snapshot, manages a
`pending_deliberation` plan state that blocks execution until a verdict arrives, and
consumes verdict signals via a breadmin-jobman handler. All coordination avoids direct
breadwinner↔moot imports.

**Prerequisite:** v0.2.0 (Iron Harvest) for full options state in portfolio snapshots;
`breadmin-jobman/docs/contracts/verdict-signal-v1.json` must be committed (the
contract is defined by the jobman team, not breadwinner).

## Goals

- **[P0]** `bw snapshot --format=jobman` outputs a versioned JSON document with equity positions, options positions (with live Greeks), crypto holdings, and analytics (Sharpe, drawdown, IV rank)
- **[P0]** Planning pipeline identifies deliberation-worthy decisions per configured thresholds and sets `requires_deliberation: true` on the plan
- **[P0]** A `breadwinner-council-signal` breadmin handler receives a verdict signal, logs it to the audit trail, and sets plan state to `approved` / `rejected` / `deferred`
- **[P0]** `bw execute-trades` refuses execution when plan is `pending_deliberation` and prints a clear message; bypassable with `--force` (loud warning)
- **[P0]** When plan transitions to `approved`, `bw execute-trades` proceeds normally
- **[P0]** Audit trail records: plan ID, deliberation submitted at, verdict received at, verdict outcome, confidence level
- **[P1]** Verdict never arrives (moot unavailable) → plan remains in `pending_deliberation`; heartbeat surfaces stale pending plans
- **[P1]** `ConnectionSignal` emitted after each planning run to jobman's knowledge store: which setups triggered, which were passed, IV environment, LLM rationale summary
- `make test && make lint` pass on mainline

## Snapshot Schema (schema_version: "1")

```json
{
  "schema_version": "1",
  "as_of": "<iso8601>",
  "equity_positions": [{ "ticker": "<str>", "quantity": 0, "market_value": 0.0, "weight_pct": 0.0 }],
  "options_positions": [{ "ticker": "<str>", "type": "CSP|CC", "strike": 0.0, "expiry": "<YYYY-MM-DD>", "delta": 0.0, "pnl_pct": 0.0 }],
  "crypto_holdings": [{ "coin": "<str>", "quantity": 0.0, "value_usd": 0.0 }],
  "analytics": { "sharpe_90d": 0.0, "max_drawdown_pct": 0.0, "cash_pct": 0.0, "iv_rank": { "<ticker>": 0.0 } }
}
```

## Open Questions

- **[P1]** `breadwinner-council-signal` handler contract: depends on `breadmin-jobman/docs/contracts/verdict-signal-v1.json`. The breadwinner handler cannot be built until that schema is committed. Reference the canonical file; do not design a separate schema.
- **[P1]** Where does deliberation trigger evaluation live — in `services/planning.py` (breadwinner) or in breadmin-jobman's schedule config? Research must clarify ownership boundary before trigger logic is designed.

## Out of Scope

- Financial council implementation (moot v0.3.0)
- breadmin-jobman verdict routing infrastructure
- Automated execution without deliberation approval
- Multi-council routing

## Constraints

- No direct imports from moot anywhere in breadwinner source
- Snapshot schema versioned; must bump `schema_version` on any field change
- Deliberation thresholds configured via env or YAML config, not hardcoded
- `--force` bypass must print a loud warning and log the override

## Modules

- `snapshot`: `src/breadwinner/snapshot.py` — `bw snapshot --format=jobman` output builder
- `planning`: `src/breadwinner/services/planning.py` — deliberation trigger evaluation, `requires_deliberation` flag, plan state machine
- `handlers`: `src/breadwinner/handlers/breadmin.py` — `breadwinner-council-signal` handler, verdict processing, audit trail
- `db`: `src/breadwinner/db.py` — plan state machine fields, deliberation audit trail

## Dependencies

- snapshot before planning (planning uses snapshot schema)
- planning before handlers (handlers update plan state)
- db before all (state machine fields required)
