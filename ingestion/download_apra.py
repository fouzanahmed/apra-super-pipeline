"""
Download APRA superannuation Excel files from the public website.
Run: python -m ingestion.download_apra
"""
import os
import re
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

APRA_SOURCES = {
    "mysuper_quarterly": {
        "url": "https://www.apra.gov.au/sites/default/files/2025-03/Quarterly%20superannuation%20performance%20statistics%20-%20December%202024.xlsx",
        "filename": "apra_mysuper_quarterly.xlsx",
    },
    "fund_level_quarterly": {
        "url": "https://www.apra.gov.au/sites/default/files/2025-03/Quarterly%20Superannuation%20Fund%20Statistics%20December%202024.xlsx",
        "filename": "apra_fund_level_quarterly.xlsx",
    },
    "annual_bulletin": {
        "url": "https://www.apra.gov.au/sites/default/files/2024-12/Annual%20superannuation%20bulletin%20June%202023.xlsx",
        "filename": "apra_annual_bulletin.xlsx",
    },
}


def _snake(name: str) -> str:
    name = re.sub(r"[^\w\s]", "", name)
    return re.sub(r"\s+", "_", name.strip()).lower()


def download_file(key: str, source: dict) -> Path:
    dest = RAW_DIR / source["filename"]
    print(f"Downloading {key} -> {dest}")
    r = requests.get(source["url"], timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"  Saved {len(r.content) / 1024:.0f} KB")
    return dest


def clean_excel(path: Path, sheet_index: int = 0, header_row: int = 3) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_index, header=header_row, engine="openpyxl")
    df.columns = [_snake(str(c)) for c in df.columns]
    df = df.dropna(how="all")
    df = df.loc[:, ~df.columns.str.startswith("unnamed")]
    print(f"  {path.name}: {len(df)} rows, {len(df.columns)} columns")
    return df


def download_all() -> dict[str, pd.DataFrame]:
    frames = {}
    for key, source in APRA_SOURCES.items():
        path = download_file(key, source)
        frames[key] = clean_excel(path)
    return frames


if __name__ == "__main__":
    frames = download_all()
    for key, df in frames.items():
        print(f"\n{key}: {df.shape}")
        print(df.head(2))
