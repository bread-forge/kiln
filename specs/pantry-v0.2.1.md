# pantry v0.2.1 — Instacart Connect Checkout

## Overview

Wire the delivery module stub to the real Instacart Connect API so that
`pantry cart` produces an actual checkout link the user can click to place
their grocery order. The aggregated ingredient list from v0.2.0 is already
correct; this milestone replaces the no-op stub with a working Instacart
client that searches for each ingredient, adds matches to a cart, and returns
a hosted checkout URL.

**Prerequisite:** v0.2.0 (Shopping Cart) — `pantry cart` must produce a
correct aggregated ingredient list as input to the Instacart client.

## Goals

- **[P0]** `pantry cart` prints a JSON result with a non-null `checkout_url`
  when `DELIVERY_PROVIDER=instacart` and valid credentials are configured
- **[P0]** Clicking the checkout URL opens a pre-filled Instacart cart in the
  browser with all matched ingredients
- **[P0]** Ingredients with no Instacart product match are included in the
  JSON output with a note; they do not block the checkout link
- **[P1]** Web UI "Build cart" button shows the checkout link as a clickable
  "Order on Instacart" button when a URL is returned
- **[P1]** `DELIVERY_PROVIDER=stub` (default) still works as before — no
  regression for users without credentials
- **[P2]** Matched product names are included in the cart JSON so the user
  can see what Instacart substituted for each ingredient
- `make test && make lint` pass on mainline

## Open Questions

- **[P0]** Exact Instacart Connect auth scheme (API key header vs OAuth 2.0
  client credentials) and base URL — read from the approved developer
  dashboard before implementing the client.
- **[P0]** Cart creation flow: does Connect expose a "create cart and get
  checkout URL" endpoint, or is it a redirect-based hosted checkout? The
  answer determines whether we store a cart ID or just return the URL.
- **[P1]** Product search: does Connect provide a text-search endpoint for
  matching ingredient names to SKUs, or does it require UPCs? If UPC-only,
  a name→UPC lookup step is needed.

## Constraints

- `DELIVERY_API_KEY` in `.env`, gitignored
- `DELIVERY_STORE_ID` in `.env` — Instacart retailer/store location ID
- `DELIVERY_PROVIDER=instacart` to activate (default remains `stub`)
- No automatic order placement — user always clicks through to complete
- Ingredient list always printed even when checkout link fails
- Pantry remains standalone (no breadmin-jobman imports)

## Modules

- `mod:delivery` — `pantry/delivery/client.py`: implement `InstacartClient`
  with product search and cart creation; `pantry/delivery/link_generator.py`:
  wire Instacart client into checkout link generation
- `mod:web` — `pantry/web/routers/plan.py` or cart view: render "Order on
  Instacart" button when `checkout_url` is present
- `mod:cli` — `pantry/cli/cart.py`: print checkout URL prominently when present

## Dependencies

- delivery before web (web renders the checkout URL from delivery output)
- delivery before cli (cli prints the checkout URL)
