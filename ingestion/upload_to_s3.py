"""
Upload raw APRA Excel files to S3 (data lake layer).
Keeps a dated archive: s3://super-pipeline/raw/YYYY-MM-DD/filename.xlsx
"""
import os
import boto3
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

S3_BUCKET = os.environ["S3_BUCKET"]
RAW_DIR = Path("data/raw")


def upload_raw_files(run_date: date | None = None) -> list[str]:
    run_date = run_date or date.today()
    prefix = f"raw/{run_date.isoformat()}"
    s3 = boto3.client("s3")
    uploaded = []

    for file in RAW_DIR.glob("*.xlsx"):
        key = f"{prefix}/{file.name}"
        print(f"Uploading {file.name} -> s3://{S3_BUCKET}/{key}")
        s3.upload_file(str(file), S3_BUCKET, key)
        uploaded.append(key)

    print(f"Uploaded {len(uploaded)} files.")
    return uploaded


if __name__ == "__main__":
    upload_raw_files()
