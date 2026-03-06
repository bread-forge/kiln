# moot v0.2.0 — RGB Council

## Overview

The RGB council is moot's flagship deliberation engine for life decisions — career
direction, habit evaluation, growth planning. Three agents debate in structured rounds:
Blue (ambition/possibility), Green (cultivation/what is working), Red (limits/risk).
Grey, a pure-process facilitator with no persistent identity, detects convergence or
divergence and produces the final output. Agent identity persists across sessions via a
six-layer model (personality seed, worldview state, journal, relationship map, track
record, context window).

**Prerequisite:** v0.1.0 (Package Foundation) — RGB is built as a `CouncilPlugin` from
the start.

## Goals

- **[P0]** `moot run --council=rgb --topic="<text>"` executes a full deliberation and prints the output
- **[P0]** Blue's Round 1 output is oriented toward possibility/vision; Green's toward landscape assessment; Red's toward risks and limits — role-authentic, not role-playing
- **[P0]** Each agent's Round 2 references specific claims from both other agents' Round 1 (not a restatement)
- **[P0]** Grey terminates deliberation after at most 3 rounds; may terminate earlier on convergence
- **[P0]** Grey produces a convergence synthesis naming which agent's concerns shaped it and any residual caveats
- **[P0]** Grey produces a divergence report with each agent's final position, unresolved tensions, alignment pattern (`blue_green_vs_red`, `red_green_vs_blue`, `blue_red_vs_green`, `no_clear_pattern`), and pattern diagnostic
- **[P0]** Grey never contributes a position, opinion, or perspective to deliberation content
- **[P0]** Agent identity persists across sessions: worldview state, journal, relationship map, track record loaded at start and written at end
- **[P1]** Cold-start handling: missing or corrupt agent state initializes from personality seed only; logged
- **[P1]** Mirror can interview each RGB agent using the standard protocol without accessing Grey or other agents' sealed data
- **[P1]** Empty or missing topic returns an error immediately; no agents invoked
- **[P1]** Model call failure → retry once; second failure → Grey terminates session with error; no partial output delivered
- `make test && make lint` pass on mainline

## Output Schema

```json
{
  "outcome": "convergence | divergence",
  "rounds_completed": 3,
  "synthesis": "<Grey's unified position — convergence only>",
  "positions": { "blue": "<str>", "green": "<str>", "red": "<str>" },
  "alignment_pattern": "blue_green_vs_red | red_green_vs_blue | blue_red_vs_green | no_clear_pattern",
  "diagnostic": "<pattern interpretation — divergence with clear pattern only>",
  "process_log": ["<Grey per-round notes>"],
  "errors": []
}
```

## Open Questions

- **[P1]** What personality seeds produce authentic Blue/Green/Red reasoning as intrinsic temperament rather than label role-playing? Research must determine: what each color sounds like in conversation, how blind spots manifest naturally, how seeds should be structured so orientation is temperament not instruction.
- **[P1]** What heuristics should Grey use to detect convergence vs divergence vs productive-but-unresolved? Naive keyword matching fails. Research must define the convergence algorithm before Grey is designed.

## Out of Scope

- Multiple agents per color
- Automated scheduling or triggering
- Routing architecture changes
- Grey participating in deliberation content
- Personality seed definitions (deferred to research from Open Question 1)
- Financial council (v0.3.0)

## Constraints

- Maximum 3 deliberation rounds; Grey may terminate earlier
- Grey: no personality, no persistent state, no identity layers — pure process
- Agents named by color only: Blue, Green, Red
- Therapeutic isolation: agents cannot access Mirror records or other agents' sealed data
- Deliberations are manually triggered only
- RGB must expose `CouncilPlugin` interface from v0.1.0

## Modules

- `councils`: `src/moot/councils/rgb/` — Blue, Green, Red agents; Grey facilitator; round orchestration; convergence/divergence detection; output synthesis
- `agents`: `src/moot/agents/` — six-layer identity model; session load/write; cold-start handling
- `mirror`: `src/moot/mirror/` — therapeutic isolation interface for RGB agents
- `cli`: `src/moot/cli.py` — `moot run --council=rgb` wiring

## Dependencies

- agents before councils (RGB agents use identity model)
- councils before cli (cli invokes council run)
