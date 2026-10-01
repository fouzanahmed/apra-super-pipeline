"""
Unit tests for the ingestion package: download_apra, load_to_postgres, upload_to_s3.

Focus is on edge cases the ingestion pipeline will hit against real APRA files:
malformed/empty sheets, header-row mismatches, and dtype inconsistencies that
would otherwise fail silently downstream.
"""
import io
import os
from datetime import date

import pandas as pd
import pytest
from openpyxl import Workbook

# load_to_postgres / upload_to_s3 read these env vars at import time.
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "testdb")
os.environ.setdefault("POSTGRES_USER", "testuser")
os.environ.setdefault("POSTGRES_PASSWORD", "testpass")

from ingestion.download_apra import _snake, clean_excel
from ingestion.load_to_postgres import _norm, _read_mysuper
from ingestion import upload_to_s3


def _make_workbook(sheets: dict) -> bytes:
    """Build an in-memory .xlsx with one sheet per (name -> rows) entry."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── _snake ────────────────────────────────────────────────────────────────

def test_snake_lowercases_and_joins_with_underscore():
    assert _snake("Fund Name") == "fund_name"


def test_snake_strips_punctuation():
    assert _snake("Fund ABN (2025)") == "fund_abn_2025"


def test_snake_collapses_repeated_whitespace():
    assert _snake("Total   Fees  \n  Paid") == "total_fees_paid"


# ── clean_excel ──────────────────────────────────────────────────────────

def test_clean_excel_raises_when_no_data_sheets(tmp_path):
    path = tmp_path / "cover_only.xlsx"
    path.write_bytes(_make_workbook({"Cover": [["Notice"], ["Nothing here"]]}))

    with pytest.raises(ValueError, match="No data sheets found"):
        clean_excel(path)


def test_clean_excel_finds_header_row_past_title_rows_and_drops_unnamed(tmp_path):
    rows = [
        ["My Report Title", None, None, None, None],
        [None, None, None, None, None],
        ["Fund Name", "ABN", "Return (%)", "Notes", None],
        ["Fund A", 123456, 5.5, "ok", "junk"],
        ["Fund B", 654321, 6.1, "ok", "junk"],
    ]
    path = tmp_path / "data.xlsx"
    path.write_bytes(_make_workbook({"Data": rows}))

    df = clean_excel(path)

    assert list(df.columns) == ["fund_name", "abn", "return", "notes"]
    assert len(df) == 2
    assert df.iloc[0]["fund_name"] == "Fund A"


def test_clean_excel_handles_header_with_no_data_rows(tmp_path):
    rows = [["Fund Name", "ABN", "Return (%)", "Notes"]]
    path = tmp_path / "empty.xlsx"
    path.write_bytes(_make_workbook({"Data": rows}))

    df = clean_excel(path)

    assert list(df.columns) == ["fund_name", "abn", "return", "notes"]
    assert len(df) == 0


def test_clean_excel_malformed_numeric_cell_becomes_silent_missing_value(tmp_path):
    # APRA sometimes marks a cell "N/A" instead of leaving it blank. pandas'
    # default NA-string handling swallows this silently as a missing value
    # rather than raising or preserving it as text -- worth documenting since
    # it means a malformed cell doesn't surface as an error anywhere upstream.
    rows = [
        ["Fund Name", "ABN", "Return (%)", "Extra"],
        ["Fund A", 123456, 5.5, "x"],
        ["Fund B", 654321, "N/A", "x"],
    ]
    path = tmp_path / "mixed.xlsx"
    path.write_bytes(_make_workbook({"Data": rows}))

    df = clean_excel(path)

    assert df["return"].dtype == "float64"
    assert pd.isna(df.iloc[1]["return"])


# ── load_to_postgres._norm ───────────────────────────────────────────────

def test_norm_strips_newlines_and_collapses_whitespace():
    assert _norm("Fund  ABN\n(unique)") == "Fund ABN (unique)"


# ── load_to_postgres._read_mysuper ──────────────────────────────────────

def _mysuper_workbook(abn_2a="12345678901", abn_1a="12345678901", abn_4="12345678901"):
    table_2a = [
        ["Table 2a"] + [None] * 10,
        [None] * 11,
        [None] * 11,
        [None] * 11,
        ["Period*", "MySuper product name", "Fund name", "Fund ABN", "Fund type",
         "One-year net return (rep member) - Annualised",
         "Three year net return (rep member) - Annualised",
         "Five year net return (rep member) - Annualised",
         "Investment fees (rep member)", "Administration fees and costs (rep member)",
         "Total fees and costs (rep member)"],
        ["%", "-", "-", "-", "-", "%", "%", "%", "%", "%", "%"],
        ["Sep-2025", "Balanced", "Test Fund", abn_2a, "Retail",
         0.05, 0.07, 0.06, 0.005, 0.003, 0.008],
    ]
    table_1a = [
        ["Table 1a", None, None],
        [None, None, None],
        [None, None, None],
        ["Period*", "Fund ABN", "Total assets"],
        ["Sep-2025", abn_1a, 1000.0],
    ]
    table_4 = [
        ["Table 4", None, None],
        [None, None, None],
        ["Period *", "Fund ABN", "Member accounts"],
        ["Sep-2025", abn_4, 500],
    ]
    return _make_workbook({"Table 2a": table_2a, "Table 1a": table_1a, "Table 4": table_4})


def test_read_mysuper_merges_all_three_tables_on_matching_keys():
    raw_bytes = _mysuper_workbook()

    df = _read_mysuper(raw_bytes)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["fund_name"] == "Test Fund"
    assert row["net_assets_m"] == 1000.0
    assert row["member_accounts"] == 500


def test_read_mysuper_merges_across_dtype_mismatched_abn_columns():
    # Table 1a/4 provide the ABN as an int while Table 2a provides it as a string,
    # mirroring how APRA sheets are sometimes typed inconsistently.
    raw_bytes = _mysuper_workbook(abn_2a="12345678901", abn_1a=12345678901, abn_4=12345678901)

    df = _read_mysuper(raw_bytes)

    assert len(df) == 1
    assert pd.notna(df.iloc[0]["net_assets_m"])
    assert df.iloc[0]["net_assets_m"] == 1000.0


def test_read_mysuper_silently_drops_match_when_units_row_poisons_abn_dtype():
    # Known limitation: if Table 2a's units row leaves the ABN cell truly blank
    # (rather than a placeholder like "-"), pandas infers the whole ABN column
    # as float64 because of the blank/NaN cell. After the code's `.astype(str)`
    # normalisation, "12345678901" becomes "12345678901.0", which no longer
    # matches Table 1a/4's clean string keys. The merge doesn't raise -- it just
    # silently produces NaN for net_assets_m / member_accounts.
    table_2a = [
        ["Table 2a"] + [None] * 10,
        [None] * 11,
        [None] * 11,
        [None] * 11,
        ["Period*", "MySuper product name", "Fund name", "Fund ABN", "Fund type",
         "One-year net return (rep member) - Annualised",
         "Three year net return (rep member) - Annualised",
         "Five year net return (rep member) - Annualised",
         "Investment fees (rep member)", "Administration fees and costs (rep member)",
         "Total fees and costs (rep member)"],
        ["%", None, None, None, None, "%", "%", "%", "%", "%", "%"],
        ["Sep-2025", "Balanced", "Test Fund", "12345678901", "Retail",
         0.05, 0.07, 0.06, 0.005, 0.003, 0.008],
    ]
    table_1a = [
        ["Table 1a", None, None],
        [None, None, None],
        [None, None, None],
        ["Period*", "Fund ABN", "Total assets"],
        ["Sep-2025", "12345678901", 1000.0],
    ]
    table_4 = [
        ["Table 4", None, None],
        [None, None, None],
        ["Period *", "Fund ABN", "Member accounts"],
        ["Sep-2025", "12345678901", 500],
    ]
    raw_bytes = _make_workbook({"Table 2a": table_2a, "Table 1a": table_1a, "Table 4": table_4})

    df = _read_mysuper(raw_bytes)

    assert len(df) == 1
    assert pd.isna(df.iloc[0]["net_assets_m"])
    assert pd.isna(df.iloc[0]["member_accounts"])


def test_read_mysuper_raises_on_header_mismatch_in_asset_table():
    # Table 1a is missing the "Fund ABN" column entirely (e.g. APRA renamed/shifted it) --
    # the hard-coded column selection should fail loudly rather than silently drop data.
    table_2a = [
        ["Table 2a"] + [None] * 10,
        [None] * 11,
        [None] * 11,
        [None] * 11,
        ["Period*", "MySuper product name", "Fund name", "Fund ABN", "Fund type",
         "One-year net return (rep member) - Annualised",
         "Three year net return (rep member) - Annualised",
         "Five year net return (rep member) - Annualised",
         "Investment fees (rep member)", "Administration fees and costs (rep member)",
         "Total fees and costs (rep member)"],
        ["%", None, None, None, None, "%", "%", "%", "%", "%", "%"],
        ["Sep-2025", "Balanced", "Test Fund", "12345678901", "Retail",
         0.05, 0.07, 0.06, 0.005, 0.003, 0.008],
    ]
    table_1a_bad_header = [
        ["Table 1a", None, None],
        [None, None, None],
        [None, None, None],
        ["Period*", "Fund Identifier", "Total assets"],
        ["Sep-2025", "12345678901", 1000.0],
    ]
    table_4 = [
        ["Table 4", None, None],
        [None, None, None],
        ["Period *", "Fund ABN", "Member accounts"],
        ["Sep-2025", "12345678901", 500],
    ]
    raw_bytes = _make_workbook({
        "Table 2a": table_2a, "Table 1a": table_1a_bad_header, "Table 4": table_4,
    })

    with pytest.raises(KeyError):
        _read_mysuper(raw_bytes)


# ── upload_to_s3.upload_raw_files ───────────────────────────────────────

class _FakeS3Client:
    def __init__(self):
        self.calls = []

    def upload_file(self, filename, bucket, key):
        self.calls.append((filename, bucket, key))


def test_upload_raw_files_uploads_only_xlsx_files(tmp_path, monkeypatch):
    fake_client = _FakeS3Client()
    monkeypatch.setattr(upload_to_s3, "RAW_DIR", tmp_path)
    monkeypatch.setattr(upload_to_s3.boto3, "client", lambda *a, **k: fake_client)

    (tmp_path / "apra_mysuper_quarterly.xlsx").write_bytes(b"fake xlsx bytes")
    (tmp_path / "notes.txt").write_text("not an excel file")

    uploaded = upload_to_s3.upload_raw_files(run_date=date(2026, 1, 15))

    assert uploaded == ["raw/2026-01-15/apra_mysuper_quarterly.xlsx"]
    assert len(fake_client.calls) == 1
    filename, bucket, key = fake_client.calls[0]
    assert bucket == "test-bucket"
    assert key == "raw/2026-01-15/apra_mysuper_quarterly.xlsx"


def test_upload_raw_files_returns_empty_list_when_no_files(tmp_path, monkeypatch):
    fake_client = _FakeS3Client()
    monkeypatch.setattr(upload_to_s3, "RAW_DIR", tmp_path)
    monkeypatch.setattr(upload_to_s3.boto3, "client", lambda *a, **k: fake_client)

    uploaded = upload_to_s3.upload_raw_files(run_date=date(2026, 1, 15))

    assert uploaded == []
    assert fake_client.calls == []
