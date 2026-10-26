"""
Upload raw APRA Excel files to S3 (data lake layer).
Keeps a dated archive: s3://super-pipeline/raw/YYYY-MM-DD/filename.xlsx
"""
import logging
import os
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from dotenv import load_dotenv

from ingestion import configure_logging, with_retry

load_dotenv()

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ["S3_BUCKET"]
RAW_DIR = Path("data/raw")

# Network blips and failed transfers (upload_file wraps S3 errors in S3UploadFailedError).
# Credential/config errors are not listed, so they still fail immediately.
TRANSIENT_S3_ERRORS = (
    S3UploadFailedError,
    EndpointConnectionError,
    ConnectionClosedError,
    ConnectTimeoutError,
    ReadTimeoutError,
)


def upload_raw_files(run_date: date | None = None) -> list[str]:
    run_date = run_date or datetime.now(timezone.utc).date()  # UTC so upload and load agree on any machine
    prefix = f"raw/{run_date.isoformat()}"
    s3 = boto3.client("s3")
    uploaded = []

    for file in RAW_DIR.glob("*.xlsx"):
        key = f"{prefix}/{file.name}"
        logger.info("Uploading %s -> s3://%s/%s", file.name, S3_BUCKET, key)
        with_retry(
            partial(s3.upload_file, str(file), S3_BUCKET, key),
            retry_on=TRANSIENT_S3_ERRORS,
            what=f"S3 upload of {file.name}",
        )
        uploaded.append(key)

    logger.info("Uploaded %d files", len(uploaded))
    return uploaded


if __name__ == "__main__":
    configure_logging()
    upload_raw_files()
