# moot — Campaign

Moot is an autonomous multi-agent deliberation engine. It externalises structured
thinking: life direction decisions (RGB council), financial portfolio decisions
(Trading Floor), and eventually any domain with a council plugin.

## Repo

`bread-wood/moot` — Python 3.11+, uv-managed, `mainline` default branch.
Depends on `breadmin-llm` and `breadmin-shared` as local path deps in dev.

## Milestones

| # | Version | Name | Depends On | Status |
|---|---------|------|------------|--------|
| 1 | v0.1.0 | Package Foundation | — | pending |
| 2 | v0.2.0 | RGB Council | v0.1.0 | pending |
| 3 | v0.3.0 | Trading Floor | v0.1.0 | pending |
| 4 | v0.4.0 | Signal Expansion | v0.3.0 | pending |

Note: v0.2.0 and v0.3.0 both depend on v0.1.0 but not on each other — they can
be built in parallel once the foundation is in place.

## End State (v0.4.0)

Two active councils: RGB (life decisions, persistent agent identity) and Trading
Floor (financial decisions, stateless personas, breadwinner bridge). Both receive
enriched context: Reddit social signals and live options/crypto portfolio state
injected from breadmin-jobman. All data collection is external to moot.

## Milestone Summaries

### v0.1.0 — Package Foundation
Clean structural base: CouncilPlugin protocol, Prism/Inquiry migrated, stale integrations
removed, scheduler deleted, fine-grained module table, library_summaries DB view.
No new features.

### v0.2.0 — RGB Council
Blue/Green/Red deliberation agents + Grey facilitator, three-round structure,
convergence/divergence output, six-layer persistent agent identity, Mirror integration.

### v0.3.0 — Trading Floor
Stateless financial council, six financial agent personas (including Meridian), deep-pass
trigger (Sonnet→Opus), breadwinner verdict handler via breadmin-jobman contract.

### v0.4.0 — Signal Expansion
Reddit signals store reader + options/crypto portfolio context adapters. Moot consumes
only — all data collection lives in breadmin-jobman.

## Cross-Milestone Contract

The `CouncilPlugin` protocol defined in v0.1.0 must not change shape after it ships.
All councils (v0.2.0, v0.3.0) implement it from day one.

```python
class CouncilPlugin(Protocol):
    council_id: str
    async def run(self, topic: Topic) -> CouncilOutput: ...
```

## Cross-Milestone Open Questions

- **[P1]** breadmin-jobman verdict-bridge schema (`breadmin-jobman/docs/contracts/verdict-signal-v1.json`) must be defined before v0.3.0 Trading Floor begins. This is a cross-repo dependency.
- **[P1]** RGB personality seeds (v0.2.0 Open Question 1) must be researched before the RGB agent implementation begins. If research is not done, agents will role-play labels rather than embody temperament.
