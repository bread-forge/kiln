# pantry v0.2.0 — Shopping Cart

## Overview

Takes a confirmed meal plan from v0.1.0 and produces a ready-to-order grocery cart. Ingredients
across all meals are aggregated (duplicates combined, units normalised), mapped to purchasable
products via a grocery delivery API, and a checkout link is generated with the cart pre-filled.
The link is emailed and displayed in a new shopping cart view in the web UI. The user always
completes the purchase themselves — no automatic ordering.

**Prerequisite:** v0.1.0 (meal planner) must ship first — a confirmed plan with ingredient data
is required as input to the cart pipeline.

## Goals

- **[P0]** Web UI: confirming a meal plan shows a "Build cart" button that produces an aggregated shopping list
- **[P0]** `pantry cart [--plan <plan_id>]` outputs aggregated ingredient list as JSON
- **[P0]** Checkout link pre-fills a cart at the delivery service with all required ingredients
- **[P0]** Duplicate ingredients aggregated across meals: "2 cups flour" + "1 cup flour" → "3 cups flour"
- **[P0]** Checkout link emailed and displayed in the web UI shopping cart view
- **[P1]** Checkout link unavailable → email plain ingredient list; web UI shows list with "checkout unavailable" notice
- **[P1]** Product not found for an ingredient → include in plain list with note; do not silently omit
- **[P1]** Checkout link expires → web UI "Regenerate link" button calls API for a fresh link
- **[P1]** Unit normalisation ambiguity (e.g. "2 cloves garlic" + "1 tbsp minced garlic") → flag for manual review in web UI; do not silently merge incompatible units
- `make test && make lint` pass on mainline

## Cart Schema

```json
{
  "plan_id": "<uuid>",
  "items": [
    { "name": "<string>", "quantity": "<number>", "unit": "<string>", "notes": "<string|null>" }
  ],
  "checkout_url": "<string|null>",
  "generated_at": "<iso8601>"
}
```

## Out of Scope

- Automatic order placement (user always clicks through to complete)
- Price comparison across stores
- Pantry inventory / "what do I already have" tracking
- Subscription or recurring order setup
- Delivery time slot selection

## Open Questions

- **[P1]** Is the Instacart Connect API accessible without formal partner approval? If not, what
  is the best available alternative for a pre-filled checkout link (Kroger API, Walmart Grocery
  API, AnyList deep-link, or other)? This is a v0.2.0 design blocker — must be investigated
  during v0.1.0 so design can start immediately after v0.1.0 ships.
- **[P2]** What is the correct strategy for normalising ingredient units across recipes from
  different sources? Does this require a dedicated measurement conversion library?

## Constraints

- User always completes purchase; no automatic ordering
- Delivery API credentials in `.env`, gitignored
- Ingredient list is always complete even when checkout link fails
- No price comparison or store selection in this version
- Pantry remains standalone (no breadmin-jobman imports)
- Python 3.11+, uv-managed

## Modules

- `cart`: ingredient aggregation across meals, unit normalisation, ambiguity flagging
- `delivery`: delivery API client, product matching, checkout link generation and refresh
- `web` (extension): shopping cart view, editable item list, "Build cart" and "Regenerate link" buttons
- `email` (extension): shopping list + checkout link appended to survey email
- `cli` (extension): `pantry cart [--plan <plan_id>]` command
- `db` (extension): cart records, checkout link cache

## Dependencies

- cart before delivery (delivery receives normalised item list)
- delivery before web extension (web calls delivery for checkout link)
- delivery before email extension (email includes checkout link)
- v0.1.0 confirmed plan schema must be stable before any v0.2.0 module begins
