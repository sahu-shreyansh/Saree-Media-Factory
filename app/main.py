"""Janardan — Saree Workflow Automation API (V2 Stabilized).

Architecture:
  scheduler tick
    ↓
  single dispatcher (iterate statuses in order)
    ↓
  exact stage by status (1 row per tick, fully serialized)
    ↓
  Processing lock prevents re-entry

Rules:
  - 1 row per stage per tick
  - Processing boolean prevents double-execution
  - Within a tick, each row dispatched at most ONCE
  - Stages run sequentially (await, not create_task)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.baserow import db, load_status_map, STATUS_MAP
from app.services.state_machine import DISPATCH_MAP

# Pipelines
from app.pipelines import (
    stage_1_upscale,
    stage_2_model_gen,
    stage_3_product_gen,
    stage_4_angles,
    stage_5_video,
)

# Map dispatch strings to modules
PIPELINE_MODULES = {
    "stage_1_upscale": stage_1_upscale,
    "stage_2_model_gen": stage_2_model_gen,
    "stage_3_product_gen": stage_3_product_gen,
    "stage_4_angles": stage_4_angles,
    "stage_5_video": stage_5_video,
}

# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("janardan")

# ── Background scheduler ─────────────────────────────────────────────

_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop() -> None:
    """Single dispatcher: one row → one stage → hard success → next.

    For each status in DISPATCH_MAP (in order):
      1. Query 1 row in that status
      2. Skip if Processing=true (another tick is already handling it)
      3. Skip if already dispatched this tick
      4. Lock row (Processing=true)
      5. Run pipeline (await — fully serialized, NOT create_task)
      6. Unlock row (Processing=false)
    """
    s = get_settings()
    interval = s.POLL_INTERVAL_SECONDS
    logger.info("Scheduler started — polling every %ds", interval)

    while True:
        try:
            logger.info("── Scheduler tick ──")
            dispatched_this_tick: set[int] = set()

            for status, pipeline_name in DISPATCH_MAP.items():
                pipeline_module = PIPELINE_MODULES.get(pipeline_name)
                if not pipeline_module:
                    continue

                # Resolve text status → numeric ID for Baserow filtering
                status_id = STATUS_MAP.get(status)
                if status_id is None:
                    logger.warning("Status '%s' not in STATUS_MAP, skipping", status)
                    continue

                try:
                    rows = await db.search_rows(
                        table_id=s.BASEROW_POSTS_TABLE_ID,
                        filters={"filter__Status__single_select_equal": status_id},
                        limit=1,
                    )
                except Exception as e:
                    logger.error("Query '%s' failed: %s", status, e)
                    continue

                if not rows:
                    continue

                row = rows[0]
                row_id = row["id"]

                # ─── Guard: already dispatched this tick ──────────
                if row_id in dispatched_this_tick:
                    logger.debug("Skipping row %d (already dispatched this tick)", row_id)
                    continue

                # ─── Guard: Processing lock ───────────────────────
                if row.get("Processing"):
                    logger.debug("Skipping row %d (Processing=true)", row_id)
                    continue

                # ─── Dispatch: sequential (await, not create_task) ─
                dispatched_this_tick.add(row_id)
                logger.info(
                    "Dispatching row %d (%s → %s)", row_id, status, pipeline_name
                )
                await _run_pipeline_task(
                    pipeline_module, row, s.BASEROW_POSTS_TABLE_ID
                )

        except Exception as exc:
            logger.exception("Scheduler tick error: %s", exc)

        await asyncio.sleep(interval)


async def _run_pipeline_task(
    pipeline_module, row: dict, table_id: int
) -> None:
    """Lock → run → unlock. Always clears Processing in finally."""
    row_id = row["id"]
    try:
        await db.update_row(table_id, row_id, {"Processing": True})
        await pipeline_module.run(row)
    except Exception as e:
        logger.exception(
            "Pipeline error row %d (%s): %s", row_id, pipeline_module.__name__, e
        )
    finally:
        try:
            await db.update_row(table_id, row_id, {"Processing": False})
        except Exception:
            logger.exception("Failed to clear Processing for row %d", row_id)


# ── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler_task
    # Load status option IDs BEFORE scheduler starts
    await load_status_map()
    logger.info("STATUS_MAP loaded: %s", STATUS_MAP)
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("🚀 Janardan V2 Stabilized — started")
    yield
    if _scheduler_task:
        _scheduler_task.cancel()
        logger.info("Scheduler stopped")


# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Janardan — Saree Workflow Automation V2",
    description="Production-grade Saree Media Factory Pipeline (Stabilized)",
    version="2.2.0",
    lifespan=lifespan,
)


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "name": "Janardan V2",
        "status": "Running",
        "pipelines": list(PIPELINE_MODULES.keys()),
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "scheduler_running": _scheduler_task is not None and not _scheduler_task.done(),
    }


@app.post("/run/{stage_name}")
async def run_stage(stage_name: str, row_id: int):
    """Manually trigger a specific pipeline stage for a row."""
    module = PIPELINE_MODULES.get(stage_name)
    if not module:
        return JSONResponse({"error": f"Unknown stage: {stage_name}"}, status_code=400)

    s = get_settings()
    try:
        row = await db.get_row(s.BASEROW_POSTS_TABLE_ID, row_id)
        await module.run(row)
        return {"status": "success", "message": f"Executed {stage_name} for row {row_id}"}
    except Exception as e:
        logger.exception("Manual run failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.API_HOST,
        port=s.API_PORT,
        reload=s.API_DEBUG,
    )
