# UAE Credit Card Intelligence Agent

Answers one question: **given this person's actual spending, which UAE credit card — or pair of cards — is estimated to produce the highest net annual value?**

It is not a "best cards in the UAE" list. The recommendation moves with the spending profile. A heavy grocery spender, a heavy diner and a frequent traveller get different answers from the same database.

## The one architectural rule

```
Official issuer source → research → structured verified rule → database
                                                                  ↓
                                                  deterministic Python calculation
                                                                  ↓
                                                    LLM explains the finished result
```

An LLM never calculates rewards, fees, cashback, points, FX, caps, eligibility or net value. Python does. The LLM receives a completed calculation and writes prose about it, and the product works with the LLM switched off entirely.

## The web client

A single-page client is served by the API itself at `/` — no build step, no
bundler, no `node_modules`. It renders API output and nothing else: a test
asserts it performs no arithmetic on any money field and hardcodes no card
names or amounts. Every figure it shows can be traced to its official issuer
document through the evidence drawer.

Deployment (single container or split static host), environment variables,
Postgres, Firecrawl, secrets and GitHub setup are all in
[DEPLOYMENT.md](DEPLOYMENT.md).

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env

python scripts/seed_database.py       # create schema, import researched cards, emit provenance CSV
python scripts/validate_card_data.py  # data quality report
python scripts/run_demo.py            # five golden profiles + the acceptance profile

uvicorn app.main:app --reload                  # API on :8000, docs at /docs
streamlit run frontend/streamlit_app.py        # UI on :8501
pytest --cov=app                               # 119 tests
```

### Docker

```bash
docker compose up --build
```

Brings up PostgreSQL and the API, seeding the database on start. Run Streamlit alongside with `API_BASE_URL` pointing at the API, or locally against the same `DATABASE_URL`.

### Replit

1. Import the repository.
2. Add `DATABASE_URL`, `ENVIRONMENT` and optionally `LLM_API_KEY` as Replit Secrets — never commit them.
3. For PostgreSQL, use Replit's managed database and set `DATABASE_URL` to `postgresql+psycopg://...`. SQLite works without any database at all.
4. `pip install -r requirements.txt`
5. `python scripts/seed_database.py`
6. Run `uvicorn app.main:app --host 0.0.0.0 --port 8000` for the API.
7. Run `streamlit run frontend/streamlit_app.py --server.address 0.0.0.0 --server.port 8501` for the UI.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Status, cards loaded, calculation version |
| GET | `/api/v1/cards` | Card summaries |
| GET | `/api/v1/cards/{card_id}` | Full rules, fees, eligibility and every provenance record |
| POST | `/api/v1/recommend` | Full recommendation |

Every response carries `run_id`, `calculation_version` and `card_data_version`. The same profile against the same card-data version produces identical numbers; only LLM wording varies.

## Data discipline

This is the part that matters, and the part most easily faked.

- **No invented cards and no invented values.** There are no placeholder or synthetic cards. A test asserts this.
- **Field-level provenance.** Each financial field records its value, source URL, source tier, retrieval date, verification status and evidence. Exported to `data/card_sources.csv`.
- **Field-level verification.** A card is not discarded because one field is unverified. `annual_fee` can be VERIFIED while `fx_fee` is UNKNOWN.
- **UNKNOWN is never quietly zero.** An unverified FX fee is reported as unquantified, not as no cost — treating it as zero would overstate net value exactly where foreign spend matters.
- **Conditional criticality.** An unverified FX fee is critical for a profile with international spend and irrelevant without it. The engine grades data quality per profile, not per card.
- **Unmodellable terms are refused, not guessed.** A per-merchant cap cannot be applied to a category-level profile, so the affected card is carried with a stated limitation instead of a falsely precise number.
- **No monetary value on perks.** Lounge access and similar are listed separately, never converted to AED.
- **No points valuation without a defensible source.** `reward_value_assumptions` exists and is currently empty, so no points or miles card is converted to AED.

Evidence hierarchy: official issuer page (tier 1) → official issuer document (2) → programme partner (3) → regulator (4) → reputable secondary (5) → discovery only (6). Secondary sources are used only where research did not establish the field officially, and are flagged.


## Research layer (Firecrawl)

The card database is no longer hand-assembled. `app/services/research/` adds a
retrieval → extraction → verification → promotion pipeline.

```
research request → orchestrator → Firecrawl search → official URL filter
 → targeted scrape (HTML + PDF) → extraction → field-level verification
 → research database → promotion gate → production card database → calculation engine
```

Firecrawl retrieves. Claude proposes. Python decides. The calculation engine is
untouched by any of it.

### Why nothing can be fabricated

Every proposed value passes `verification.verify_candidate` before it is stored:

1. the quoted evidence must occur in the retrieved document
2. the claimed value must occur in the quoted evidence
3. `"up to 10%"` does not become `10%` unless the qualifying conditions were
   themselves verified
4. a non-official domain can never produce `VERIFIED_OFFICIAL`

Fail any check and the field becomes `UNKNOWN` with a recorded reason. There is
no code path that writes a value without passing this gate — including the LLM
path, whose invented quotes fail check 1.

Conflicts are resolved, not hidden: a newer effective date supersedes an older
document and the loser is kept as a recorded conflict; two current official
documents that disagree produce `CONFLICTING_SOURCES` for human review.

### Promotion

```
RESEARCHED → EXTRACTED → VALIDATED → VERIFIED → PROMOTED
```

One step at a time, no skipping. A card with any unverified critical field
(`reward_rate`, `reward_cap`, `min_monthly_spend`, `annual_fee`,
`annual_fee_waiver`, `fx_fee`, `eligibility_min_salary`,
`international_reward_rate`) is held at `VALIDATED` and never reaches
production. Rejected cards are kept with their blocking fields listed, so the
next run knows what to look for.

### Cost control

Search first, filter to official domains, scrape only survivors. Every response
is cached on disk by request hash. A document whose content hash is unchanged is
not re-extracted — its prior evidence is carried forward instead. Full-domain
crawling is opt-in and capped; the default path is search + targeted scrape.

### Change detection

`research_documents` stores content hash, retrieval time, effective date and the
previous hash. A changed document is flagged `needs_reverification` rather than
silently overwriting the card, and promotion appends a new card version instead
of destroying history.

### Research API

| Method | Path |
| --- | --- |
| GET | `/api/v1/research/targets` |
| POST | `/api/v1/research/card` |
| POST | `/api/v1/research/batch` |
| GET | `/api/v1/research/runs/{run_id}` |
| GET | `/api/v1/research/cards/{card_id}` |
| GET | `/api/v1/research/cards/{card_id}/evidence` |
| POST | `/api/v1/research/verify/{research_id}` |
| POST | `/api/v1/research/promote/{research_id}` |

The recommendation endpoints are unchanged, and a test asserts it.

### Configuration

`FIRECRAWL_API_KEY` enables live retrieval. Without it the pipeline runs in
replay mode against the on-disk cache and an uncached URL raises rather than
returning empty content that could be mistaken for "the page does not mention
this field". Targets live in `app/data/research_targets.json` — adding a bank or
card needs no code change.

Unit tests mock Firecrawl entirely; the one live test is skipped unless
`FIRECRAWL_API_KEY` is set.

## Current dataset

Four researched cards. Honest state:

| Card | Ranking status | Why |
| --- | --- | --- |
| ADCB 365 Cashback | Ranked | Every critical field on the official product page |
| Emirates Islamic Cashback Plus | Ranked | Tiered card; tier values and cap amount from secondary sources |
| Mashreq Cashback | Not ranked | Official cap is per-merchant and cannot be modelled from a category profile |
| RAKBANK World | Not ranked | Official page confirms a cap exists; the cap table was not established |

`data/uae_cards.json` carries a `research_queue` naming the next issuers and cards to ingest. That is the main outstanding work — see Limitations.

## Layout

```
app/
  api/        routes_health, routes_cards, routes_recommendations
  core/       config, structured JSON logging with request ids
  db/         models (normalized, versioned), schemas (Pydantic v2), database
  domain/     enums — shared vocabulary, no DB or API imports
  services/   eligibility, reward, fee, fx, ranking, strategy, comparison,
              insight, explanation, recommendation orchestrator
  data/       card_importer (research artifact → DB), card_validator
data/         uae_cards.json (canonical research output), card_sources.csv (generated)
scripts/      seed_database, validate_card_data, run_demo
tests/        59 tests
frontend/     streamlit_app.py
```

`data/uae_cards.json` is the canonical research artifact rather than a CSV: reward rules are nested and tiered, which a flat CSV cannot express without losing structure the engine depends on. `data/card_sources.csv` is generated from it at seed time for the flat provenance view.

## Calculation notes

- Caps are monthly, so the engine models one representative month and multiplies by twelve. This assumes evenly distributed spend and is reported as an assumption on every result.
- Cap order is fixed: per-category monthly cap, then card-level monthly cap, then annualise. When the card-level cap binds, the category breakdown is pro-rated so it still sums to the reported total.
- Tier selection uses **total** monthly card throughput, including international spend, because that is what issuers measure.
- Annual fee uses the year-two figure. A first-year waiver is not counted as ongoing value; spend-based waivers are applied when projected spend meets the threshold.
- The two-card optimizer allocates categories using each card's per-category value computed in the context of full spend, then re-scores the split with the real engine so gates and caps apply to the reduced spend each card sees. Degenerate allocations are always included, so the two-card answer can never score below the best single card.
- A second card is only recommended when the gain clears `MIN_INCREMENTAL_VALUE` (default AED 250).

## Security

No card numbers, CVVs, credentials, OTPs or payment details are collected, stored or requested anywhere in the system.

## Limitations

1. **Four cards, not the ten to fifteen targeted.** Research is the bottleneck, not the engine. Each card needs its official product page, fees page and terms cross-checked, and the environment running this build cannot reach bank domains directly.
2. **No FX fee is verified for any card.** Product pages defer to schedules of charges that were not retrieved. Every profile with international spend is graded LOW as a result, and the net values shown are optimistic by the unquantified FX cost.
3. **Two of four cards cannot be ranked precisely**, for the reasons in the table above.
4. **No points or miles card yet**, so the reward-valuation path is implemented and tested but unexercised by real data.
5. **Category mapping is approximate.** A user's "entertainment" is not necessarily the issuer's. Where a published rate is narrower than the category it is mapped to, the rule is marked PARTIALLY_VERIFIED with a note.
6. **Alembic is configured but unused**; V1 creates the schema directly.

## Smallest remaining work before production use

1. Verify the FX fee for every card from each issuer's schedule of charges. This single field currently caps data quality at LOW for any international spender.
2. Retrieve the RAKBANK World cap table and the Emirates Islamic cashback tier table from official sources, promoting both cards to full ranking.
3. Ingest the cards in `research_queue`, prioritising one points card to exercise `reward_value_assumptions`.
4. Resolve the two CONFLICTING annual fees against official documents.
5. Generate the initial Alembic migration before the first deployment.
