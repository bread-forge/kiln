# breadwinner v0.1.0 — Options Layer

## Overview

Extends breadwinner's existing equity pipeline to handle options end-to-end: position
models, exit rule evaluation, multi-strategy scanning, multi-leg order execution, fill
tracking with Greeks, and heartbeat monitoring. Dividend ex-date tracking and a
rebalancing engine round out the equity side. An integration test suite validates the
full stack against the Alpaca paper account.

The `src/breadwinner/llm/` directory is renamed to `src/breadwinner/prompts/` — it
contains prompt builders and response parsers, not LLM provider code. All provider code
belongs in breadmin-llm.

## Goals

- **[P0]** `bw update-state` loads and persists `OptionPosition` objects from strategy YAML; re-running is idempotent
- **[P0]** `bw plan` Phase 2 includes triggered options exits and scanner candidates in the LLM planning context
- **[P0]** `bw plan` Phase 4 proposes concrete options actions (open/close/roll) when conditions are met
- **[P0]** `bw plan` Phase 5 compliance rejects options proposals violating `OptionsProfile` (delta limits, earnings blackout, cash check, covered-call coverage)
- **[P0]** `bw execute-trades` places multi-leg options orders via Alpaca options API; fills recorded with Greeks in `option_fills` DB table
- **[P0]** `bw execute-trades --preview` shows per-position estimated cost in dollars (not allocation %)
- **[P0]** Heartbeat flags open options positions at 21 DTE, 50% profit, or delta breach
- **[P0]** `bw update-state` fetches dividend ex-dates; sell proposals suppressed within the blackout window
- **[P1]** Rebalancing engine detects portfolio drift > 5% and generates proposals preferring long-term gains
- **[P1]** `pytest -m integration` passes against the `alpaca-test` paper account with no failures
- **[P1]** `src/breadwinner/llm/` does not exist; all imports updated to `src/breadwinner/prompts/`
- `make test && make lint` pass on mainline

## Open Questions

- **[P1]** Roll atomicity on Alpaca: does the options API support simultaneous close + open as a single order, or must rolls be two separate orders? Research must check Alpaca docs and paper account behavior before execution design is finalized.
- **[P2]** Covered-call delta tracking: exit rule on option delta alone or net delta of (short call + underlying)? Research should confirm the correct risk measure.

## Out of Scope

- Backtesting engine (v0.2.0)
- IV rank computation (v0.2.0)
- Signal pipeline and morning brief (v0.2.0)
- Moot deliberation bridge (v0.3.0)
- Crypto order execution (state tracked; orders not placed)

## Constraints

- Strategy YAML schema extension must be backward-compatible with existing YAML files
- Execution pipeline handles Level 3 multi-leg strategies available on Alpaca paper
- Integration tests clean up their own orders/positions in `finally` blocks

## Modules

- `models`: `src/breadwinner/models.py` — `OptionPosition`, `OptionExitRule`, `OptionsProfile`, `CryptoPosition`, strategy YAML schema extension
- `options_scan`: `src/breadwinner/services/options_scan.py` — CSP, covered-call, vertical spread, iron condor scanners
- `options_risk`: `src/breadwinner/options_risk.py` — portfolio Greeks aggregation, P&L scenarios, early assignment alerts
- `exit_engine`: `src/breadwinner/exit_engine.py` — `evaluate_option_exit_rules()` (delta, DTE, profit/loss triggers)
- `dividends`: `src/breadwinner/dividends.py` — corporate actions, ex-date calendar, yield-on-cost
- `rebalancing`: `src/breadwinner/rebalancing.py` — drift detection, tax-impact-minimizing proposals
- `planning`: `src/breadwinner/services/planning.py` — Phases 2/4/5 extended for options
- `execution`: `src/breadwinner/services/execution.py` — multi-leg order placement, fill tracking, roll logic
- `db`: `src/breadwinner/db.py` — `option_fills` and `option_roll_history` tables
- `infra`: rename `src/breadwinner/llm/` → `src/breadwinner/prompts/`, update all imports

## Dependencies

- models before all other modules
- options_scan, options_risk, exit_engine, dividends before planning
- planning before execution
- db before execution
