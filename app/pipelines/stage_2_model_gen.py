"""Stage 2: Front Model Image Generation.

Trigger:  Status = MANNEQUIN_UPSCALED
Input:    mannequin_*_upscaled (4 Freepik URLs from Stage 1)
Output:   model_image_raw (ImgBB URL), model_image_upscaled (ImgBB URL)
Success:  HERO_READY  (manual approval gate)
Failure:  FAILED_STAGE_2

Flow (matches n8n):
  1. Send 4 upscaled mannequin URLs to OpenRouter (gemini-3-pro-image-preview)
  2. Get generated image → convert to data:image/png;base64,...
  3. Upload raw generated image to ImgBB
  4. Upscale via Freepik (same flow as Stage 1)
  5. After COMPLETED → upload upscaled to ImgBB
  6. Save final ImgBB public URL to Baserow
  7. Save URL as file field in Baserow: [{"url": "..."}]
  8. Status → HERO_READY (waiting for manual approval)

Note: This generates only the FRONT model image.
      After approval (APPROVED_FOR_ANGLES), Stage 4 generates 4 angle images.
"""

import asyncio
import logging

from app.config import get_settings
from app.services.baserow import db, load_status_map, STATUS_MAP
from app.services import openrouter, freepik, imgbb
from app.services.image_utils import download_image_as_base64

logger = logging.getLogger(__name__)


async def run(row: dict) -> None:
    row_id = row["id"]
    s = get_settings()
    name = row.get("Name", f"row_{row_id}")

    # ─── STATUS GUARD ─────────────────────────────────────────────
    current = row.get("Status", {})
    if isinstance(current, dict):
        current = current.get("value", "")
    if current != "MANNEQUIN_UPSCALED":
        logger.warning("Stage 2: row %d status '%s' != MANNEQUIN_UPSCALED", row_id, current)
        return

    logger.info("Stage 2: Processing row %d (%s) — Front model generation", row_id, name)

    # ─── Get the 4 upscaled image URLs from Stage 1 ──────────────
    front_url = row.get("mannequin_front_upscaled", "")
    closer_url = row.get("mannequin_closer_upscaled", "")
    border_url = row.get("mannequin_border_upscaled", "")
    pallu_url = row.get("mannequin_pallu_upscaled", "")

    if not all([front_url, closer_url, border_url, pallu_url]):
        await _fail(row_id, s, "Missing upscaled mannequin images")
        return

    try:
        # ── Step 1: Set status to GENERATING_MODEL ────────────────
        await _set_status(row_id, s, "GENERATING_MODEL")

        # ── Step 2: Send 4 images to OpenRouter/Gemini ────────────
        logger.info("Row %d: Sending 4 upscaled images to OpenRouter (gemini-3-pro-image-preview)...", row_id)
        image_data_url = await openrouter.generate_model_image(
            front_url=front_url,
            closer_url=closer_url,
            border_url=border_url,
            pallu_url=pallu_url,
        )

        if not image_data_url:
            await _fail(row_id, s, "OpenRouter returned no image")
            return

        logger.info("Row %d: Got generated image from Gemini (len=%d)", row_id, len(image_data_url))

        # ── Step 3: Upload raw generated image to ImgBB ───────────
        raw_result = await imgbb.upload_image(image_data_url, name=f"{name}_model_raw")
        raw_url = raw_result["url"]
        logger.info("Row %d: Raw model image → ImgBB: %s", row_id, raw_url)

        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {
            "model_image_raw": raw_url,
        })
        await _set_status(row_id, s, "MODEL_GENERATED")

        # ── Step 4: Upscale via Freepik ───────────────────────────
        await _set_status(row_id, s, "UPSCALING_MODEL")
        logger.info("Row %d: Upscaling model image via Freepik...", row_id)
        upscaled_freepik_url = await freepik.upscale_image(image_data_url=image_data_url)
        logger.info("Row %d: Freepik upscale done → %s", row_id, upscaled_freepik_url[:80])

        # ── Step 5: Download upscaled → upload to ImgBB ──────────
        upscaled_b64 = await download_image_as_base64(upscaled_freepik_url)
        if not upscaled_b64:
            await _fail(row_id, s, "Failed to download upscaled image from Freepik")
            return

        upscaled_result = await imgbb.upload_image(upscaled_b64, name=f"{name}_model_upscaled")
        final_url = upscaled_result["url"]
        logger.info("Row %d: Upscaled model image → ImgBB: %s", row_id, final_url)

        # ── Step 6: Save to Baserow + set HERO_READY ──────────────
        await load_status_map()
        hero_ready_id = STATUS_MAP.get("HERO_READY")
        if not hero_ready_id:
            await _fail(row_id, s, "HERO_READY status not found in STATUS_MAP")
            return

        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {
            "model_image_upscaled": final_url,
            "Status": hero_ready_id,
            "error_message": "",
        })

        logger.info(
            "Stage 2 COMPLETE row %d → HERO_READY (awaiting manual approval)",
            row_id,
        )

    except Exception as e:
        logger.exception("Stage 2 error for row %d", row_id)
        await _fail(row_id, s, str(e))


# ── Helpers ───────────────────────────────────────────────────────────

async def _set_status(row_id: int, s, status: str) -> None:
    """Set status using STATUS_MAP."""
    await load_status_map()
    sid = STATUS_MAP.get(status)
    if sid:
        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {"Status": sid})
        logger.info("Row %d: Status → %s", row_id, status)


async def _fail(row_id: int, s, msg: str) -> None:
    """Mark row as FAILED_STAGE_2."""
    logger.error("Stage 2 FAILED row %d: %s", row_id, msg)
    await load_status_map()
    failed_id = STATUS_MAP.get("FAILED_STAGE_2")
    await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {
        "Status": failed_id,
        "error_message": f"Stage 2: {msg}",
    })
