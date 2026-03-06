# breadwinner v0.2.0 — Iron Harvest

## Overview

Turns breadwinner from a portfolio tracker into a systematic income engine by adding
four primitives: a backtesting engine to validate strategies on history, an IV
infrastructure layer to time entries by implied volatility regime, options position
lifecycle management with proven rules (21 DTE, 50% profit, delta breach), and a
signal pipeline that aggregates everything into a daily actionable brief. A composer
research loop ties it together so strategy research sprints produce testable parameters.

**Prerequisite:** v0.1.0 (Options Layer) — options position models, exit rules, scanner,
and execution pipeline must exist before the backtester and signal pipeline can use them.

## Goals

- **[P0]** `bw backtest run <strategy> --start <date> --end <date>` replays the plan engine over a historical date range and prints annualized Sharpe, max drawdown, win rate, and SPY benchmark comparison
- **[P0]** `bw bars fetch <ticker> --start --end` pre-populates a local OHLCV bar cache; backtester runs entirely from cache with no live API calls during replay
- **[P0]** `bw iv fetch` collects ATM implied volatility for all watchlist tickers and stores in `iv_history`; `bw iv rank <ticker>` returns IV rank and IV percentile
- **[P0]** `bw options scan` returns a ranked list of CSP and covered-call candidates meeting configurable delta, DTE, and IV rank thresholds
- **[P0]** Heartbeat flags positions at 50% profit target, 21 DTE, or configured delta threshold breach
- **[P0]** `bw brief` produces a plain-text morning summary ≤ 4096 characters covering: positions needing attention, IV rank for watchlist, top technical signals, upcoming earnings within 14 days — formatted for Telegram dispatch
- **[P1]** `bw backtest export <run-id>` produces a markdown summary of a completed run readable by composer research agents
- **[P1]** Bar cache miss during replay → abort with error directing user to run `bw bars fetch` first; never fall back to live API during replay
- `make test && make lint` pass on mainline

## Backtest Output Schema

```json
{
  "run_id": "<uuid>",
  "strategy": "<name>",
  "start": "<YYYY-MM-DD>",
  "end": "<YYYY-MM-DD>",
  "metrics": {
    "sharpe": 1.32, "max_drawdown_pct": -12.4,
    "win_rate": 0.68, "total_trades": 47, "benchmark_sharpe": 0.91
  },
  "equity_curve": [["<date>", 0.0]],
  "trade_log": [{ "date": "", "action": "", "symbol": "", "pnl": 0.0 }]
}
```

## Open Questions

- **[P1]** IV data availability via Alpaca: does the option snapshot endpoint provide reliable 30-day ATM IV per ticker? Which expiry/strike selection heuristic yields a consistent IV proxy? If unreliable, is yfinance or CBOE data needed? Research must confirm before IV infrastructure is designed.
- **[P1]** Plan engine time-travel compatibility: the six-phase planning pipeline makes several "today" calls (market clock, latest bars, account state). Can these be injected with a historical `as_of_date`, or do they require mocking? Research must audit `planning.py` before the backtester is designed.

## Out of Scope

- Moot deliberation integration (v0.3.0)
- Live automated execution (all signals surface to `bw execute-trades`)
- Multi-leg complex strategies beyond CSP, covered call, verticals
- Real-time intraday signals (end-of-day, daily bar based)
- Portfolio optimization / mean-variance allocation

## Constraints

- Backtester uses existing `plan_engine.generate_plan()` and `exit_engine` without forking; time-travel via injected `as_of_date`
- SQLite remains the only persistence layer
- IV fetch integrates into `update-state` as optional step, not a separate scheduled job
- Slippage model configurable (default 0.1%); commissions not modeled
- `bw brief` output: plain text, Telegram-safe, ≤ 4096 characters

## Modules

- `backtester`: `src/breadwinner/backtester.py` — bar cache, simulated execution, equity curve, metrics, export
- `iv`: `src/breadwinner/iv.py` — daily IV snapshot collection, IV rank/percentile computation
- `signals`: `src/breadwinner/signals.py` — RSI, MACD, Bollinger Band technical signal scan
- `brief`: `src/breadwinner/brief.py` — morning brief aggregator
- `db`: `src/breadwinner/db.py` — `bar_cache`, `iv_history`, `backtest_runs` tables

## Dependencies

- iv before signals (IV rank feeds signal pipeline)
- signals, iv before brief (brief aggregates both)
- backtester independent (uses existing planning/exit infrastructure)
