"""Automatic retry engine — self-healing pipeline with exponential backoff.

Manages retry_count, last_error, last_attempt_at fields in Baserow.
Used by the scheduler to auto-retry failed stages before dead-lettering.

Usage:
    if await should_retry(row, table_id):
        await record_attempt(row["id"], table_id, "Starting retry")
        # ... reset status and re-dispatch ...
    else:
        await dead_letter(row["id"], table_id)
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from app.config import get_settings
from app.services.baserow import db

logger = logging.getLogger(__name__)


# ── Recovery map: FAILED status → status to retry from ────────────────
RECOVERY_MAP: dict[str, str] = {
    "FAILED_STAGE_1": "Draft",
    "FAILED_STAGE_2": "MANNEQUIN_UPSCALED",
    "FAILED_STAGE_ANGLES": "APPROVED_FOR_ANGLES",
    "FAILED_STAGE_VIDEO": "APPROVED_FOR_VIDEO",
}


async def should_retry(row: dict, table_id: int) -> bool:
    """Check if a failed row should be retried.

    Conditions:
      1. retry_count < MAX_RETRIES
      2. Enough time has elapsed (exponential backoff cooldown)
    """
    s = get_settings()
    max_retries = s.MAX_RETRIES

    count = _get_retry_count(row)
    if count >= max_retries:
        logger.info("Row %d: retry_count=%d >= max=%d → dead-letter", row["id"], count, max_retries)
        return False

    # Check backoff cooldown
    last_attempt = row.get("last_attempt_at", "")
    if last_attempt:
        try:
            last_dt = datetime.fromisoformat(last_attempt)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            elapsed = (now - last_dt).total_seconds()
            # Exponential backoff: min(2^count + random(0,1), 300) seconds
            backoff = min(2 ** count + random.random(), 300)
            if elapsed < backoff:
                logger.debug(
                    "Row %d: backoff %.0fs, elapsed %.0fs — too soon",
                    row["id"], backoff, elapsed,
                )
                return False
        except (ValueError, TypeError):
            pass  # Invalid timestamp — allow retry

    return True


async def record_attempt(row_id: int, table_id: int, error_msg: str = "") -> None:
    """Increment retry_count and record the attempt timestamp + error."""
    row = await db.get_row(table_id, row_id)
    count = _get_retry_count(row) + 1
    now = datetime.now(timezone.utc).isoformat()

    await db.update_row(table_id, row_id, {
        "retry_count": count,
        "last_error": error_msg[:2000],    # Truncate to avoid Baserow limits
        "last_attempt_at": now,
    })
    logger.info("Row %d: recorded retry attempt #%d at %s", row_id, count, now)


async def reset_retry(row_id: int, table_id: int) -> None:
    """Reset retry counter on successful stage completion."""
    await db.update_row(table_id, row_id, {
        "retry_count": 0,
        "last_error": "",
        "last_attempt_at": "",
        "error_message": "",
    })


async def dead_letter(row_id: int, table_id: int) -> None:
    """Move a row to DEAD_LETTER status after all retries exhausted."""
    from app.services.baserow import load_status_map, STATUS_MAP

    await load_status_map()
    dl_id = STATUS_MAP.get("DEAD_LETTER")
    if not dl_id:
        logger.error("DEAD_LETTER status not found in STATUS_MAP")
        return

    await db.update_row(table_id, row_id, {
        "Status": dl_id,
        "error_message": "All retries exhausted — moved to dead letter",
    })
    logger.warning("Row %d → DEAD_LETTER (retries exhausted)", row_id)


def get_recovery_status(failed_status: str) -> str | None:
    """Get the status to reset to for retry."""
    return RECOVERY_MAP.get(failed_status)


def _get_retry_count(row: dict) -> int:
    """Extract retry_count from row, defaulting to 0."""
    val = row.get("retry_count")
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0
