"""
Load cleaned APRA data from S3 into PostgreSQL (AWS RDS).

apra_mysuper_quarterly.xlsx  ->  raw.apra_mysuper
  Merges three sheets:
    Table 2a (header=4, skip units row): returns + fees per fund/quarter
    Table 1a (header=3): total assets per fund/quarter
    Table 4  (header=2): member accounts per fund/quarter (aggregated across lifecycle stages)

apra_fund_level_quarterly.xlsx  ->  raw.apra_fund_level  (loaded as-is for now)
apra_annual_bulletin.xlsx       ->  raw.apra_annual_bulletin (loaded as-is for now)
"""
import os
import io
import re
import boto3
import pandas as pd
from datetime import date
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

S3_BUCKET = os.environ["S3_BUCKET"]
DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)

TABLES = {
    "apra_mysuper_quarterly.xlsx": "raw.apra_mysuper",
    "apra_fund_level_quarterly.xlsx": "raw.apra_fund_level",
    "apra_annual_bulletin.xlsx": "raw.apra_annual_bulletin",
}


def _norm(col: str) -> str:
    """Normalize column name: strip newlines and collapse whitespace."""
    return re.sub(r"\s+", " ", str(col).strip().replace("\n", " "))


def _read_mysuper(raw_bytes: bytes) -> pd.DataFrame:
    """Parse the MySuper quarterly file by merging Tables 2a + 1a + 4."""
    xf = pd.ExcelFile(io.BytesIO(raw_bytes))

    # ── Table 2a: returns and fees ─────────────────────────────────────────────
    t2a = pd.read_excel(xf, sheet_name="Table 2a", header=4, engine="openpyxl")
    t2a = t2a.iloc[1:].reset_index(drop=True)   # drop units row (%, $, etc.)
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
    keep_2a = ["quarter_year", "product_name", "fund_name", "abn", "fund_type",
               "return_1yr", "return_3yr", "return_5yr",
               "investment_fee_pct", "admin_fee_pct", "total_fee_pct"]
    t2a = t2a[[c for c in keep_2a if c in t2a.columns]].dropna(subset=["abn"])

    # ── Table 1a: total assets ─────────────────────────────────────────────────
    t1a = pd.read_excel(xf, sheet_name="Table 1a", header=3, engine="openpyxl")
    t1a.columns = [_norm(c) for c in t1a.columns]
    t1a = t1a.rename(columns={
        "Period*":      "quarter_year",
        "Fund ABN":     "abn",
        "Total assets": "net_assets_m",
    })
    t1a = t1a[["quarter_year", "abn", "net_assets_m"]].dropna(subset=["abn"])
    # multiple lifecycle rows per fund — sum assets to fund level
    t1a = t1a.groupby(["quarter_year", "abn"], as_index=False)["net_assets_m"].sum()

    # ── Table 4: member accounts ───────────────────────────────────────────────
    t4 = pd.read_excel(xf, sheet_name="Table 4", header=2, engine="openpyxl")
    t4.columns = [_norm(c) for c in t4.columns]
    t4 = t4.rename(columns={
        "Period *":        "quarter_year",
        "Fund ABN":        "abn",
        "Member accounts": "member_accounts",
    })
    t4 = t4[["quarter_year", "abn", "member_accounts"]].dropna(subset=["abn"])
    # aggregate lifecycle stages to fund level
    t4 = t4.groupby(["quarter_year", "abn"], as_index=False)["member_accounts"].sum()

    # ── normalise join keys to string so types match across sheets ────────────
    for frame in (t2a, t1a, t4):
        frame["quarter_year"] = frame["quarter_year"].astype(str).str.strip()
        frame["abn"] = frame["abn"].astype(str).str.strip()

    # ── merge ──────────────────────────────────────────────────────────────────
    df = t2a.merge(t1a, on=["quarter_year", "abn"], how="left")
    df = df.merge(t4,  on=["quarter_year", "abn"], how="left")
    return df


def _read_from_s3(run_date: date, filename: str) -> pd.DataFrame:
    s3 = boto3.client("s3")
    key = f"raw/{run_date.isoformat()}/{filename}"
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    raw_bytes = obj["Body"].read()

    if filename == "apra_mysuper_quarterly.xlsx":
        return _read_mysuper(raw_bytes)

    # other files: load first sheet as-is (used for future dbt models)
    df = pd.read_excel(io.BytesIO(raw_bytes), engine="openpyxl")
    df.columns = [_norm(c) for c in df.columns]
    return df


def load_all(run_date: date | None = None) -> None:
    run_date = run_date or date.today()
    engine = create_engine(DB_URL)

    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS raw"))

    for filename, table in TABLES.items():
        print(f"Loading {filename} -> {table}")
        df = _read_from_s3(run_date, filename)
        df["_loaded_at"] = pd.Timestamp.utcnow()
        df["_run_date"] = run_date
        schema, tbl = table.split(".")
        df.to_sql(
            tbl, engine, schema=schema,
            if_exists="replace", index=False,
            method="multi", chunksize=5000,
        )
        print(f"  Loaded {len(df)} rows into {table}")


if __name__ == "__main__":
    load_all()
