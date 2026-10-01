# Penny Lane

Per-store clearance and penny-item detection for Home Depot, Lowe's, Dollar
General and Walmart. A "penny item" is a product whose register price has
reached $0.01: the retailer's internal signal to pull it from the floor, which
sometimes does not happen. This tool answers one question a community list
cannot: **is it on the shelf at *my* store right now?**

Three parts:

- `pennylane/`: Python package. One adapter per retailer, community-list
  ingesters, a SQLite price history, a detector, a Flask API + web UI.
- A macOS app (`/Applications/Penny Lane.app`, built with the Exobrain
  harness `app-shell`) that wraps the local web UI.
- `ios/`: a SwiftUI shell for iPhone with a native VisionKit barcode scanner,
  for use in the store. It renders the same web UI over a token-guarded
  tunnel.

## How it finds things

1. **Community lists** (`sources/`): PennyCentral and PennyRecon JSON feeds
   (Home Depot, with SKU + UPC + internet number), The Freebie Guy,
   RetailShout and RebelDealz (Dollar General, UPCs, the Tuesday list).
   BrickSeek is login-walled and is not used.
2. **Live per-store lookups** (`retailers/`): every reported item is checked at
   the stores you watch. Home Depot's GraphQL returns price, clearance state
   and the on-shelf count; Dollar General's omni API returns price, quantity
   and a literal `pennyOrZeroPriceItem` flag; Lowe's `/wpd/` JSON returns
   price, markdown state, quantity and aisle; Walmart's product page returns
   price and clearance flags for whatever store the session is anchored to.
3. **Sweeps**: Home Depot's in-store "Special Values" listing is walked
   nightly per store (about 320 requests), which builds a local UPC and SKU
   index and samples every markdown step for the cadence model.
4. **The detector** (`detect.py`) scores each (item, store) 0 to 100:
   store-level clearance status, percent off, price-ending ladder, weeks since
   the first markdown, markdown steps seen, "gone online but still on the
   shelf", and community reports joined to local stock. Nothing on the shelf
   caps the score at 20. A register price of $0.01 on a stocked shelf is 100.
5. **Alerts**: a macOS banner for every new alert, and a **Discord DM the
   moment a penny with MSRP at or above `notify.discord.min_msrp` (default
   $100) is on a watched shelf**. MSRP is the lane's original price, else the
   retail price the community feed recorded.
6. **Pulse** (`bin/penny pulse`, hourly under launchd): re-checks the hot set
   (reported items worth at least the floor, plus anything already scoring
   50+) at every watched Home Depot and Dollar General, then re-scores. Lowe's
   and Walmart are left out of the pulse because their request budgets cannot
   take an hourly pass.

## Lane notes (probed 2026-09-30)

| Lane | Works | Budget / gotchas |
|---|---|---|
| Home Depot GraphQL | price, `pricing.clearance`, `anchorStoreStatusType`, bopis quantity, store search, Special Values sweep | 0.5 s gap is fine; `pageSize` > 24 = 403; `products(itemIds)` above 12 ids = null; HTTP 206 = upstream hiccup, retry |
| Dollar General omni API | price + qty by UPC, `pennyOrZeroPriceItem`, store search with per-store stock | token bootstrap + one F5 cookie request; no product search at all |
| Lowe's `/wpd/` JSON | price, `priceTypeReason` (`_MD_` = markdown), on-hand qty, aisle | store via `sn` cookie after a warm-up; ~70 requests/15 min = 72+ min block |
| Walmart `__NEXT_DATA__` | price, `priceDisplayCodes.clearance`, UPC, pickup status | PerimeterX wall after ~25 requests; no store switching; `/store/` is walled on first hit |

Everything above is third-party data: the ingesters quote it, nothing in it is
treated as an instruction.

## Setup

```bash
scripts/setup.sh                 # venv, deps, fonts, config.json
# edit config.json: zip, radius, optional store ids per retailer
bin/penny stores                 # fetch stores near the zip; nearest 3 per lane are watched
bin/penny sources                # pull the community lists
bin/penny verify                 # check every reported item at your stores
bin/penny predict                # score + alert
bin/penny pulse                  # hot-set re-check + score (hourly job)
bin/penny serve                  # web UI on http://127.0.0.1:5033
```

launchd: copy `launchd/*.plist` into `~/Library/LaunchAgents` (edit the paths; the jobs spawn `.venv/bin/python` directly because macOS TCC refuses a venv interpreter reached through a shell wrapper from launchd)
and `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/<label>.plist`. The
server job keeps the UI and tunnel up; the scan job runs `all` four times a
day; the sweep job walks Home Depot nightly; the pulse job runs hourly.

## Phone access

The server starts a `cloudflared` quick tunnel in front of a token-guarded
origin and publishes the current URL to a tiny discovery document (Workers KV,
via the MIST Console's share Worker when its credentials are present). The
iPhone app reads the discovery document, so the tunnel URL can change and the
phone still finds the Mac. Pair once from Settings → Phone access (QR). The
token never leaves the QR and the phone.

Without Cloudflare credentials the tunnel still runs; re-pair when the URL
changes.

## iOS

```bash
cd ios && scripts/build.sh            # unsigned compile check
scripts/build.sh --device             # signed (Config/Signing.xcconfig)
scripts/install.sh                    # devicectl install + launch
```

In the store: tap the barcode icon, point at the shelf tag or the box. The
code goes to `/api/lookup`, which resolves it (local index, community
reports, or Dollar General live) and checks every watched store.

## Design

The web UI is Material 3 through Beer CSS in a soft, bright register: light tonal
surfaces, large radii, low elevation, Material Symbols Rounded, and an MD3 scheme
computed once from the copper seed `#b86a2b` with Google's material-color-utilities
and baked into `web/static/app.css` (a dark set is included for a future toggle).
`beer.min.js` is an ES module: load it with `type="module"`.

## Privacy

`config.json` (home zip, store ids), `data/` (database, remote token) and
`ios/Config/Signing.xcconfig` are gitignored.
