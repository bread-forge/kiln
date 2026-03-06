# breadwinner — Campaign

Breadwinner is an autonomous portfolio strategy engine: equity + options income
generation, deliberation-gated execution, and video-to-watchlist signal ingestion.

## Repo

`bread-wood/breadwinner` — Python 3.11+, uv-managed, `mainline` default branch.

## Milestones

| # | Version | Name | Depends On | Status |
|---|---------|------|------------|--------|
| 1 | v0.1.0 | Options Layer | — | pending |
| 2 | v0.2.0 | Iron Harvest | v0.1.0 | pending |
| 3 | v0.3.0 | Crossing | v0.2.0, jobman contract | pending |
| 4 | v0.4.0 | Ticker Tape | — (parallel with v0.1.0+) | pending |

v0.4.0 has no hard dependency on v0.1.0–v0.3.0 (uses existing Alpaca client + Claude
SDK, no options-specific code). It can be built in parallel or sequenced last.

## End State (v0.4.0)

A full income engine: options positions tracked and managed by proven rules (21 DTE,
50% profit, delta breach), entries timed by IV rank, backtested strategies, daily
morning brief, moot deliberation gate for high-stakes decisions, and YouTube-to-watchlist
ingestion that feeds the planning pipeline.

## Milestone Summaries

### v0.1.0 — Options Layer
OptionPosition/OptionExitRule/OptionsProfile models, scanner (CSP, CC, verticals),
multi-leg execution with Greeks, dividend ex-date tracking, rebalancing engine, prompts
dir rename.

### v0.2.0 — Iron Harvest
Backtesting engine with local bar cache, IV infrastructure (fetch + rank), options
lifecycle automation (21/50/delta rules), signal pipeline (RSI/MACD/BB), morning brief.

### v0.3.0 — Crossing
moot deliberation bridge: snapshot format, `pending_deliberation` plan state machine,
`breadwinner-council-signal` handler, execution guard with `--force` bypass, audit trail.

### v0.4.0 — Ticker Tape
YouTube → Alpaca watchlist ingestion pipeline: transcript/Gemini decomposition, Claude
thematic extraction, Alpaca financial validation, audit log, queue-drain mode.

## Cross-Milestone Contracts

### prompts/ directory (v0.1.0 rename)
`src/breadwinner/llm/` is renamed to `src/breadwinner/prompts/` in v0.1.0. All
subsequent milestones use `breadwinner.prompts` imports.

### Snapshot schema (v0.3.0)
`bw snapshot --format=jobman` schema_version "1" must not change after v0.3.0 ships
without bumping the version. moot and breadmin-jobman reference this schema.

### Verdict-bridge contract
`breadmin-jobman/docs/contracts/verdict-signal-v1.json` is authored by the jobman
team. breadwinner v0.3.0 cannot begin until this file is committed.

## Cross-Milestone Open Questions

- **[P1]** Alpaca options API roll atomicity (v0.1.0 blocker): must be confirmed before execution design.
- **[P1]** IV data reliability via Alpaca (v0.2.0 blocker): must confirm before IV infrastructure design.
- **[P1]** Plan engine time-travel compatibility (v0.2.0 blocker): must audit `planning.py` before backtester design.
- **[P1]** Verdict-bridge contract (v0.3.0 blocker): must be committed to breadmin-jobman before Crossing begins.
