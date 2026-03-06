# moot v0.1.0 — Package Foundation

## Overview

Establish the clean structural base that all future moot councils depend on. The
existing codebase has grown organically to ~15k lines with council logic, stale
integrations (Gmail, Calendar, Fitbit, Rampart), a standalone scheduler that duplicates
breadmin-jobman's job, and an overly coarse module table that prevents isolated agent
dispatch. This milestone performs no new feature work — it defines the CouncilPlugin
protocol, migrates existing councils (Prism, Inquiry) to implement it, removes
everything stale, and gives the package a clean module boundary map.

## Goals

- **[P0]** `from moot.councils import CouncilPlugin` exposes a typed `typing.Protocol` with `council_id: str` and `async def run(topic: Topic) -> CouncilOutput`
- **[P0]** Prism and Inquiry both implement `CouncilPlugin`; existing behavior unchanged
- **[P0]** `src/moot/integrations/` contains only `notion.py` and `breadmin.py`; Gmail, Calendar, Fitbit stubs deleted
- **[P0]** `src/moot/procedures/` is removed; routing logic merged into `src/moot/councils/`
- **[P0]** Rampart removed from all source and config references
- **[P0]** `src/moot/scheduler.py` deleted; morning/evening/weekly cycles registered as breadmin-jobman handlers (`moot-heartbeat`, `moot-morning-cycle`, `moot-evening-cycle`, `moot-weekly-cycle`)
- **[P0]** Budget/cost tracking removed from `heartbeat.py`; breadmin-llm `token_usage` table is the single source of truth
- **[P0]** `library_summaries` SQLite view exists in moot's DB schema exposing the stable contract fields for jobman's `LibraryReader`
- **[P1]** Router updated from GPT-5 Nano to `claude-haiku-4-5-20251001` via breadmin-llm `ProviderRegistry`
- **[P1]** CLAUDE.md module table has ≥10 fine-grained labels replacing the single `mod:moot`
- `make test && make lint` pass on mainline

## Out of Scope

- RGB council (v0.2.0)
- Financial council (v0.3.0)
- New feature work of any kind
- Identity model changes
- Personality seed definitions

## Constraints

- `CouncilPlugin` must be a `typing.Protocol` — no forced inheritance for existing councils
- Router uses `claude-haiku-4-5-20251001` via breadmin-llm ProviderRegistry
- All existing Prism and Inquiry tests must pass after migration

## Modules

- `councils`: `src/moot/councils/` — CouncilPlugin protocol, council manifests, merge of procedures/
- `integrations`: `src/moot/integrations/` — remove stale stubs, keep notion + breadmin
- `router`: `src/moot/router.py` — swap GPT-5 Nano → Claude Haiku
- `heartbeat`: `src/moot/heartbeat.py` — remove budget/cost tracking
- `db`: `src/moot/db.py`, `src/moot/schema.sql` — add `library_summaries` view
- `infra`: `CLAUDE.md` module table, `pyproject.toml`, delete `scheduler.py`
