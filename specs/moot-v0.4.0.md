# moot v0.4.0 — Signal Expansion

## Overview

Broadens what councils can see. Two data inputs: Reddit sentiment summaries (written by
a breadmin-jobman job; moot reads from the signals store) and options/crypto portfolio
state (pushed by breadwinner via the JobMan contract from v0.3.0). Moot only consumes —
it never fetches Reddit posts or queries breadwinner directly. All data collection lives
in JobMan job handlers.

**Prerequisite:** v0.3.0 (Trading Floor) — verdict handler and JobMan integration
contract must be stable before the options/crypto context layer is extended.

## Goals

- **[P0]** Financial council deliberations include a "social signals" context block from the signals store when the data is ≤ 24 hours old
- **[P0]** Financial council context includes open options positions (strike, expiry, delta, P&L) when the deliberation involves an optionable position
- **[P0]** Financial council context includes crypto holdings (coin, quantity, current value) for crypto-adjacent deliberations
- **[P0]** No Reddit fetching code exists in `src/moot/` — signals store is the only interface; fetching is a breadmin-jobman job
- **[P0]** No direct imports from `breadwinner` in `src/moot/` — all portfolio state arrives via the JobMan contract
- **[P1]** Signals store absent or stale (> 24 hours) → deliberation proceeds without social signals; warning visible in output
- **[P1]** JobMan payload malformed or missing fields → use fields present; log warning; do not crash
- **[P1]** Contract version mismatch → log error with version info; reject payload rather than silently misinterpret
- `make test && make lint` pass on mainline

## Signals Store Schema (written by JobMan, read by moot)

```json
{
  "schema_version": "1",
  "as_of": "<iso8601>",
  "subreddits": [
    {
      "name": "<str>",
      "sentiment": "bullish | bearish | neutral",
      "key_themes": ["<str>"],
      "summary": "<str>",
      "post_count": 25,
      "fetched_at": "<iso8601>"
    }
  ]
}
```

## Portfolio State Schema (pushed by breadwinner job via JobMan)

```json
{
  "schema_version": "1",
  "as_of": "<iso8601>",
  "options_positions": [
    { "ticker": "<str>", "type": "CSP | CC", "strike": 0.0, "expiry": "<YYYY-MM-DD>", "delta": 0.0, "pnl_pct": 0.0 }
  ],
  "crypto_holdings": [
    { "coin": "<str>", "quantity": 0.0, "value_usd": 0.0 }
  ]
}
```

## Open Questions

- **[P1]** Reddit API access model for the JobMan job: PRAW, scraper, or breadmin abstraction? This is a breadmin-jobman scope question, but the answer determines what fields moot can expect in the signals store schema. Research must define the schema before moot's reader is designed.
- **[P1]** What fields does the financial council need from options/crypto state, and at what granularity? Research must confirm the schema before the context adapter is designed.

## Out of Scope

- Reddit fetching, post scoring, sentiment computation (breadmin-jobman job)
- JobMan job scheduling or trigger configuration
- Real-time Reddit streaming
- Twitter/X or other social platform ingestion
- Automated trade execution
- Relevance detection for injecting options/crypto context (council-layer concern, not adapter)

## Constraints

- Signals store is owned by breadmin-jobman; moot reads via `JOBMAN_SIGNALS_DB` path — never writes
- No Reddit fetching in moot source
- No breadwinner imports in moot source
- Integration contracts versioned; moot rejects unsupported schema versions

## Modules

- `integrations`: `src/moot/integrations/signals.py` — signals store reader, staleness check
- `councils`: `src/moot/councils/financial/` — context adapters for social signals, options state, crypto holdings
- `db`: `src/moot/db.py` — signals cache if needed for staleness tracking

## Dependencies

- integrations before councils (councils call integrations for context data)
