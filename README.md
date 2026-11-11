# APRA Superannuation Analytics Platform

A batch pipeline over APRA's quarterly MySuper filings. It loads the regulator's
messy Excel workbooks into Postgres, models them with dbt into tested
value-for-money marts, and answers plain-English questions through a FastAPI
endpoint where Claude writes the SQL and Postgres enforces that it's read-only.

Personal project. Started April 2026, rebuilt and hardened September to October 2026.

---

## What's measured

| | |
|---|---|
| Clean MySuper rows / distinct funds | **589** / **55** |
| Period with reported figures | **Sep 2020 → Sep 2023** |
| Products in the latest quarter | **35** (10 best value, 8 worst value) |
| dbt | **4 models, 21 tests**, all passing |
| pytest (ingestion) | **15 tests**, all passing |
| Query API limits | read-only transaction, **10 s** timeout, **200**-row cap, one statement |

---

## Architecture

```
APRA Excel ─► Python (requests, pandas, openpyxl) ─► S3 raw archive (raw/YYYY-MM-DD/)
           ─► Postgres raw ─► dbt staging (typed view) ─► dbt marts (tested tables)
           ─► Power BI (read-only role)   +   FastAPI + Claude (text-to-SQL)

Airflow runs it weekly: download → S3 → load → dbt run → dbt test → Slack
GitHub Actions lints, tests and compiles every PR · Docker packages the API
```

| Model | Grain | What it holds |
|---|---|---|
| `staging/stg_apra_mysuper` | product × quarter | Cleaned, typed MySuper data |
| `mart/fund_performance` | product × quarter | Returns ranked within quarter and peer group, fee percentile |
| `mart/fee_vs_return` | product, latest quarter | Best / worst value quadrant against exact medians |
| `mart/member_flow` | product × quarter | Net member flows (empty, see status below) |

---

## The bugs worth reading about

**A join that failed silently.** The first full run finished with no errors and
fund assets **100% NULL across all 866 rows**. Three sheets were merged on
`(quarter, ABN)`, but one sheet's quarter keys came out as
`"2020-09-30 00:00:00"` and the other two, after a `groupby` re-inferred them as
dates, as `"2020-09-30"`. A left join doesn't complain about that, it just
returns NULLs. One `_clean_keys()` function now normalizes every key before any
grouping, and the merges use `validate="one_to_one"`. Matched rows went from
**0 to 589**.

**The wrong grain.** Some funds offer two or three MySuper products, but assets
were summed per fund and copied onto every product row. Joins are now at
`(quarter, ABN, product_name)`, and the dbt `unique` tests check that grain.

**The source changed underneath it.** From Dec 2023 every return and fee in the
file is blank: APRA moved the figures to a newer publication. Profiling by
period found it. The real coverage is documented, and `fee_vs_return`, which
picks the latest quarter, no longer comes back empty.

**Units.** APRA publishes `7.89` meaning 7.89%, and some number columns hold text
like "Refer to Explanatory Notes". Both are handled once, at ingestion:
`to_numeric(errors="coerce") / 100`.

**Reruns broke the views.** `to_sql(if_exists="replace")` drops the table, which
Postgres refuses once dbt views depend on it. Loads are now `TRUNCATE` + append
in one transaction, so Airflow retries are safe.

---

## Text-to-SQL, and two ways it went wrong

`POST /query` takes a question, Claude writes the SQL, Postgres runs it, and the
response includes the SQL so a person can check it.

**Confident but wrong.** "Which funds are the worst value?" returned 16 funds. Claude
had compared `return_5yr` against `median_return`, which is the median *1-year*
return, so the filter was always true. The fix was metadata, not a bigger model:
column descriptions written in dbt `schema.yml`, saved to Postgres as comments
with `persist_docs`, and read into the prompt from `information_schema`. The same
question now uses `value_quadrant = 'worst_value'` and returns the correct **8**.
Because the schema is read live, the prompt can't drift from the dbt models.

**A safety check that wasn't.** The original guard checked that the SQL started
with `SELECT`. This passed it:

```sql
WITH x AS (DELETE FROM raw.apra_mysuper RETURNING 1) SELECT count(*) FROM x
```

Safety now lives in the database: a read-only transaction, a 10-second statement
timeout, a 200-row cap and a single statement. The same query is blocked and all
589 rows are still there.

Without an `ANTHROPIC_API_KEY` the API falls back to the Claude Code CLI for
local development. The prompt goes over stdin and the CLI runs from a temp
folder, so it sees only the context it's given.

---

## Engineering details

- **Self-contained CI.** The PR check runs ruff, pytest and `dbt compile` against a
  throwaway Postgres service container, so it needs no cloud database or secrets.
  Every tool is pinned (ruff 0.16.9, dbt-core 1.12.5, dbt-postgres 1.11.0) after an
  unpinned linter and a dbt release candidate broke the build.
- **Least-privilege reporting role.** `powerbi_reader` gets `SELECT` through
  `ALTER DEFAULT PRIVILEGES`, so access survives dbt recreating tables. Writes
  are denied.
- **Docker.** The API image builds from the repo root, with a `.dockerignore` that
  keeps `.env` and the raw Excel files out.
- **Window functions.** `RANK` and `PERCENT_RANK` partitioned by quarter, exact
  medians from `percentile_cont`, and a `generate_schema_name` override so dbt
  writes to `mart` rather than `staging_mart`.

---

## Status

**Working and verified locally:** Excel → Postgres load, dbt models and tests,
pytest, the query API (real questions plus the safety tests), the Docker build,
the read-only reporting role, and the full CI job.

**Built, not run in this rebuild:** the S3 upload, the Airflow DAG, and the
PySpark version of the fund-level calculations (`spark/`).

**Open:**
- `member_flow` is empty. Member counts sit in a table covering different
  products; the next step is sourcing them from the Annual Bulletin.
- The Power BI report is built in Desktop (Import mode) but not published. The
  build guide is in [`docs/power_bi_guide.md`](docs/power_bi_guide.md).
- `dbt_test.yml` (on merge to `main`) still expects a cloud database; only the PR
  check is self-contained.
- The fund-level and annual-bulletin workbooks load only their cover sheet.

It originally ran on AWS (S3, RDS, Airflow on EC2). The database was torn down to
control cost and rebuilt locally in Docker; hosts are environment variables, so
moving back is a config change.

---

## Running it

```bash
pip install -r requirements-local.txt
cp .env.example .env                       # point POSTGRES_* at a local Postgres

python -m ingestion.download_apra          # fetch the workbooks
python -m ingestion.load_to_postgres --local

pip install -r requirements-dbt.txt
mkdir -p ~/.dbt && cp dbt_project/profiles.yml.example ~/.dbt/profiles.yml
cd dbt_project && dbt run && dbt test && cd ..

pip install -r requirements-api.txt
uvicorn api.main:app --reload              # http://localhost:8000/docs
```

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which funds are the worst value?"}'
```

`docker-compose up` starts Airflow and the API together.

Data comes from APRA's public
[quarterly superannuation statistics](https://www.apra.gov.au/quarterly-superannuation-statistics).
No API key is needed for the data itself.
