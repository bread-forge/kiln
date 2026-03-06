# moot v0.3.0 — Trading Floor

## Overview

A financial deliberation council purpose-built for portfolio decisions. Stateless agent
personas (financial_analyst, risk_officer, macro_strategist, sector_specialist,
quant_researcher, meridian) deliberate on high-stakes breadwinner decisions. A deep-pass
trigger escalates from Sonnet to Opus for decisions above configured thresholds. The
council's verdict is translated into a breadwinner-consumable signal via the
breadmin-jobman contract; no direct moot↔breadwinner imports.

**Prerequisite:** v0.1.0 (Package Foundation); financial council is a `CouncilPlugin`.

## Goals

- **[P0]** Financial council manifest is a valid `CouncilPlugin` loadable by the routing layer
- **[P0]** A deliberation on a financial topic produces a structured verdict: `approve`, `reject`, or `defer` with rationale and confidence level
- **[P0]** Deep-pass trigger fires (Sonnet → Opus) when position change exceeds size threshold, drawdown threshold, or strategy-change flag is set
- **[P0]** Verdict handler translates council output into a breadwinner-consumable signal matching the contract schema at `breadmin-jobman/docs/contracts/verdict-signal-v1.json`
- **[P0]** Heartbeat surfaces docket issues stale longer than configured threshold and issues marked urgent
- **[P0]** Rampart is absent from all configs and source
- **[P0]** Meridian (systems integrator) persona is defined and loads as part of the financial council roster
- **[P1]** Deep-pass threshold misconfigured (zero or negative) → raises at startup, not at trigger time
- **[P1]** breadwinner endpoint unreachable → verdict logged locally; surfaced in heartbeat as undelivered; not auto-retried
- `make test && make lint` pass on mainline

## Verdict Schema

```json
{
  "outcome": "approve | reject | defer",
  "rationale": "<string>",
  "dissents": ["<agent_id>: <brief position>"],
  "confidence": "high | medium | low",
  "deep_pass_used": true
}
```

## Open Questions

- **[P1]** What is the moot-to-breadwinner verdict handler interface? Depends on breadmin-jobman v0.2.0-verdict-bridge. The canonical schema is committed to `breadmin-jobman/docs/contracts/verdict-signal-v1.json` — reference this file; do not design a separate schema.
- **[P1]** What are the deep-pass trigger thresholds? Position size %, drawdown %, and any other signals that warrant Opus-level deliberation. Research must confirm before trigger logic is designed.

## Out of Scope

- Six-layer identity persistence for financial council agents (stateless by design)
- Mirror integration for financial agents
- Reddit signals / options context (v0.4.0)
- Automated trade execution from verdicts (breadwinner executes; moot signals)
- Real-time market data feeds

## Constraints

- Financial council is a `CouncilPlugin` — no special-casing in routing layer
- No direct imports from breadwinner in moot source
- Deep-pass thresholds configurable via env or config, not hardcoded
- Contract is versioned; moot must reject unrecognized schema versions

## Modules

- `councils`: `src/moot/councils/financial/` — council manifest, agent personas, deliberation procedure, deep-pass trigger
- `integrations`: `src/moot/integrations/breadmin.py` — verdict handler, signal delivery
- `heartbeat`: `src/moot/heartbeat.py` — docket staleness/urgency checks
- `cli`: `src/moot/cli.py` — `moot run --council=financial` wiring

## Dependencies

- councils before integrations (integrations deliver council output)
- integrations before heartbeat (heartbeat surfaces undelivered verdicts)
