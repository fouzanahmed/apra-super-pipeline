"""
Download APRA superannuation Excel files from the public website.
Run: python -m ingestion.download_apra
"""
import re
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

RAW_DIR = Path("data/raw")

APRA_SOURCES = {
    "mysuper_quarterly": {
        "url": "https://www.apra.gov.au/sites/default/files/2026-02/Quarterly%20MySuper%20statistics%20from%20September%202020%20to%20December%202025.xlsx",
        "filename": "apra_mysuper_quarterly.xlsx",
    },
    "fund_level_quarterly": {
        "url": "https://www.apra.gov.au/sites/default/files/2026-03/Quarterly%20Superannuation%20Fund%20Statistics%20December%202025.xlsx",
        "filename": "apra_fund_level_quarterly.xlsx",
    },
    "annual_bulletin": {
        "url": "https://www.apra.gov.au/sites/default/files/2025-12/Annual%20superannuation%20bulletin%20June%202015%20to%20June%202025%20-%20Superannuation%20entities.xlsx",
        "filename": "apra_annual_bulletin.xlsx",
    },
}


# Structural contract each publication must meet before it enters the pipeline:
# sheet name -> (0-based header row, columns that must appear on that row).
# Mirrors the hard-coded sheets/header rows/column names in
# load_to_postgres._read_mysuper, so a restructured APRA file fails here, at
# download, instead of loading partial or NULL data downstream.
# Sources with no entry are only checked for being a readable workbook.
EXPECTED_SCHEMAS = {
    "mysuper_quarterly": {
        "Table 2a": (4, [
            "Period*", "MySuper product name", "Fund name", "Fund ABN", "Fund type",
            "One-year net return (rep member) - Annualised",
            "Three year net return (rep member) - Annualised",
            "Five year net return (rep member) - Annualised",
            "Investment fees (rep member)",
            "Administration fees and costs (rep member)",
            "Total fees and costs (rep member)",
        ]),
        "Table 1a": (3, ["Period*", "MySuper product name", "Fund ABN", "Total assets"]),
        "Table 4": (2, ["Period *", "MySuper product name", "Fund ABN", "Member accounts"]),
    },
}


class SchemaValidationError(ValueError):
    """Raised when a downloaded APRA workbook does not match its expected structure."""


def _snake(name: str) -> str:
    name = re.sub(r"[^\w\s]", "", name)
    return re.sub(r"\s+", "_", name.strip()).lower()


def _norm(col: str) -> str:
    """Collapse newlines/whitespace in a header cell (same rule as load_to_postgres._norm)."""
    return re.sub(r"\s+", " ", str(col).strip())


def validate_workbook(path: Path, expected: dict) -> None:
    """Check sheet names and header columns, reporting every problem found at once."""
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception as exc:  # e.g. an HTML error page saved as .xlsx
        raise SchemaValidationError(f"{path.name} is not a readable .xlsx workbook: {exc}") from exc

    problems = []
    for sheet, (header_row, columns) in expected.items():
        if sheet not in xl.sheet_names:
            problems.append(f"missing sheet {sheet!r} (found: {xl.sheet_names})")
            continue
        try:
            # Same header= read as load_to_postgres, so the check matches what it will parse.
            header = pd.read_excel(xl, sheet_name=sheet, header=header_row, nrows=0)
        except ValueError:  # sheet has fewer rows than the header row index
            problems.append(f"sheet {sheet!r} header row {header_row} is missing columns {columns}")
            continue
        found = {_norm(c) for c in header.columns}
        missing = [c for c in columns if c not in found]
        if missing:
            problems.append(f"sheet {sheet!r} header row {header_row} is missing columns {missing}")

    if problems:
        raise SchemaValidationError(
            f"{path.name} failed schema validation:\n  - " + "\n  - ".join(problems)
        )


def download_file(key: str, source: dict) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / source["filename"]
    print(f"Downloading {key} -> {dest}")
    r = requests.get(source["url"], timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"  Saved {len(r.content) / 1024:.0f} KB")
    return dest


_SKIP_SHEETS = {"cover", "notes", "contents", "important notice", "revisions",
                "explanatory notes", "filters control", "charts", "charts data"}


def clean_excel(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path, engine="openpyxl")
    data_sheets = [s for s in xl.sheet_names if s.strip().lower() not in _SKIP_SHEETS]
    if not data_sheets:
        raise ValueError(f"No data sheets found in {path.name}")
    sheet = data_sheets[0]
    # Scan first 10 rows to find the header row (first row where >3 cells are non-null)
    raw = pd.read_excel(path, sheet_name=sheet, header=None, nrows=15, engine="openpyxl")
    header_row = 0
    for i, row in raw.iterrows():
        if row.notna().sum() > 3:
            header_row = i
            break
    df = pd.read_excel(path, sheet_name=sheet, header=header_row, engine="openpyxl")
    df.columns = [_snake(str(c)) for c in df.columns]
    df = df.dropna(how="all")
    df = df.loc[:, ~df.columns.str.startswith("unnamed")]
    print(f"  {path.name} (sheet: {sheet!r}): {len(df)} rows, {len(df.columns)} columns")
    return df


def download_all() -> dict[str, pd.DataFrame]:
    frames = {}
    for key, source in APRA_SOURCES.items():
        path = download_file(key, source)
        validate_workbook(path, EXPECTED_SCHEMAS.get(key, {}))
        frames[key] = clean_excel(path)
    return frames


if __name__ == "__main__":
    frames = download_all()
    for key, df in frames.items():
        print(f"\n{key}: {df.shape}")
        print(df.head(2))
