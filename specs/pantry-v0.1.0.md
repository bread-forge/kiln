# pantry v0.1.0 — Meal Planner

## Overview

Pantry generates a personalised weekly meal plan and delivers it via email and a local web UI.
A scheduled job fires on a configurable day (Friday by default) and emails N meal suggestions
(default 7) with a link to the web UI for review and refinement. The user can accept meals,
reject them, or request alternatives with a free-text spec ("more fish, no gluten"). Recipes
combine LLM generation with API-validated data, scaled to 1 person. No shopping or ordering
in this milestone.

## Goals

- **[P0]** `pantry survey` sends a meal plan email and serves the web UI with 7 suggestions
- **[P0]** `pantry survey --days N` returns an N-meal plan; `--date YYYY-MM-DD` anchors the plan to that date; `--meals N` returns exactly N suggestions
- **[P0]** Weekly scheduler fires on the configured day; email arrives with a link to the web UI
- **[P0]** Web UI: browse suggestions, accept/reject meals, request alternatives with free-text spec
- **[P0]** Each confirmed meal has ingredient names and quantities scaled to exactly 1 serving
- **[P0]** Recipes sourced from external recipe API where a match exists; LLM-generated where not
- **[P0]** Email includes full meal plan as plain text (readable without opening the web UI link)
- **[P1]** Recipe API unavailable → fall back to LLM-generated recipe; log warning; plan not blocked
- **[P1]** LLM call fails → retry once; surface error in web UI and email; do not deliver partial plan silently
- **[P1]** Email delivery fails → log error; web UI still serves the plan; retry on next run
- **[P1]** Email credentials not configured → log error at job time; warn on next CLI run
- `make test && make lint` pass on mainline

## Plan Schema

```json
{
  "plan_id": "<uuid>",
  "generated_at": "<iso8601>",
  "days": 7,
  "meals": [
    {
      "name": "<string>",
      "recipe_source": "api | llm",
      "ingredients": [
        { "name": "<string>", "quantity": "<number>", "unit": "<string>" }
      ],
      "instructions": "<string>",
      "servings": 1
    }
  ]
}
```

## Out of Scope

- Shopping / grocery cart integration (v0.2.0)
- Multi-person scaling or household profiles
- Nutritional tracking or dietary scoring
- Budget-constrained planning
- Mobile app or native push notifications

## Open Questions

- **[P2]** Which recipe API (Spoonacular, Edamam, or other) provides the best ingredient data
  quality and free-tier coverage for single-person meal planning? The answer determines the
  recipe resolution pipeline and API client design.
- **[P2]** When the LLM suggests a meal with no API match, what is the right fallback strategy —
  LLM-only recipe, re-suggest a different meal, or present both options to the user?

## Constraints

- Web UI served on localhost only
- Recipe API key in `.env`, gitignored
- Gmail credentials in `.env` or credentials JSON path, gitignored
- All recipes stored scaled to exactly 1 serving
- No direct imports from breadmin-jobman; pantry is standalone
- Email transport follows `breadmin-core/transports/email.py` pattern
- Python 3.11+, uv-managed

## Modules

- `cli`: `pantry survey [--days N] [--date YYYY-MM-DD] [--meals N]`, `pantry schedule --install`
- `planner`: LLM meal suggestion pipeline via breadmin-llm
- `recipes`: recipe API client (Spoonacular/Edamam), LLM-generated fallback, 1-person scaling
- `web`: FastAPI web UI — browse suggestions, accept/reject, free-text refinement, confirm plan
- `email`: email delivery — survey email (plan text + web UI link), confirmation email
- `scheduler`: launchd plist generation and installation
- `db`: SQLite models for current plan and meal history

## Dependencies

- recipes before planner (planner calls recipe resolution)
- planner before web (web serves planner output)
- planner before email (email sends planner output)
- db before planner (planner writes plan to db)
- all of the above before cli integration tests
