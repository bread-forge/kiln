# pantry — Campaign

Pantry is a personal meal-planning and grocery assistant: from weekly suggestion to pre-filled
grocery cart in two milestones.

## Repo

`bread-wood/pantry` — Python 3.11+, uv-managed, `mainline` default branch.

## Milestones

| # | Version | Name | Depends On | Status |
|---|---------|------|------------|--------|
| 1 | v0.1.0  | Meal Planner   | —      | pending |
| 2 | v0.2.0  | Shopping Cart  | v0.1.0 | pending |

## End State (v0.2.0)

The user receives a Friday email with a personalised meal plan, clicks through to a web UI to
accept or refine suggestions, confirms the plan, and gets a checkout link that pre-fills a
grocery cart at their delivery service. All they do is click "place order".

## Milestone Summaries

### v0.1.0 — Meal Planner
LLM-driven meal suggestions backed by a recipe API (Spoonacular/Edamam), FastAPI web UI for
review and refinement, weekly email delivery, launchd scheduler. Single-person scaling, 7
meals default. Spec: `specs/pantry-v0.1.0.md`.

### v0.2.0 — Shopping Cart
Ingredient aggregation across meals, unit normalisation, delivery API checkout link (Instacart
Connect or best available alternative), shopping cart view in web UI, plain-list email fallback.
Spec: `specs/pantry-v0.2.0.md`.

## Cross-Milestone Contract

The plan JSON schema produced by v0.1.0 and consumed by v0.2.0. These fields must be stable
after v0.1.0 ships:

```json
{
  "plan_id": "<uuid>",
  "meals": [
    {
      "ingredients": [
        { "name": "<string>", "quantity": "<number>", "unit": "<string>" }
      ]
    }
  ]
}
```

Do not change `plan_id` or `ingredients` field shapes after v0.1.0 merges.

## Cross-Milestone Open Questions

- **[P1]** Delivery API selection (v0.2.0 design blocker): must be investigated as a research
  node during v0.1.0 execution so v0.2.0 design can start immediately after v0.1.0 ships.
  See `pantry-v0.2.0.md` Open Questions.
