"""
Load cleaned APRA data from S3 into PostgreSQL (AWS RDS).

apra_mysuper_quarterly.xlsx  ->  raw.apra_mysuper   (one row per quarter + fund ABN + MySuper product)
  Merges three sheets:
    Table 2a (header=4, skip units row): returns + fees per product/quarter
    Table 1a (header=3): total assets per product/quarter
    Table 4  (header=2): member accounts per product/quarter (summed across lifecycle stages).
                         Only published up to Sep 2022 and only for a subset of products,
                         so member_accounts is mostly NULL.
  APRA left all figures blank from Dec 2023 onward; those rows are dropped.
  Returns and fees are published as percentages (7.89 = 7.89%) and stored as decimals (0.0789).

apra_fund_level_quarterly.xlsx  ->  raw.apra_fund_level  (loaded as-is for now)
apra_annual_bulletin.xlsx       ->  raw.apra_annual_bulletin (loaded as-is for now)

Run:  python -m ingestion.load_to_postgres            # read today's files from S3
      python -m ingestion.load_to_postgres --local    # read from data/raw/ (no AWS needed)
"""
import argparse
import io
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()

S3_BUCKET = os.environ.get("S3_BUCKET")
RAW_DIR = Path("data/raw")
DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)

TABLES = {
    "apra_mysuper_quarterly.xlsx": "raw.apra_mysuper",
    "apra_fund_level_quarterly.xlsx": "raw.apra_fund_level",
    "apra_annual_bulletin.xlsx": "raw.apra_annual_bulletin",
}

KEYS = ["quarter_year", "abn", "product_name"]
PCT_COLS = ["return_1yr", "return_3yr", "return_5yr",
            "investment_fee_pct", "admin_fee_pct", "total_fee_pct"]


def _norm(col: str) -> str:
    """Normalize column name: strip newlines and collapse whitespace."""
    return re.sub(r"\s+", " ", str(col).strip().replace("\n", " "))


def _clean_keys(frame: pd.DataFrame) -> pd.DataFrame:
    """Give every sheet identical join keys, and drop units/SRF-reference/footnote rows.

    Keys must be normalised before any groupby: pandas re-infers a mixed text/date
    column as datetime after grouping, which changes its string form and silently
    breaks the join.
    """
    frame = frame.copy()
    frame["quarter_year"] = pd.to_datetime(frame["quarter_year"], errors="coerce").dt.date
    frame["abn"] = pd.to_numeric(frame["abn"], errors="coerce").astype("Int64").astype(str)
    frame["product_name"] = frame["product_name"].astype(str).str.strip()
    return frame[frame["quarter_year"].notna() & (frame["abn"] != "<NA>")]


def _read_mysuper(raw_bytes: bytes) -> pd.DataFrame:
    """Parse the MySuper quarterly file by merging Tables 2a + 1a + 4."""
    xf = pd.ExcelFile(io.BytesIO(raw_bytes))

    # ── Table 2a: returns and fees ─────────────────────────────────────────────
    t2a = pd.read_excel(xf, sheet_name="Table 2a", header=4, engine="openpyxl")
    t2a.columns = [_norm(c) for c in t2a.columns]
    t2a = t2a.rename(columns={
        "Period*":                                          "quarter_year",
        "MySuper product name":                            "product_name",
        "Fund name":                                       "fund_name",
        "Fund ABN":                                        "abn",
        "Fund type":                                       "fund_type",
        "One-year net return (rep member) - Annualised":   "return_1yr",
        "Three year net return (rep member) - Annualised": "return_3yr",
        "Five year net return (rep member) - Annualised":  "return_5yr",
        "Investment fees (rep member)":                    "investment_fee_pct",
        "Administration fees and costs (rep member)":      "admin_fee_pct",
        "Total fees and costs (rep member)":               "total_fee_pct",
    })
    keep_2a = KEYS + ["fund_name", "fund_type"] + PCT_COLS
    t2a = _clean_keys(t2a[[c for c in keep_2a if c in t2a.columns]])
    for col in PCT_COLS:
        # APRA publishes percentages (7.89 = 7.89%); store as decimals (0.0789).
        # Text such as "Refer to Explanatory Notes" becomes NULL.
        t2a[col] = pd.to_numeric(t2a[col], errors="coerce") / 100

    # ── Table 1a: total assets ─────────────────────────────────────────────────
    t1a = pd.read_excel(xf, sheet_name="Table 1a", header=3, engine="openpyxl")
    t1a.columns = [_norm(c) for c in t1a.columns]
    t1a = t1a.rename(columns={
        "Period*":              "quarter_year",
        "Fund ABN":             "abn",
        "MySuper product name": "product_name",
        "Total assets":         "net_assets_m",
    })
    t1a = _clean_keys(t1a[KEYS + ["net_assets_m"]])
    t1a["net_assets_m"] = pd.to_numeric(t1a["net_assets_m"], errors="coerce")
    t1a = t1a.groupby(KEYS, as_index=False)["net_assets_m"].sum(min_count=1)

    # ── Table 4: member accounts ───────────────────────────────────────────────
    t4 = pd.read_excel(xf, sheet_name="Table 4", header=2, engine="openpyxl")
    t4.columns = [_norm(c) for c in t4.columns]
    t4 = t4.rename(columns={
        "Period *":             "quarter_year",
        "Fund ABN":             "abn",
        "MySuper product name": "product_name",
        "Member accounts":      "member_accounts",
    })
    t4 = _clean_keys(t4[KEYS + ["member_accounts"]])
    t4["member_accounts"] = pd.to_numeric(t4["member_accounts"], errors="coerce")
    # one row per lifecycle stage — sum to product level
    t4 = t4.groupby(KEYS, as_index=False)["member_accounts"].sum(min_count=1)

    # ── merge ──────────────────────────────────────────────────────────────────
    df = t2a.merge(t1a, on=KEYS, how="left", validate="one_to_one")
    df = df.merge(t4,  on=KEYS, how="left", validate="one_to_one")
    df["member_accounts"] = df["member_accounts"].astype("Int64")
    # From Dec 2023 APRA still lists products in this file but leaves every figure blank
    # (the stats moved to the fund-level publication). Drop rows with nothing reported.
    return df.dropna(subset=PCT_COLS + ["net_assets_m", "member_accounts"], how="all")


def _read_raw_bytes(run_date: date, filename: str, local: bool) -> bytes:
    if local:
        return (RAW_DIR / filename).read_bytes()
    s3 = boto3.client("s3")
    key = f"raw/{run_date.isoformat()}/{filename}"
    return s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()


def _read_source(run_date: date, filename: str, local: bool = False) -> pd.DataFrame:
    raw_bytes = _read_raw_bytes(run_date, filename, local)

    if filename == "apra_mysuper_quarterly.xlsx":
        return _read_mysuper(raw_bytes)

    # other files: load first sheet as-is (used for future dbt models)
    df = pd.read_excel(io.BytesIO(raw_bytes), engine="openpyxl")
    df.columns = [_norm(c) for c in df.columns]
    return df


def load_all(run_date: date | None = None, local: bool = False) -> None:
    run_date = run_date or datetime.now(timezone.utc).date()  # UTC so upload and load agree on any machine
    engine = create_engine(DB_URL)

    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS raw"))

    for filename, table in TABLES.items():
        print(f"Loading {filename} -> {table}")
        df = _read_source(run_date, filename, local)
        df["_loaded_at"] = pd.Timestamp.utcnow()
        df["_run_date"] = run_date
        schema, tbl = table.split(".")
        # Truncate + append in one transaction instead of if_exists="replace":
        # "replace" DROPs the table, which Postgres refuses once dbt views depend on it.
        # Readers keep seeing the previous load until this commits, so reruns are safe.
        with engine.begin() as conn:
            if inspect(conn).has_table(tbl, schema=schema):
                conn.execute(text(f"TRUNCATE TABLE {table}"))
            df.to_sql(
                tbl, conn, schema=schema,
                if_exists="append", index=False,
                method="multi", chunksize=5000,
            )
        print(f"  Loaded {len(df)} rows into {table}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", action="store_true", help="read Excel files from data/raw/ instead of S3")
    load_all(local=parser.parse_args().local)
