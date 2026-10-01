"""
FastAPI NL-to-SQL service.
POST /query  {"question": "Which fund had the best 5yr return under 0.5% fees?"}
GET  /schema  -> the mart tables/columns the model is given
GET  /health

SQL is written by Claude through the API when ANTHROPIC_API_KEY is set; otherwise, for local
development, through the Claude Code CLI (`claude -p`) using your logged-in Claude account.
"""
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache

import anthropic
import psycopg2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="APRA Super Query API", version="1.1.0")

MODEL = "claude-sonnet-4-6"
MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 10_000

# Dataset-wide facts. Per-table and per-column meaning lives in dbt's mart/schema.yml.
DATA_NOTES = """
Returns and fees are stored as decimals: 0.0852 = 8.52%, 0.005 = 0.50%.
Data covers quarters from 2020-09-30 to 2023-09-30 (APRA MySuper statistics).
Prefer the precomputed columns (ranks, value_quadrant) over re-deriving them.
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


@lru_cache(maxsize=1)
def _claude() -> anthropic.Anthropic:
    # Created on first use so the API still starts (and /health works) without a key.
    return anthropic.Anthropic()


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@lru_cache(maxsize=1)
def _schema_context() -> str:
    """Describe the mart tables from the live database, so the prompt never drifts from dbt.

    Table/column descriptions come from dbt's schema.yml, which dbt writes into Postgres
    comments (persist_docs). Column names alone are ambiguous — e.g. median_return is the
    median ONE-year return — so the meaning has to travel with them.
    Cached: restart the API after `dbt run` to pick up changes.
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                select c.table_name,
                       obj_description(format('%I.%I', c.table_schema, c.table_name)::regclass, 'pg_class'),
                       c.column_name,
                       c.data_type,
                       col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, c.ordinal_position)
                from information_schema.columns c
                where c.table_schema = 'mart'
                order by c.table_name, c.ordinal_position
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        raise RuntimeError("No tables found in schema 'mart' — run `dbt run` first.")
    lines: list[str] = []
    current = None
    for table, table_desc, column, dtype, column_desc in rows:
        if table != current:
            current = table
            lines.append(f"\nmart.{table}" + (f": {' '.join(table_desc.split())}" if table_desc else ""))
        lines.append(f"  - {column} ({dtype})" + (f": {' '.join(column_desc.split())}" if column_desc else ""))
    return "\n".join(lines).strip()


def _system_prompt() -> str:
    return (
        "You write PostgreSQL queries over Australian superannuation (retirement fund) data "
        "for analysts. Given a plain-English question, reply with a single read-only SELECT "
        "statement and nothing else: no markdown fences, no explanation.\n\n"
        f"Tables:\n{_schema_context()}\n{DATA_NOTES}"
    )


def _ask_claude_code(question: str) -> str:
    """Local-dev fallback without an API key: Claude Code in print mode, using your Claude login.

    The whole prompt goes through stdin (Windows can cut multi-line command-line args), and it
    runs from a temp dir so Claude Code doesn't read this repo's files as extra context.
    """
    cli = shutil.which("claude")
    if not cli:
        raise HTTPException(
            status_code=503,
            detail="No Claude access: set ANTHROPIC_API_KEY in .env, or run the API locally "
                   "where the Claude Code CLI (`claude`) is installed and logged in.",
        )
    prompt = f"{_system_prompt()}\n\nQuestion: {question}"
    try:
        result = subprocess.run(
            [cli, "-p", "--model", "sonnet", "--tools", ""],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=180, cwd=tempfile.gettempdir(),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Claude Code CLI timed out.")
    if result.returncode != 0:
        raise HTTPException(status_code=502, detail=f"Claude Code CLI failed: {result.stderr.strip()[:500]}")
    return result.stdout


def _generate_sql(question: str) -> str:
    if _has_api_key():
        message = _claude().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_system_prompt(),
            messages=[{"role": "user", "content": question}],
        )
        text = "".join(b.text for b in message.content if b.type == "text").strip()
    else:
        text = _ask_claude_code(question).strip()
    # Tolerate a ```sql fence if the model adds one anyway.
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (fenced.group(1) if fenced else text).strip().rstrip(";").strip()


def _validate_sql(sql: str) -> None:
    if ";" in sql:
        raise HTTPException(status_code=400, detail="Only a single SQL statement is allowed.")
    if not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Only SELECT queries are allowed.")


def _run_sql(sql: str) -> list[dict]:
    conn = _db_conn()
    try:
        # The real safety net: Postgres itself rejects writes in a read-only
        # transaction, and runaway queries are cancelled by the timeout.
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur.execute(sql)
            cols = [desc[0] for desc in cur.description]
            rows = cur.fetchmany(MAX_ROWS)
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    try:
        sql = _generate_sql(req.question)
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=503, detail="Claude API key missing or invalid (set ANTHROPIC_API_KEY).")
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Claude API error: {e}")

    _validate_sql(sql)

    try:
        results = _run_sql(sql)
    except psycopg2.errors.ReadOnlySqlTransaction:
        raise HTTPException(status_code=400, detail=f"Query tried to modify data and was blocked.\n\nGenerated SQL:\n{sql}")
    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {e}\n\nGenerated SQL:\n{sql}")

    return QueryResponse(question=req.question, sql=sql, results=results, row_count=len(results))


@app.get("/schema")
def schema():
    return {"tables": _schema_context().splitlines(), "notes": DATA_NOTES.strip().splitlines()}


@app.get("/health")
def health():
    return {"status": "ok"}
