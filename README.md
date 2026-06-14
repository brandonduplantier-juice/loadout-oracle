# Loadout Oracle

A three-step wizard that ranks a curated build library by how well each build
matches your picks. Class and the three build-around picks are hard filters;
everything else soft-scores so the closest builds always rank to the top.

The page repaints to the selected element (Arc, Solar, Void, Stasis, Strand,
Prismatic) for accent, glow, and glyph.

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

Data lives in data/builds.json and data/options.json, both generated from the
Destiny 2 reference workbook. Fonts approximate Destiny's and are loaded from
Google Fonts. Fan tool, not affiliated with Bungie.
