"""
FastAPI NL-to-SQL service.
POST /query  {"question": "Which fund had the best 5yr return under 0.5% fees?"}
"""
import os
import json
import anthropic
import psycopg2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="APRA Super Query API", version="1.0.0")

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SCHEMA_CONTEXT = """
Tables in the PostgreSQL database (schema: mart):

mart.fund_performance
  fund_name TEXT, abn TEXT, fund_type TEXT, quarter_date DATE,
  return_1yr NUMERIC, return_3yr NUMERIC, return_5yr NUMERIC, return_7yr NUMERIC, return_10yr NUMERIC,
  total_fee_pct NUMERIC, net_assets_m NUMERIC, member_accounts BIGINT,
  rank_5yr_in_type INT, rank_5yr_overall INT, fee_percentile NUMERIC, total_funds_that_quarter INT

mart.fee_vs_return
  fund_name TEXT, abn TEXT, fund_type TEXT, quarter_date DATE,
  total_fee_pct NUMERIC, return_1yr NUMERIC, return_5yr NUMERIC, net_assets_m NUMERIC,
  value_quadrant TEXT  -- 'best_value' | 'expensive_performer' | 'cheap_underperformer' | 'worst_value'

mart.member_flow
  fund_name TEXT, abn TEXT, fund_type TEXT, quarter_date DATE,
  member_accounts BIGINT, net_assets_m NUMERIC,
  member_net_change BIGINT, member_growth_pct NUMERIC,
  rolling_annual_member_change BIGINT

Returns are stored as decimals: 0.0852 = 8.52%.
Fees are stored as decimals: 0.005 = 0.50%.
"""

SYSTEM_PROMPT = f"""You are a SQL expert helping analysts query Australian superannuation (retirement fund) data.
Given a plain English question, write a single PostgreSQL SELECT query. Return ONLY valid SQL — no markdown fences, no explanation.

{SCHEMA_CONTEXT}
"""


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    question: str
    sql: str
    results: list[dict]
    row_count: int


def _db_conn():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _generate_sql(question: str) -> str:
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    return message.content[0].text.strip()


def _run_sql(sql: str) -> list[dict]:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchmany(200)  # cap at 200 rows
    return [dict(zip(cols, row)) for row in rows]


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    sql = _generate_sql(req.question)

    # Reject any non-SELECT statements for safety
    if not sql.lower().lstrip().startswith("select"):
        raise HTTPException(status_code=400, detail="Only SELECT queries are allowed.")

    try:
        results = _run_sql(sql)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {e}\n\nGenerated SQL:\n{sql}")

    return QueryResponse(question=req.question, sql=sql, results=results, row_count=len(results))


@app.get("/health")
def health():
    return {"status": "ok"}
