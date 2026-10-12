import logging
import os

# Same format as api/main.py, so pipeline and API logs grep the same way.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    """Root logging setup for the `python -m ingestion.*` entry points (level via LOG_LEVEL)."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format=LOG_FORMAT)
