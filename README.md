# Loadout Oracle

A Destiny 2 build synergy engine and loadout sharing tool. Pick your class and
build-around items and it ranks a curated build library by how well each build
matches, then generates a shareable Destiny Item Manager (DIM) loadout link you
can import in one click.

Live: https://loadout-oracle.onrender.com

## What it does
- Three-step wizard that ranks a curated build library against your picks. Class
  and the three build-around picks are hard filters; everything else soft-scores
  so the closest builds rank to the top.
- Generates shareable DIM loadout links through the Bungie.net and DIM APIs.
- Renders each super, aspect, fragment, ability, exotic, and legendary with its
  real Destiny icon, hotlinked from the Bungie manifest.
- Repaints the page to the selected element (Arc, Solar, Void, Stasis, Strand,
  Prismatic) for accent, glow, and glyph.

## Technical highlights
- Server-side OAuth 2.0 token chain: exchanges a Bungie refresh token for an
  access token, then a DIM bearer token, with cached tokens and graceful
  degradation when auth is not configured.
- Resolves the Destiny platform membership id via GetMembershipsForCurrentUser,
  which the DIM share endpoint requires.
- Matching pipeline that resolved 878 catalog items to Bungie manifest assets
  (853 by hash, 25 by name) with zero unmatched.
- Deployed on Render behind gunicorn with unattended token management.

## Stack
Python, Flask, gunicorn, Bungie.net API, Destiny Item Manager API, Render.

## Run locally
    pip install -r requirements.txt
    python app.py
    # open http://127.0.0.1:5000

## Production
    gunicorn wsgi:app

## Deploy on Render
1. Push this folder to a GitHub repo.
2. render.com -> New + -> Web Service -> connect the repo.
3. Runtime: Python. Build: pip install -r requirements.txt. Start: gunicorn wsgi:app.
4. Instance: Free. Branch: main. Create Web Service.

DIM sharing uses these environment variables (a one-time setup script mints the
refresh token): BUNGIE_OAUTH_CLIENT_ID, BUNGIE_OAUTH_CLIENT_SECRET,
BUNGIE_API_KEY, BUNGIE_REFRESH_TOKEN, BUNGIE_MEMBERSHIP_ID, DIM_API_KEY.

## Data
Build data lives in data/builds.json and data/options.json, both generated from
the Destiny 2 reference workbook. Fonts approximate Destiny's and load from
Google Fonts.

## License
MIT. See LICENSE.

## Disclaimer
Unofficial fan-made tool, not affiliated with, endorsed by, or sponsored by
Bungie. Destiny, Destiny 2, Bungie, and related images, icons, names, and game
data are property of Bungie. Game data and icons are displayed using Bungie API
and manifest data.