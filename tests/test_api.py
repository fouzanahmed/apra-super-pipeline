"""
Unit tests for api/main.py: the SQL-safety layer, ```sql fence stripping, and the
error-to-HTTP-status mapping of /query and /health.

No database or Claude access is needed: SQL generation and execution are monkeypatched.
"""
import psycopg2
import psycopg2.errors
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import main


@pytest.fixture
def client():
    return TestClient(main.app)


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("should not have been called")


# ── _validate_sql ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select fund_name from mart.fund_performance",
    "  \n  SELECT * FROM mart.member_flow",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "with t as (select 1) select * from t",
])
def test_validate_sql_accepts_select_and_with(sql):
    main._validate_sql(sql)  # does not raise


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE mart.fund_performance",
    "SELECT 1;",
    "SELECT ';'",
])
def test_validate_sql_rejects_semicolons(sql):
    with pytest.raises(HTTPException) as exc:
        main._validate_sql(sql)
    assert exc.value.status_code == 400
    assert "single SQL statement" in exc.value.detail


@pytest.mark.parametrize("sql", [
    "DROP TABLE mart.fund_performance",
    "DELETE FROM mart.member_flow",
    "UPDATE mart.fee_vs_return SET value_quadrant = 'x'",
    "INSERT INTO mart.member_flow VALUES (1)",
    "selectx FROM t",
    "-- comment\nSELECT 1",
    "",
])
def test_validate_sql_rejects_non_select(sql):
    with pytest.raises(HTTPException) as exc:
        main._validate_sql(sql)
    assert exc.value.status_code == 400
    assert "Only SELECT" in exc.value.detail


# ── _generate_sql fence stripping (Claude Code CLI path) ──────────────────

@pytest.mark.parametrize("model_output, expected", [
    ("```sql\nSELECT 1;\n```", "SELECT 1"),
    ("```SQL\nSELECT fund_name FROM mart.fund_performance\n```", "SELECT fund_name FROM mart.fund_performance"),
    ("```\nSELECT 2\n```", "SELECT 2"),
    ("Here you go:\n```sql\nSELECT 3\n```\nHope that helps.", "SELECT 3"),
    ("  SELECT 4 ;  \n", "SELECT 4"),
    ("SELECT 5", "SELECT 5"),
])
def test_generate_sql_strips_fences_and_trailing_semicolon(monkeypatch, model_output, expected):
    monkeypatch.setattr(main, "_has_api_key", lambda: False)
    monkeypatch.setattr(main, "_ask_claude_code", lambda question: model_output)
    assert main._generate_sql("any question") == expected


def test_generate_sql_passes_question_to_claude_code(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "_has_api_key", lambda: False)
    monkeypatch.setattr(main, "_ask_claude_code", lambda question: seen.append(question) or "SELECT 1")
    main._generate_sql("Which fund grew most?")
    assert seen == ["Which fund grew most?"]


# ── /health ───────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── /query ────────────────────────────────────────────────────────────────

def test_query_success(client, monkeypatch):
    monkeypatch.setattr(main, "_generate_sql", lambda q: "SELECT fund_name FROM mart.fund_performance")
    monkeypatch.setattr(main, "_run_sql", lambda sql: [{"fund_name": "A"}, {"fund_name": "B"}])
    resp = client.post("/query", json={"question": "List funds"})
    assert resp.status_code == 200
    assert resp.json() == {
        "question": "List funds",
        "sql": "SELECT fund_name FROM mart.fund_performance",
        "results": [{"fund_name": "A"}, {"fund_name": "B"}],
        "row_count": 2,
    }


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_query_empty_question_returns_400(client, monkeypatch, question):
    monkeypatch.setattr(main, "_generate_sql", _fail_if_called)
    monkeypatch.setattr(main, "_run_sql", _fail_if_called)
    resp = client.post("/query", json={"question": question})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "question must not be empty"


@pytest.mark.parametrize("generated", [
    "DROP TABLE mart.fund_performance",
    "SELECT 1; DELETE FROM mart.member_flow",
])
def test_query_unsafe_generated_sql_returns_400_without_running(client, monkeypatch, generated):
    monkeypatch.setattr(main, "_generate_sql", lambda q: generated)
    monkeypatch.setattr(main, "_run_sql", _fail_if_called)
    resp = client.post("/query", json={"question": "Drop everything"})
    assert resp.status_code == 400


def test_query_read_only_violation_returns_400(client, monkeypatch):
    def blocked(sql):
        raise psycopg2.errors.ReadOnlySqlTransaction("cannot execute UPDATE in a read-only transaction")

    monkeypatch.setattr(main, "_generate_sql", lambda q: "WITH x AS (UPDATE t SET a = 1 RETURNING a) SELECT * FROM x")
    monkeypatch.setattr(main, "_run_sql", blocked)
    resp = client.post("/query", json={"question": "Sneaky write"})
    assert resp.status_code == 400
    assert "blocked" in resp.json()["detail"]


def test_query_database_error_returns_500(client, monkeypatch):
    def broken(sql):
        raise psycopg2.OperationalError("relation \"mart.nope\" does not exist")

    monkeypatch.setattr(main, "_generate_sql", lambda q: "SELECT * FROM mart.nope")
    monkeypatch.setattr(main, "_run_sql", broken)
    resp = client.post("/query", json={"question": "Bad table"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "SQL execution failed" in detail
    assert "SELECT * FROM mart.nope" in detail
