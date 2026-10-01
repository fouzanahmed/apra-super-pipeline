# Interview prep: APRA Superannuation Pipeline

What I built, what went wrong, how I found and fixed it, and what I'd say about it.
Every number here comes from running the pipeline locally (Sep–Oct 2026 rebuild).

---

## 1. The 30-second pitch

> "Australian super funds hold about $4.5 trillion, and the regulator, APRA, publishes each fund's
> returns and fees every quarter, but only as messy Excel files. I built a batch pipeline that
> ingests those files into Postgres, models them with dbt into value-for-money and performance
> marts with data tests, and serves them two ways: a Power BI dashboard and a FastAPI endpoint
> where you ask questions in plain English and Claude writes the SQL. Most of the interesting
> work was data quality: I found a silent join bug that left half the dataset empty, and a case
> where the AI gave confident but wrong answers because a column name was ambiguous."

---

## 2. Architecture, and why each piece exists

```
APRA Excel ─► Python (requests, pandas) ─► S3 raw archive ─► Postgres raw
          ─► dbt staging (clean, typed view) ─► dbt marts (tables, tested)
          ─► Power BI dashboard   +   FastAPI + Claude (text-to-SQL)
Airflow schedules it weekly · GitHub Actions lints and compiles · Docker packages it
```

| Tool | Job in this project | "Why not just…?" answer |
|---|---|---|
| **pandas + openpyxl** | Parse multi-sheet Excel with header rows, unit rows, footnotes | The Excel layout is irregular; pandas gives fine control over headers and joins |
| **S3** | Raw archive by run date (`raw/YYYY-MM-DD/`) | I can reprocess any past run without re-downloading. APRA revises and moves files. |
| **Postgres** | Warehouse with `raw` → `staging` → `mart` layers | Small data (hundreds of rows); a warehouse like Snowflake would be overkill |
| **dbt** | SQL transformations, dependency order (`ref()`), data tests, documentation | Version-controlled, tested SQL that analysts can read; tests catch regressions |
| **Airflow** | Weekly DAG: download → S3 → load → dbt run → dbt test → Slack | Retries, scheduling, alerting, visibility of each step |
| **FastAPI** | `/query` endpoint for plain-English questions | Lightweight, automatic `/docs` page, typed request/response models |
| **Claude** | Turns the question into SQL, grounded in the live schema | Not a chatbot: it only writes SQL, and Postgres runs it read-only |
| **Power BI** | 3-page dashboard (rankings, fee vs return, trends) | What business users actually use |
| **Docker** | Local Postgres; API image | Reproducible; no cloud bill while developing |

---

## 3. Mistakes and bugs: my best interview stories

Format for each: **symptom → root cause → fix → lesson.** Pick 2–3 for "tell me about a hard problem".

### 3.1 The silent join failure ⭐ (best story)
- **Symptom:** The pipeline ran with no errors, but `net_assets_m` and `member_accounts` were **100% NULL** (0 of 866 rows).
- **Root cause:** I merged 3 Excel sheets on `(quarter, ABN)`. One sheet's quarter column held mixed text and dates, so converting it to text gave `"2020-09-30 00:00:00"`. The other two sheets went through `groupby` first, and pandas **re-inferred the column as datetime**, which turned into `"2020-09-30"`. The strings never matched, and a left join doesn't complain about that; it just returns NULLs.
- **Fix:** One `_clean_keys()` function normalises every sheet's keys (real `date`, integer ABN, trimmed product name) **before** any groupby. I also added `validate="one_to_one"` to the merges. Assets went from 0 to 589 matched rows.
- **Lesson:** "It ran" isn't "it's right". After every join, check the NULL rate. Normalise keys once, early, in one place.

### 3.2 Wrong grain (double counting)
- **Symptom:** Found while debugging 3.1. Some funds offer 2–3 MySuper products, but assets were summed per **fund** and then copied onto every product row.
- **Fix:** Join on `(quarter, ABN, product_name)`, the true grain of the source table.
- **Lesson:** Always state the grain of a table: "one row per ___". I later added `product_name` to the marts, because HESTA's two products (HESTA MySuper, $56.9bn; HESTA for Mercy, $1.3bn) looked like duplicate rows and would have merged into one bar in Power BI.

### 3.3 Units: percent vs decimal
- APRA publishes `7.89` meaning 7.89%, but the dbt models and API assumed `0.0789`. Some number columns also contained text like "Refer to Explanatory Notes".
- **Fix:** `pd.to_numeric(errors="coerce") / 100` at ingestion, documented in one place.
- **Lesson:** Units are part of the schema. Document them, and pick one convention at the boundary.

### 3.4 The source changed underneath me
- **Symptom:** From Dec 2023 onward, **every** return and fee was blank.
- **Root cause:** Not my code. APRA still lists the products in this file but moved the figures to a newer publication. So the data really covers **Sep 2020 – Sep 2023**.
- **Knock-on bug:** `fee_vs_return` picks the latest quarter. The latest quarter now held only blanks, so the mart would have been **empty**.
- **Fix:** Drop rows where nothing was reported, with a comment explaining why.
- **Lesson:** Profile the data by period, not only overall. Regulators change formats; say "the source changed" rather than hide it.

### 3.5 A limitation I chose to report honestly
- Member counts only exist in a table covering different products (1 product name in common), so `member_flow` is **empty**.
- I kept the mart and documented it rather than invent data. Next step: source members from the Annual Bulletin.
- **Lesson:** Interviewers respect "here's what the data can't tell you" more than a dashboard built on nothing.

### 3.6 Reruns crashed: pipelines must be idempotent
- `pandas.to_sql(if_exists="replace")` runs `DROP TABLE`. Once dbt views depended on the raw table, Postgres refused, so **the second run failed**.
- **Fix:** Run `TRUNCATE` + append in **one transaction**. Readers see the old data until commit, and reruns are safe.
- **Lesson:** Design every load to be safe to rerun (idempotent). Airflow retries will rerun it.

### 3.7 dbt schema naming surprise
- My tables landed in `staging_mart` instead of `mart`. dbt's default joins the target schema and the custom schema into one name.
- **Fix:** A 5-line `generate_schema_name` macro override.
- **Lesson:** Know your tool's defaults; this one catches most dbt beginners.

### 3.8 Deleted the cloud database mid-project
- I deleted my RDS instance to stop the bill, then forgot the project's state.
- **Recovery:** Postgres 16 in Docker locally. `.env` and `profiles.yml` read from environment variables, so switching between local and RDS is a config change, not a code change. I also found the dbt profile had the RDS host **hard-coded**, and fixed it.
- **Lesson:** Develop locally, deploy to cloud. Never hard-code hosts; environment variables are the switch.

### 3.9 The AI prompt described columns that didn't exist
- The API's hand-written schema listed `return_7yr` and `return_10yr`, which never existed. Claude would write SQL that fails.
- **Fix:** The API reads the schema **from the database** (`information_schema`), so it can't drift from dbt.

### 3.10 A SQL safety check that looked safe but wasn't ⭐
- The original check was "does the SQL start with SELECT?". I tested this:
  `WITH x AS (DELETE FROM raw.apra_mysuper RETURNING 1) SELECT count(*) FROM x`
  It **passed the text check**.
- **Fix:** Safety moved into the database: a **read-only transaction**, a 10-second statement timeout, a 200-row cap, and single statements only. The same attack now returns "blocked", and all 589 rows are still there.
- **Lesson:** Don't secure SQL with string matching; use the database's own permissions. Defence in depth.

### 3.11 Confident but wrong AI answers ⭐ (text-to-SQL story)
- **Symptom:** "worst value" returned 16 funds, and 8 of them were labelled `expensive_performer` by my own model.
- **Root cause:** Claude compared `return_5yr` with `median_return`, but `median_return` is the median **1-year** return (8.93%). Every 5-year return (about 4.5–6.4%) is below that, so the condition was always true and the query became "every fund with above-median fees".
- **Fix:** Wrote column descriptions in dbt `schema.yml` → `persist_docs` saves them as Postgres comments → the API reads them into the prompt. The same question now uses `WHERE value_quadrant = 'worst_value'` and returns exactly the right **8** funds.
- **Lessons:**
  - Text-to-SQL fails *silently* on ambiguous names. The fix is **metadata**, not a bigger model.
  - One source of truth: the meaning lives next to the SQL that computes it, and dbt docs, Power BI and the AI all read it.
  - The API returns the generated SQL so a person can check it.

### 3.12 Docker build context, and keeping secrets out of it
- The Dockerfile did `COPY ../requirements-api.txt`, but Docker can't read outside the build context, so **the image never built** (and CI would fail).
- **Fix:** Build from the repo root, and add a `.dockerignore` so `.env` and the Excel files never enter the build.

### 3.13 Running Claude locally without an API key
- The API falls back to the Claude Code CLI (`claude -p`) using my login. Two bugs showed up:
  1. Windows cut the multi-line system prompt at the first line break when passed as an argument. **Fix:** send the whole prompt through stdin.
  2. Run inside the repo, the CLI read project files and used columns I hadn't given it. **Fix:** run it from a temp folder.
- **Lesson:** Test that the model sees *only* the context you intend.

### 3.14 Access for dashboards
- I created a read-only `powerbi_reader` role. Plain `GRANT SELECT` would vanish, because dbt drops and recreates tables on every run. So I used `ALTER DEFAULT PRIVILEGES`. I verified that reads work and a `DELETE` gets "permission denied".
- **Lesson:** Least privilege, and know how your tool (dbt) recreates objects.

### 3.15 CI failed on code that passed locally: unpinned dependencies ⭐
- **Symptom:** The PR's lint job failed with 9 errors, including in files I never touched.
- **Root cause:** CI ran `pip install ruff` (latest, 0.16.9), but locally I had 0.5.0, and newer ruff checks more rules by default. The same thing was waiting in dbt: pinning only `dbt-postgres` let pip pick a **release candidate** of `dbt-core` (2.0.0rc8).
- **Fix:** Pin every tool (ruff 0.16.9, dbt-core 1.12.5, dbt-postgres 1.11.0). Resolving them together exposed a hidden conflict (dbt needs `python-dotenv>=1.2`), which I fixed too. I rehearsed the whole CI job in a clean virtual environment before pushing.
- **Also:** CI's `dbt compile` pointed at my deleted RDS. It now uses a throwaway Postgres **service container**, so CI needs no cloud database and no secrets.
- **Lesson:** "Works on my machine" is usually a version difference. Pin versions, and make CI self-contained.

### 3.16 A merge silently undid my work
- GitHub's "Update branch" merged `main` into my PR, and the conflict resolution kept `main`'s side of `schema.yml`. That **deleted the column descriptions** that fixed the AI's "worst value" bug, with no error anywhere.
- **Fix:** I reviewed the merge diff file by file and restored the descriptions alongside the new tests.
- **Lesson:** Always read the diff after a merge, especially for config and metadata files, where nothing crashes when content disappears.

### 3.17 Tests that locked in a bug, and tests with the wrong grain
- A unit test from an earlier PR asserted the join **returns NULL** ("known limitation"). It encoded bug 3.1 as expected behaviour. After my fix, I flipped it into a regression test that asserts the match succeeds.
- New dbt `unique` tests assumed one row per fund per quarter. The real grain is per **product**: 23 duplicates in `fund_performance` and 1 (HESTA) in `fee_vs_return`. I corrected them to `abn + product_name + quarter_date`. dbt now runs 21 tests, all passing.
- **Lesson:** A test is only as right as its assumption about the grain. When a test documents a bug, fix the bug and flip the test.

### 3.18 Analytical caveats I'd raise myself
- **1-year window is noisy:** Hostplus is `worst_value` on 1-year numbers despite a top 5-year return (6.38%).
- **Ties at the median:** Rei Super's fee is *exactly* the 0.24% median, and OneSuper sits exactly at both medians. `<=` vs `<` flips their labels. With only 35 products, the labels are fragile at the edges.
- **Simple vs asset-weighted averages:** a $0.2bn fund shouldn't count the same as a $200bn fund for "the average fee". Hence the `Asset-weighted Fee` measure in Power BI.

---

## 4. SQL you must be able to explain

| Concept | Where I used it | One-liner |
|---|---|---|
| `RANK()` | `rank_5yr_overall` | Ties share a rank and leave gaps. Mercer and Goldman Sachs both rank **3**, and the next fund is **5**. |
| `DENSE_RANK()` / `ROW_NUMBER()` | (not used; know the difference) | Dense ranks ties without gaps (3, 3, 4); row_number never ties (3, 4, 5) |
| `PARTITION BY` | rank within `quarter_date, fund_type` | Ranks restart for each quarter and peer group |
| `PERCENT_RANK()` | `fee_percentile` | 0 = cheapest, 1 = most expensive, within the quarter |
| `percentile_cont(0.5)` | medians in `fee_vs_return` | Exact median, interpolated between values if needed |
| `LAG()` | `member_flow` | Previous quarter's value for the same fund |
| `ROWS BETWEEN 3 PRECEDING AND CURRENT ROW` | rolling annual change | A 4-quarter moving window |
| `CROSS JOIN` a one-row CTE | attach medians to every row | Clean way to compare each row with an overall statistic |

---

## 5. dbt and Power BI concepts

- **Staging views vs mart tables:** staging is cheap and always current; marts are stored as tables for fast dashboard and API reads.
- **dbt tests:** 21 passing: `not_null`, `accepted_values`, `relationships` (marts → staging), and `unique` on the grain (`abn + product_name + quarter_date`).
- **`persist_docs`:** writes schema.yml descriptions into the database as comments.
- **Import vs DirectQuery:** Import copies the data into the report (fast, offline). DirectQuery asks the database on every click (always live, but slower and puts load on the database). I used Import locally; DirectQuery would make sense against RDS.
- **Calculated column vs measure:** a column is computed per row at load time (`Fund Label`); a measure is computed for whatever is currently filtered (`Avg 5yr Return`).

---

## 6. Numbers I can quote (all verified)

| Fact | Value |
|---|---|
| Clean MySuper rows / distinct funds (ABNs) | 589 / 55 |
| Period with reported figures | Sep 2020 – Sep 2023 |
| `fund_performance` rows | 575 |
| Products in the latest quarter (`fee_vs_return`) | 35 (10 best value, 8 worst value) |
| Median fee / median 1-year return, Sep 2023 | 0.24% / 8.93% |
| Top 5-year return, Sep 2023 | Meat Industry Employees Super 6.39%, Hostplus 6.38% |
| AustralianSuper average total fee | 0.225% (2020) → 0.183% (2023) |
| dbt | 4 models, 21 tests passing |
| pytest (ingestion) | 15 tests passing |

---

## 7. Honest status (don't overclaim)

**Working and verified locally:** Excel → Postgres load, dbt models and tests, pytest unit tests, the query API (tested with real questions, plus the safety tests), the Docker image build, the read-only Power BI user, and the full CI job rehearsed in a clean environment.

**Built, but not run in this rebuild:** the S3 upload path, the Airflow DAG, and the PySpark script.

**Not done yet:**
- Power BI report (guide written in `docs/power_bi_guide.md`).
- Member-flow data.
- The `dbt_test.yml` workflow (on merge to `main`) still points at the deleted RDS; only the PR check is self-contained so far.
- The Airflow image lacks dbt.
- Spark reads parquet that nothing writes.
- The fund-level and annual-bulletin files currently load only their Cover sheet.
- RDS/EC2 redeploy.

If asked "is it in production?": *"It ran on AWS originally. I tore the database down to control cost and rebuilt it locally with Docker. Moving it back is a config change: the hosts are environment variables."*

---

## 8. Likely questions, short answers

- **Why S3 before Postgres?** An immutable raw archive: I can replay history and debug parsing without re-downloading.
- **How do you make loads idempotent?** Truncate + insert in one transaction per run. Next step: upsert on the grain key.
- **How do you know the data is right?** dbt tests, NULL-rate checks after joins, and profiling by period (that's how I found 3.1 and 3.4).
- **How do you stop the LLM from damaging data?** It never has write access: a read-only transaction, timeout, row cap, single statement. Plus I return the SQL for review.
- **What if the AI's SQL is wrong but runs?** That's the real risk (3.11). The mitigations are rich metadata, precomputed business logic in dbt, and showing the SQL. Next: an eval set of questions with known answers.
- **What would change at 100× the data?** Parquet in S3, incremental dbt models, partitioning by quarter, Spark/EMR for heavy transforms, and DirectQuery or aggregations in Power BI.
- **What would you do differently?** Start with a data profile per sheet and period, write the grain and units down first, and add a `unique` test on the grain from day one.
