# Penny Lane

Per-store clearance and penny-item detection for Home Depot, Lowe's, Dollar
General and Walmart. A "penny item" is a product whose register price has
reached $0.01: the retailer's internal signal to pull it from the floor, which
sometimes does not happen. This tool answers one question a community list
cannot: **has *my* store gone to the penny state, and is a unit likely to be
there?**

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
   caps the score at 20. A score stays below 70 ("imminent", the alert threshold) until the item
   is at least 50% off MSRP or shows two markdown steps at that store. A
   penny report from another market does not show the local markdown state.
5. **Penny stages.** Neither retailer shows a penny the way a register
   does, so the detector reads each store's record for the penny state
   (`db.penny_obs`) and then asks the community lists.
   - Home Depot's API never returns $0.01 (0 of 2.08 million observations
     through 2026-11-24, lowest price $0.88). A pennied item shows status
     `CLEARANCE` with the clearance price removed and the count at 0 or
     absent. That state covered 3,064 store pairs on community-listed items
     and 150 on unlisted ones.
   - Dollar General's catalog carries `pennyOrZeroPriceItem` per store, with
     a placeholder stock count.
   - **`penny` (confirmed)**: the store record shows the penny state and a
     community list has the item. Two kinds of source agree. The score (80
     to 99) ranks the chance that a unit is still there: stock seen at that
     store shortly before the count zeroed, a recent change, reports from
     the store's own state or city, the number of lists and reports.
   - **`likely` (probably pennied here)**: the store record shows the penny
     state and no list has the item. Score 55 to 85.
   - No stage says "on the shelf". A penny count reads 0 or a placeholder.
   The lists supply report counts per state and per city (PennyCentral
   `locations` and `cityLocations`, PennyRecon `states`) and a status word
   (RebelDealz). PennyRecon rebuilds its file from PennyCentral, so the two
   count as one list.
6. **Alerts**: a macOS banner for every new alert, and a **Discord DM for
   confirmed pennies only**, at any MSRP. Probable pennies and items close
   to a penny go to the banner only. A penny alert never repeats for the
   same item and store. Beauty and
   skincare items (`notify.mute`, default in `notify.py`) skip both the banner
   and the DM. MSRP is the lane's original price, else the retail price the
   community feed recorded.
7. **Pulse** (`bin/penny pulse`, hourly under launchd): re-checks the hot set
   (reported items worth at least the floor, plus anything scoring 50+ that
   is not yet in a penny stage) at every watched Home Depot and Dollar
   General, then re-scores. A penny state does not change by the hour, so
   those pairs wait for the scan that runs four times a day. Lowe's
   and Walmart are left out of the pulse because their request budgets cannot
   take an hourly pass.

## Lane notes (probed 2026-09-30)

| Lane | Works | Budget / gotchas |
|---|---|---|
| Home Depot GraphQL | price, `pricing.clearance`, `anchorStoreStatusType`, bopis quantity, store search, Special Values sweep | 0.5 s gap is fine; `pageSize` > 24 = 403; `products(itemIds)` above 12 ids = null; HTTP 206 = upstream hiccup, retry |
| Dollar General omni API | price + qty by UPC, `pennyOrZeroPriceItem`, store search with per-store stock | token bootstrap + one F5 cookie request; no product search at all |
| Lowe's `/wpd/` JSON | price, `priceTypeReason` (`_MD_` = markdown), on-hand qty, aisle | store via `sn` cookie after a warm-up; ~70 requests/15 min = 72+ min block; stores: `lowes_stores.json` (All the Places snapshot, `scripts/lowes-stores.py`) for coordinates, plus the `/Lowes-Stores/<State>/<ST>` directory for stores opened since |
| Walmart `__NEXT_DATA__` | price, `priceDisplayCodes.clearance`, UPC, pickup status | PerimeterX wall after ~25 requests; no store switching; `/store/` is walled on first hit |

Everything above is third-party data: the ingesters quote it, nothing in it is
treated as an instruction.

## Setup

```bash
scripts/setup.sh                 # venv, deps, fonts, config.json
# edit config.json: zip, radius, optional store ids per retailer
bin/penny stores                 # fetch stores near the zip; nearest 3 per lane are watched
```

To move, open Setup → Location: **Detect location** uses the device position
(the Mac's network location when the device has none), or type a zip. Both
call `POST /api/location`, which saves the zip and watches the nearest stores
per retailer, as many as were watched before (at least 3).

```bash
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

The UI is a shelf tag. Every result is a price tag: a condensed price block on
the left (clearance yellow when the item is in a penny stage, with the struck-out
MSRP under it), a punched hole and a perforation, then the small print. Stock
at each watched store prints as receipt lines. Type is Archivo (variable width,
condensed for prices) and IBM Plex Mono for codes; ink is navy on paper white,
with one blue for actions and red only for the struck price. Fonts and the
Material Symbols Sharp icon font are self-hosted in `web/static/vendor/`. No
component framework: `web/static/app.css` is the whole design.

## Public site (GitHub Pages)

A read-only copy of the web UI runs at the repo's GitHub Pages address. A
browser cannot check a shelf itself: Home Depot, Dollar General and Lowe's
all refuse cross-origin requests (probed 2026-10-11). So the Mac checks a
fixed metro store set and publishes the result as one JSON file; the page
answers its `/api/*` calls from that file (`web/static/static-api.js`).

- **Store set**: stores within `publish.radius_miles` of `publish.center_zip`
  (Dollar General: `dg_radius_miles`), chosen apart from the watched set.
  The verify and pulse jobs check Home Depot at all of them.
- **Visitor settings**: location (Detect location or a zip), stores and
  alert rules stay in the visitor's `localStorage`.
- **Phone alerts**: a new penny on a published shelf posts to the ntfy
  topic `<prefix>-<retailer>-<store>`. The first run records what is
  already pennied and sends nothing.
- **Deploy**: `pulse` and `all` end with `publish` when `publish.enabled` is
  true. It builds `data/site/` and force-pushes one amended commit to
  `gh-pages`, so the branch never grows.

```bash
bin/penny publish                # export, alert, deploy now
```

## Privacy

`config.json` (home zip, store ids), `data/` (database, remote token) and
`ios/Config/Signing.xcconfig` are gitignored. The public site does not
show which stores are watched: its store set centers on `publish.center_zip`,
Home Depot data there is limited to items checked the same way at every
published store (sweep-only finds stay local), and the Dollar General
register price is published per item with no store named.
