# Deployment handoff

Two supported shapes. Pick one.

**A — single container (recommended).** FastAPI serves the API and the web client from one process. One URL, no CORS, no separate frontend deploy. This is what `docker compose up` gives you.

**B — split.** Static host for `frontend/web/`, container host for the API. Needed only if you want a CDN in front of the UI. Requires `CORS_ALLOW_ORIGINS` and `window.API_BASE_URL`.

---

## 1. Local run

```bash
git clone <your-repo> && cd uae-credit-card-intelligence
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

python scripts/seed_database.py        # creates schema, imports researched cards
python scripts/validate_card_data.py   # data quality report; exits non-zero on errors
pytest -q                              # 119 passed, 1 skipped

uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`. The web client is served from `/`, the API from `/api/v1`, docs at `/docs`.

---

## 2. Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `DATABASE_URL` | prod | `sqlite:///./uae_cards.db` | Postgres: `postgresql+psycopg://user:pass@host:5432/uae_cards` |
| `ENVIRONMENT` | no | `local` | Surfaced on `/api/v1/health` |
| `CORS_ALLOW_ORIGINS` | split only | unset | Comma-separated exact origins. Leave unset for single-container — no CORS headers are emitted at all. Never `*`. |
| `LLM_API_KEY` | no | unset | Anthropic key. Enables prose explanations and LLM-assisted extraction. Without it the deterministic explanation is used and everything still works. |
| `LLM_MODEL` | no | `claude-sonnet-4-6` | |
| `FIRECRAWL_API_KEY` | research only | unset | Without it the research pipeline runs in replay mode against the disk cache. Not needed to serve recommendations. |
| `FIRECRAWL_BASE_URL` | no | `https://api.firecrawl.dev/v2` | |
| `FIRECRAWL_TIMEOUT` | no | `120` | Seconds. Raise for large PDFs. |
| `RESEARCH_CACHE_DIR` | no | `.research_cache` | Mount a volume in production so cache survives restarts — this is what stops you paying twice for the same page. |
| `RESEARCH_CACHE_TTL_HOURS` | no | `168` | |
| `MIN_INCREMENTAL_VALUE` | no | `250` | AED gain needed before a second card is recommended |
| `STALENESS_DAYS` | no | `180` | Card data older than this is reported STALE |

None of these have production-safe defaults for `DATABASE_URL`. Set it explicitly.

---

## 3. PostgreSQL

```bash
createdb uae_cards
export DATABASE_URL="postgresql+psycopg://postgres:<password>@localhost:5432/uae_cards"
python scripts/seed_database.py
```

Managed Postgres (Neon, Supabase, RDS, Railway) works unchanged. Two things to get right:

- The driver is **psycopg 3**, so the URL scheme is `postgresql+psycopg://`, not `postgresql://`. Providers hand you the latter; rewrite the scheme.
- Most managed providers require TLS: append `?sslmode=require`.

Schema is created by `scripts/seed_database.py` and on API startup. Before your first real deployment, generate the Alembic baseline (see `migrations/README.md`) so future schema changes are versioned. Card data changes never need a migration — they version themselves in `card_versions`.

### Re-seeding

`seed_database.py` is idempotent: it replaces each card by `card_id` and rewrites `data/card_sources.csv`. Safe to run on every deploy. Existing `recommendation_runs` are preserved, so past runs stay auditable.

---

## 4. Docker

```bash
docker compose up --build     # postgres + api, seeds on start, serves UI at :8000
```

`docker-compose.yml` brings up Postgres with a healthcheck and the API, which waits for it, seeds, then runs uvicorn. To pass secrets:

```bash
LLM_API_KEY=sk-... FIRECRAWL_API_KEY=fc-... docker compose up
```

For a single image on Fly/Render/Railway/Cloud Run:

- **Build command:** `docker build -t uae-cards .`
- **Start command:** `sh -c "python scripts/seed_database.py && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"`
- **Health check path:** `/api/v1/health` — returns `{"status":"ok"}` when cards are loaded and `{"status":"degraded"}` when the database is empty. Point your platform's health check at this and treat `degraded` as unhealthy; it means seeding failed.
- **Port:** honour the platform's `$PORT`.

Without Docker (Render/Railway native Python):

- **Build:** `pip install -r requirements.txt && python scripts/seed_database.py`
- **Start:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

---

## 5. Frontend deployment

**Single container:** nothing to do. `app/main.py` mounts `frontend/web/` at `/` after the API routers, so `/api/v1/*` can never be shadowed.

**Split deploy (Vercel / Netlify / S3+CloudFront):**

1. Deploy the API first and note its origin, e.g. `https://api.yourdomain.com`.
2. Tell the client where the API lives. Add this **before** the main `<script>` in `frontend/web/index.html`:

   ```html
   <script>window.API_BASE_URL = "https://api.yourdomain.com";</script>
   ```

3. Set on the API: `CORS_ALLOW_ORIGINS=https://cards.yourdomain.com`
4. Deploy `frontend/web/` as a static directory.
   - Vercel: **Output directory** `frontend/web`, **Build command** empty, **Framework preset** Other.
   - Netlify: **Publish directory** `frontend/web`, no build command.

There is no build step, no `node_modules`, and no bundler. One HTML file with inline CSS and JS, plus Google Fonts over CDN.

---

## 6. Firecrawl

Not needed to serve recommendations — only to research new cards.

```bash
export FIRECRAWL_API_KEY=fc-...
python scripts/live_firecrawl_check.py     # ONE card, all eight stages, exits non-zero on failure
```

Run that before any batch. It prints the real response shape at each stage, so if Firecrawl's API differs from the client's parsing assumptions you get a diagnostic instead of silently empty data. Only once it passes:

```bash
curl -X POST localhost:8000/api/v1/research/batch -H 'Content-Type: application/json' -d '{}'
```

Research writes to the research tables only. Nothing reaches the production card database until it passes the promotion gate:

```bash
curl -X POST localhost:8000/api/v1/research/verify/<research_id>
curl -X POST localhost:8000/api/v1/research/promote/<research_id>
```

In production, mount a persistent volume at `RESEARCH_CACHE_DIR`. Without it every restart re-fetches every page at full cost.

---

## 7. Secrets

- `.env` is in `.gitignore`. `.env.example` carries names only, never values.
- No secret is ever read at import time — everything goes through `app/core/config.py`, so a missing key degrades a feature rather than crashing boot.
- Set secrets in your platform's secret store (Fly `fly secrets set`, Render/Railway dashboard, GitHub Actions repository secrets). Never in `docker-compose.yml`, never in the image.
- The web client holds no secrets. It calls your API only; `LLM_API_KEY` and `FIRECRAWL_API_KEY` stay server-side.
- The app collects no card numbers, CVVs, credentials or OTPs, and there is nowhere to store them.

If a key leaks: rotate at the provider, update the platform secret, redeploy. There is no key material in the database.

---

## 8. GitHub setup

```bash
git init
git add -A
git commit -m "UAE Credit Card Intelligence: deterministic engine, research pipeline, web client"
git branch -M main
git remote add origin git@github.com:<you>/uae-credit-card-intelligence.git
git push -u origin main
```

Confirm before the first push:

```bash
git check-ignore -v .env        # must print a match
git ls-files | grep -E '\.env$|\.db$|research_cache'   # must print nothing
```

Minimal CI at `.github/workflows/ci.yml`:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: python scripts/validate_card_data.py
      - run: pytest -q --cov=app
```

The suite needs no Firecrawl key and makes no network calls; the one live test skips itself when `FIRECRAWL_API_KEY` is absent. Add the key as a repository secret only if you want that test to run in CI.

---

## 9. Production checklist

```bash
curl -s https://<host>/api/v1/health          # status ok, cards_loaded > 0
curl -s https://<host>/api/v1/cards | head    # real card ids
curl -s -X POST https://<host>/api/v1/recommend \
  -H 'Content-Type: application/json' \
  -d '{"emirate":"Dubai","salary_monthly":25000,
       "monthly_category_spend":{"groceries":3000,"dining":1500},
       "international_monthly_spend":1000,"reward_preference":"cashback",
       "annual_fee_tolerance":500,"pays_balance_in_full":true}'
```

Then open the site and confirm: results render, the evidence drawer opens with live issuer links, and the data-quality banner reflects the cards actually loaded.

- `DATABASE_URL` points at Postgres, not SQLite
- `CORS_ALLOW_ORIGINS` set only for a split deploy, and to exact origins
- Health check wired to `/api/v1/health`
- Research cache on a persistent volume if you will run research in production
- `/docs` is public by default; put it behind auth or disable it (`FastAPI(docs_url=None)`) if you'd rather not expose the schema

---

## 10. What runs where

| Concern | Where | Why |
| --- | --- | --- |
| Reward, fee, FX, ranking, two-card maths | Python, backend | Deterministic and testable. Same profile + same card-data version gives identical numbers. |
| Evidence verification | Python, backend | A value is only stored once the quoted evidence is found in the retrieved document. |
| Prose explanation | LLM, optional | Receives a finished calculation. Never computes. Falls back to a deterministic summary. |
| Rendering, formatting, layout | Browser | Renders API output. A test asserts it performs no arithmetic on any money field. |

The web client is a view. If it disappeared tomorrow the product would be unchanged; that separation is deliberate and is what makes the numbers trustworthy.
