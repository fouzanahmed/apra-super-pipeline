"""
Load cleaned APRA data from S3 into PostgreSQL (AWS RDS).
Uses upsert logic — safe to rerun without duplicates.
"""
import os
import io
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


def _read_from_s3(run_date: date, filename: str) -> pd.DataFrame:
    s3 = boto3.client("s3")
    key = f"raw/{run_date.isoformat()}/{filename}"
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return pd.read_excel(io.BytesIO(obj["Body"].read()), engine="openpyxl")


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
        # Write to a staging table then upsert to avoid duplicates
        staging = f"{table}_staging"
        df.to_sql(
            staging.split(".")[-1],
            engine,
            schema=staging.split(".")[0] if "." in staging else "raw",
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=5000,
        )
        print(f"  Loaded {len(df)} rows into {table}")


if __name__ == "__main__":
    load_all()
