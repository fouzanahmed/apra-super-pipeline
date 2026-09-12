# Agent Instructions for This Repository

You are operating autonomously, assigned exactly ONE backlog task. Follow these rules in order before making any change:

1. Read README.md fully before touching anything.
2. Inspect the repository structure (list directories, note the stack).
3. Run `git log --oneline -30` to see recent history — do not repeat work already done.
4. You will be given recent entries from this repo's automation history (prior categories/files touched). Never repeat the same category of change within 14 days unless the task explicitly says otherwise.
5. Check existing tests and CI config before adding new ones — extend, don't duplicate.
6. You have been assigned exactly one backlog item (given to you in the task prompt). Implement only that item. Do not expand scope, do not "also fix" unrelated things you notice — note them as follow-up ideas in your final structured output instead.
7. Preserve the project's actual purpose and existing architecture. Don't introduce a new framework/library/technology unless the task explicitly calls for it.
8. Keep the change small: 1-5 files, one logical commit's worth of work (a couple of commits' worth only if genuinely separate concerns, e.g. code + its test).
9. Test everything you change. Run the test suite, linter, and type checker (whichever exist in this repo) yourself before finishing. If you cannot get them passing, report failure — do not leave the working tree in a broken state.
10. NEVER fabricate results, metrics, benchmarks, or datasets. Never claim a feature exists if it doesn't. Any number you write into docs must be verifiable from real code/tests/data in this repo.
11. If the assigned task turns out to be already done, no longer applicable, or genuinely unsafe to do in one run, say so clearly in your structured output and stop — do not force an unrelated change to avoid "wasting the run."
12. Do not run `git add`, `git commit`, or `git push`, and do not open a PR. Leave your edits unstaged in the working tree — the orchestrator independently re-runs your tests, then handles staging, the commit, the push, and PR creation using the structured summary you provide at the end of your turn.

## Project-Specific Section

- **Goal:** Ingests APRA (Australian Prudential Regulation Authority) quarterly superannuation-fund publications, lands them in Postgres via S3, transforms them with dbt into fund-performance/fee/member-flow marts, and exposes the results through a FastAPI service and Power BI. Demonstrates an end-to-end batch data-engineering pipeline for a regulated-industry dataset.
- **Architecture:**
  - `ingestion/` — downloads APRA Excel publications, uploads raw files to S3, loads them into Postgres (`download_apra.py`, `upload_to_s3.py`, `load_to_postgres.py`)
  - `dbt_project/models/staging/` — cleans and types the raw tables (`stg_apra_mysuper.sql`)
  - `dbt_project/models/mart/` — fund_performance, fee_vs_return, member_flow marts consumed by Power BI
  - `api/main.py` — FastAPI service over the marts
  - `airflow/` — orchestrates the ingestion → dbt run on a schedule
  - `.github/workflows/pr_check.yml` — existing CI: ruff lint + dbt compile (no test execution yet)
- **Technologies:** Python, pandas, boto3, AWS S3, PostgreSQL, dbt, Airflow, FastAPI, GitHub Actions, Docker.
- **Recruiter skills demonstrated:** batch ETL design, dbt modeling with tests, orchestration (Airflow), API design over a warehouse, CI for a data pipeline.
- **Allowed improvement areas:** `ingestion/`, `api/`, `dbt_project/models/*/schema.yml` (tests only, not the transformation logic), `tests/` (new), `.github/workflows/pr_check.yml`, `README.md`.
- **Do not touch automatically:** `airflow/` DAG scheduling/connections, `.env`/secrets, `dbt_project/models/*.sql` transformation logic itself (only its `schema.yml` tests), anything under `data/` (source Excel files), Docker/infra credentials.
