# breadwinner v0.4.0 — Ticker Tape

## Overview

Converts a YouTube financial video into a validated, tradeable Alpaca watchlist.
Pipeline: video decomposition (transcript or Gemini multimodal) → thematic extraction
via Claude → financial validation against Alpaca asset data → watchlist upsert.
Also supports processing pending entries from the breadmin-youtube queue store.
Output feeds directly into breadwinner's existing `bw plan` pipeline.

## Goals

- **[P0]** `bw ingest --source=youtube --url=<url> --watchlist=<name>` exits 0 and creates or updates a named Alpaca paper watchlist containing only validated tickers
- **[P0]** Command prints a structured summary: tickers extracted, tickers filtered (with reason), final watchlist
- **[P0]** Tickers with market cap < $2B filtered and logged with reason `market_cap_too_small`
- **[P0]** Tickers not in Alpaca's asset universe filtered with reason `not_tradeable`
- **[P0]** Video cannot be decomposed → exit non-zero with clear error; no partial watchlist written
- **[P0]** LLM returns tickers not in Alpaca → logged as `ticker_not_found_in_alpaca`; not silently dropped
- **[P0]** Ingestion results written to `~/.breadwinner/ingest/<date>-<watchlist>.json` for audit
- **[P1]** `bw ingest --from-queue` reads pending entries from breadmin-youtube queue store; updates status to `done` or `error`
- **[P1]** `--dry-run` prints proposed watchlist without writing to Alpaca
- **[P1]** `--append` adds tickers to existing watchlist rather than replacing
- `make test && make lint` pass on mainline

## Audit Log Schema

```json
{
  "schema_version": "1",
  "ingested_at": "<iso8601>",
  "source": "youtube",
  "url": "<str>",
  "watchlist_name": "<str>",
  "decomposition_method": "transcript | gemini_multimodal",
  "llm_raw_output": "<str>",
  "candidates": [
    { "ticker": "<str>", "source": "explicit | inferred", "theme": "<str>",
      "disposition": "accepted | filtered", "filter_reason": "<str|null>" }
  ],
  "final_watchlist": ["<ticker>"]
}
```

## Open Questions

- **[P1]** Transcript vs Gemini multimodal as primary decomposition method: Gemini captures visual signals (charts, slides, on-screen tickers) that transcripts miss but adds API dependency and per-video cost. Research must: (a) test Gemini on 3–5 representative financial videos and compare signal quality vs transcript, (b) price per-video Gemini cost at expected queue volumes, (c) confirm whether Gemini accepts a YouTube URL directly or requires a downloaded file.
- **[P1]** Does Alpaca's asset API return market cap data, or is a separate provider needed (e.g. yfinance)? Research must verify Alpaca's asset payload before a new dependency is introduced.

## Out of Scope

- Scheduled/cron-based ingestion (breadmin-jobman v0.4.0-signal-grid)
- Non-YouTube sources: podcasts, earnings call transcripts, SEC filings
- Automated plan generation triggered by ingestion
- Sentiment scoring or IV-rank filtering at ingestion time
- Speaker attribution or host-vs-guest signal weighting
- TradingView watchlist sync

## Constraints

- Decomposition method configurable via `BREADWINNER_INGEST_DECOMP=transcript|gemini`
- Gemini API key: `GEMINI_API_KEY` env var; never committed
- Thematic analysis always uses Claude via Anthropic SDK — no second LLM SDK for analysis
- Market cap threshold ($2B) configurable via breadwinner config, not hardcoded
- Audit logs append-only; never overwrite a previous run
- `bw ingest` must not trigger any trade or plan execution — read + watchlist-write only
- No direct imports from breadmin-youtube; queue store accessed via SQLite path only

## Modules

- `ingest`: `src/breadwinner/ingest/` — decomposition layer (transcript + Gemini paths), thematic extractor (Claude), financial validator (Alpaca asset data), watchlist writer, audit logger
- `cli`: `src/breadwinner/cli.py` — `bw ingest` command wiring

## Dependencies

- ingest fully self-contained; cli wires it at the end
