"""Stage 5: Video Generation.

Trigger:  Status = APPROVED_FOR_VIDEO
Input:    angle_*_final images (or fallback to model_image_upscaled)
Output:   video_url
Success:  PUBLISHED
Failure:  FAILED_STAGE_VIDEO
"""

import logging

from app.config import get_settings
from app.services.baserow import db
from app.services.state_machine import ProductStatus
from app.services import kieai

logger = logging.getLogger(__name__)

ANGLE_FIELDS = [
    "angle_front_final", "angle_side_final", "angle_back_final",
    "angle_closeup_final", "angle_movement_final",
]


async def run(row: dict) -> None:
    row_id = row["id"]
    s = get_settings()

    # ─── STATUS GUARD ─────────────────────────────────────────────
    current = row.get("Status", {})
    if isinstance(current, dict):
        current = current.get("value", "")
    if current != ProductStatus.APPROVED_FOR_VIDEO:
        logger.warning("Stage 5: row %d status '%s' != APPROVED_FOR_VIDEO. Skipping.", row_id, current)
        return

    logger.info("Stage 5: Processing row %d (Video)", row_id)

    # ─── IMAGE GATHERING ──────────────────────────────────────────
    # User request: "image_urls": [ "{{ $json.frontImage }}" ]
    # We use the Stage 2 result: model_image_upscaled
    
    front_image = row.get("model_image_upscaled")
    if not front_image:
        # Fallback to manurequin upscale if model gen failed (unlikely given status guard)
        front_image = row.get("mannequin_front_upscaled")
        
    if not front_image:
        await _fail(row_id, s, "No model_image_upscaled found for video generation")
        return

    input_urls = [front_image]

    try:
        # Mark in-progress
        await _set_status(row_id, s, ProductStatus.GENERATING_VIDEO)

        logger.info("Row %d: Starting Kie.ai video (%d images)...", row_id, len(input_urls))
        task_id = await kieai.create_video_task(image_urls=input_urls)

        if not task_id:
            raise ValueError("Failed to create video task")

        logger.info("Row %d: Polling video (task=%s)...", row_id, task_id)
        video_url = await kieai.poll_until_complete(task_id)

        if not video_url:
            raise ValueError("Video generation timed out or failed")

        # Success
        next_id = await db.get_option_id(
            s.BASEROW_POSTS_TABLE_ID, "Status", ProductStatus.PUBLISHED
        )
        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {
            "video_url": video_url,
            "Status": next_id,
            "error_message": "",
        })
        logger.info("Stage 5 COMPLETE row %d → PUBLISHED", row_id)

    except Exception as e:
        await _fail(row_id, s, str(e))


async def _set_status(row_id: int, s, status: str) -> None:
    sid = await db.get_option_id(s.BASEROW_POSTS_TABLE_ID, "Status", status)
    if sid:
        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {"Status": sid})


async def _fail(row_id: int, s, msg: str) -> None:
    logger.error("Stage 5 FAILED row %d: %s", row_id, msg)
    failed_id = await db.get_option_id(
        s.BASEROW_POSTS_TABLE_ID, "Status", ProductStatus.FAILED_STAGE_VIDEO
    )
    await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {
        "Status": failed_id,
        "error_message": f"Stage 5: {msg}",
    })
