import logging
import os
import time
from collections.abc import Callable
from typing import TypeVar

# Same format as api/main.py, so pipeline and API logs grep the same way.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger(__name__)

T = TypeVar("T")


def configure_logging() -> None:
    """Root logging setup for the `python -m ingestion.*` entry points (level via LOG_LEVEL)."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format=LOG_FORMAT)


def with_retry(
    fn: Callable[[], T],
    *,
    retry_on: tuple[type[BaseException], ...],
    what: str,
    attempts: int = 3,
    base_delay: float = 2.0,
) -> T:
    """Call fn(), retrying transient errors with exponential backoff (2s, 4s, ...).

    Only exceptions in `retry_on` are retried; anything else, or the last failed
    attempt, is re-raised so the run still fails loudly.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt == attempts:
                logger.error("%s failed after %d attempts: %s", what, attempts, exc)
                raise
            delay = base_delay * 2 ** (attempt - 1)
            logger.warning("%s failed (attempt %d/%d): %s; retrying in %.0fs",
                           what, attempt, attempts, exc, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")
