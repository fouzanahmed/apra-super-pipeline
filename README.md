# Australian Superannuation Analytics Platform

End-to-end data pipeline on APRA's public regulatory data ($4.5T AUM industry) — automated ingestion, dbt transformations, AI-powered querying, and a live Power BI dashboard. Deployed on AWS with Docker and CI/CD.


## Stack

| Layer | Technology |
|-------|-----------|
| Ingestion | Python, requests, pandas, boto3 |
| Storage | AWS S3 (data lake) + AWS RDS PostgreSQL |
| Transformation | dbt (staging + mart models) |
| Orchestration | Apache Airflow on AWS EC2 |
| AI Query API | FastAPI + Claude API (NL-to-SQL) |
| Visualisation | Power BI DirectQuery → RDS |
| CI/CD | GitHub Actions (ruff + dbt compile + dbt test) |
| Containers | Docker + Docker Compose |
| Bonus | PySpark on EMR Serverless |

## Quick start (local)

```bash
git clone https://github.com/YOUR_USERNAME/apra-super-pipeline.git
cd apra-super-pipeline
cp .env.example .env          # fill in your credentials
docker-compose up             # spins up Airflow + API
```

Airflow UI: http://localhost:8080 (admin / admin)
Query API:  http://localhost:8000/docs

## Architecture

```
APRA website
    │  (Python download script)
    ▼
AWS S3  ──────────────────────────────────────────── PySpark (EMR)
s3://super-pipeline/raw/                                   │
    │  (boto3 + pandas)                                     │
    ▼                                                       │
AWS RDS PostgreSQL                                          │
  schema: raw                                               │
    │  (dbt)                                                │
    ▼                                                       ▼
  schema: staging          schema: mart (aggregated)
  stg_apra_mysuper   ──►  fund_performance
                           fee_vs_return
                           member_flow
                               │              │
                               ▼              ▼
                           Power BI      FastAPI NL-to-SQL
                           (DirectQuery)  /query endpoint
```

## Data sources

All public, no API key required:
- [APRA Quarterly MySuper Stats](https://www.apra.gov.au/quarterly-superannuation-statistics)
- [APRA Quarterly Fund-Level Stats](https://www.apra.gov.au/quarterly-fund-level-statistics)
- [APRA Annual Fund Bulletin](https://www.apra.gov.au/annual-superannuation-bulletin)

## dbt models

| Model | Type | Description |
|-------|------|-------------|
| `staging/stg_apra_mysuper` | view | Cleaned + typed raw data |
| `mart/fund_performance` | table | Returns ranked by peer group + fee percentile |
| `mart/fee_vs_return` | table | Value quadrant classification (best/worst value) |
| `mart/member_flow` | table | Net member inflows/outflows per quarter |

## AI query examples

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which fund had the best 5yr return under 0.5% fees?"}'

curl -X POST http://localhost:8000/query \
  -d '{"question": "Which 3 funds lost the most members last quarter?"}'
```

## GitHub Actions CI/CD

- **PR check** — `ruff` linter + `dbt compile` on every pull request
- **dbt test** — full `dbt run` + `dbt test` on merge to main
- **Docker build** — confirms the API image builds cleanly on every push

## Setup guide

See [SETUP.md](SETUP.md) for step-by-step AWS provisioning, RDS setup, and EC2 Airflow deployment.

