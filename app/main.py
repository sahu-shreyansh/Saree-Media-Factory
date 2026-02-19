"""Janardan Saree Media Factory — Production-Grade FastAPI Application.

Features:
  - Dispatcher engine: deterministic stage routing via DISPATCH_MAP
  - Auto-retry: self-healing failed stages with exponential backoff
  - Dead-letter queue: terminal state after max retries
  - Startup recovery: reset stuck Processing flags on boot
  - Parallel processing: asyncio.gather for multiple rows
  - Row locking: Processing boolean prevents double execution
  - Centralized logging with structured transitions
"""

from __future__ import annotations

import asyncio
import importlib
import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

from app.config import get_settings
from app.services.baserow import db, load_status_map, STATUS_MAP
from app.services.state_machine import DISPATCH_MAP
from app.services import retry as retry_engine

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Lifespan: startup + shutdown
# ═══════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load status map, recover stuck rows, start scheduler."""
    s = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, s.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("app.log", mode="a", encoding="utf-8"),
        ],
    )

    # Load Baserow status option IDs
    await load_status_map()
    logger.info("Status map loaded (%d statuses)", len(STATUS_MAP))

    # ── Startup Recovery: reset stuck Processing flags ────────────
    await _recover_stuck_rows(s.BASEROW_POSTS_TABLE_ID)

    # Start background scheduler
    scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("Pipeline scheduler started")

    yield  # App running

    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    logger.info("Pipeline scheduler stopped")


app = FastAPI(
    title="Janardan Saree Media Factory",
    version="2.0.0",
    lifespan=lifespan,
)


# ═══════════════════════════════════════════════════════════════════════
# Startup Recovery
# ═══════════════════════════════════════════════════════════════════════

async def _recover_stuck_rows(table_id: int) -> None:
    """Reset any rows stuck with Processing=true (crashed mid-run)."""
    try:
        stuck = await db.search_rows(
            table_id,
            filters={"filter__field_5873__boolean": "1"},
            limit=50,
        )
        if stuck:
            logger.warning("Found %d stuck rows (Processing=true), resetting...", len(stuck))
            for row in stuck:
                await db.update_row(table_id, row["id"], {"Processing": False})
                logger.info("  Reset Processing for row %d", row["id"])
        else:
            logger.info("No stuck rows found — clean startup")
    except Exception:
        logger.exception("Failed to recover stuck rows (non-fatal)")


# ═══════════════════════════════════════════════════════════════════════
# Scheduler — Main dispatch loop
# ═══════════════════════════════════════════════════════════════════════

async def _scheduler_loop() -> None:
    """Continuously poll Baserow and dispatch pipeline stages."""
    s = get_settings()
    table_id = s.BASEROW_POSTS_TABLE_ID
    max_concurrent = s.MAX_CONCURRENT_ROWS

    while True:
        try:
            # ── Phase 1: Process FAILED rows (auto-retry) ─────────
            await _process_failed_rows(table_id)

            # ── Phase 2: Dispatch normal pipeline stages ──────────
            for status, pipeline_name in DISPATCH_MAP.items():
                status_id = STATUS_MAP.get(status)
                if not status_id:
                    continue

                rows = await db.search_rows(
                    table_id,
                    filters={f"filter__field_5739__single_select_equal": status_id},
                    limit=max_concurrent,
                )
                if not rows:
                    continue

                pipeline_module = _import_pipeline(pipeline_name)
                if not pipeline_module:
                    continue

                # Filter out rows already being processed
                eligible = [r for r in rows if not r.get("Processing")]
                if not eligible:
                    continue

                logger.info(
                    "Dispatching %d row(s) for %s [%s]",
                    len(eligible), status, pipeline_name,
                )

                # Run up to max_concurrent rows in parallel
                tasks = [
                    _run_pipeline_task(pipeline_module, row, table_id)
                    for row in eligible
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception:
            logger.exception("Scheduler tick failed (will retry next cycle)")

        await asyncio.sleep(s.POLL_INTERVAL_SECONDS)


# ═══════════════════════════════════════════════════════════════════════
# Auto-Retry Engine
# ═══════════════════════════════════════════════════════════════════════

FAILED_STATUSES = [
    "FAILED_STAGE_1",
    "FAILED_STAGE_2",
    "FAILED_STAGE_ANGLES",
    "FAILED_STAGE_VIDEO",
]


async def _process_failed_rows(table_id: int) -> None:
    """Check all failed rows and retry or dead-letter them."""
    for failed_status in FAILED_STATUSES:
        status_id = STATUS_MAP.get(failed_status)
        if not status_id:
            continue

        rows = await db.search_rows(
            table_id,
            filters={f"filter__field_5739__single_select_equal": status_id},
            limit=10,
        )
        if not rows:
            continue

        for row in rows:
            row_id = row["id"]
            if row.get("Processing"):
                continue

            # Check if we should retry
            if await retry_engine.should_retry(row, table_id):
                recovery_status = retry_engine.get_recovery_status(failed_status)
                if not recovery_status:
                    continue

                recovery_id = STATUS_MAP.get(recovery_status)
                if not recovery_id:
                    logger.error("Recovery status %r not in STATUS_MAP", recovery_status)
                    continue

                last_err = row.get("last_error", "") or row.get("error_message", "")
                await retry_engine.record_attempt(row_id, table_id, str(last_err))

                # Reset status to retry input state
                await db.update_row(table_id, row_id, {"Status": recovery_id})
                count = retry_engine._get_retry_count(row) + 1
                logger.info(
                    "Row %d: auto-retry #%d (%s → %s)",
                    row_id, count, failed_status, recovery_status,
                )
            else:
                # Exhausted retries → dead-letter
                await retry_engine.dead_letter(row_id, table_id)


# ═══════════════════════════════════════════════════════════════════════
# Pipeline Task Execution
# ═══════════════════════════════════════════════════════════════════════

async def _run_pipeline_task(
    pipeline_module, row: dict, table_id: int
) -> None:
    """Execute a pipeline stage with row locking."""
    row_id = row["id"]
    try:
        await db.update_row(table_id, row_id, {"Processing": True})
        await pipeline_module.run(row)
        # On success, reset retry counter
        await retry_engine.reset_retry(row_id, table_id)
    except Exception as e:
        logger.exception(
            "Pipeline error row %d (%s): %s", row_id, pipeline_module.__name__, e
        )
    finally:
        try:
            await db.update_row(table_id, row_id, {"Processing": False})
        except Exception:
            logger.exception("Failed to clear Processing for row %d", row_id)


def _import_pipeline(name: str):
    """Dynamically import a pipeline module."""
    try:
        return importlib.import_module(f"app.pipelines.{name}")
    except ImportError:
        logger.error("Pipeline module not found: app.pipelines.%s", name)
        return None


# ═══════════════════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "features": [
            "auto_retry",
            "dead_letter",
            "startup_recovery",
            "rate_limiting",
            "parallel_processing",
            "watermarking",
        ],
    }


@app.post("/trigger/{row_id}/{stage}")
async def trigger_stage(row_id: int, stage: str):
    """Manually trigger a pipeline stage for a specific row."""
    s = get_settings()
    table_id = s.BASEROW_POSTS_TABLE_ID

    pipeline_module = _import_pipeline(stage)
    if not pipeline_module:
        raise HTTPException(404, f"Pipeline stage not found: {stage}")

    row = await db.get_row(table_id, row_id)
    if not row:
        raise HTTPException(404, f"Row {row_id} not found")

    # Run in background
    asyncio.create_task(_run_pipeline_task(pipeline_module, row, table_id))
    return {"message": f"Stage '{stage}' triggered for row {row_id}"}


@app.post("/retry-dead-letter/{row_id}")
async def retry_dead_letter(row_id: int):
    """Manually retry a dead-lettered row (resets retry count)."""
    s = get_settings()
    table_id = s.BASEROW_POSTS_TABLE_ID

    row = await db.get_row(table_id, row_id)
    if not row:
        raise HTTPException(404, f"Row {row_id} not found")

    status = _get_status_text(row)
    if status != "DEAD_LETTER":
        raise HTTPException(400, f"Row {row_id} is not in DEAD_LETTER (current: {status})")

    # We need to know which stage failed — check error_message for the last failed stage
    # Default to Draft (restart from beginning)
    await retry_engine.reset_retry(row_id, table_id)
    draft_id = STATUS_MAP.get("Draft")
    if draft_id:
        await db.update_row(table_id, row_id, {"Status": draft_id})

    return {"message": f"Row {row_id} reset from DEAD_LETTER to Draft — will retry"}


@app.get("/pipeline/status")
async def pipeline_status():
    """Get overview of all rows and their current status."""
    s = get_settings()
    table_id = s.BASEROW_POSTS_TABLE_ID

    rows = await db.search_rows(table_id, limit=100)
    summary = {}
    for row in rows:
        status = _get_status_text(row)
        summary.setdefault(status, []).append({
            "id": row["id"],
            "name": row.get("Name", ""),
            "retry_count": row.get("retry_count", 0),
            "processing": row.get("Processing", False),
        })

    return {"total_rows": len(rows), "by_status": summary}


def _get_status_text(row: dict) -> str:
    """Extract status text from a row."""
    status = row.get("Status")
    if isinstance(status, dict):
        return status.get("value", "Unknown")
    return str(status) if status else "Unknown"


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.API_HOST,
        port=s.API_PORT,
        reload=s.API_DEBUG,
    )
