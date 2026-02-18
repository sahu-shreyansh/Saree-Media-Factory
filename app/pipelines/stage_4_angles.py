"""Stage 4: Angle Image Generation.

Trigger:  Status = APPROVED_FOR_ANGLES
Input:    model_image_upscaled (single reference from Stage 2)
Output:   angle_side_final, angle_back_final, angle_closeup_final, angle_movement_final
Success:  IMAGES_READY  (only if ALL 4 succeed)
Failure:  FAILED_STAGE_ANGLES

Flow (matches n8n / image_prompt.md):
  For each angle (side, back, closeup, movement):
    1. Send model_image_upscaled + angle-specific prompt to OpenRouter
    2. Extract generated image → data:image/png;base64,...
    3. Upload raw to ImgBB
    4. Upscale via Freepik (20s cooldown between each)
    5. Download upscaled → upload to ImgBB
    6. Save final ImgBB URL to Baserow

Note: Uses model_image_upscaled as single reference (not mannequin images).
"""

import logging

from app.config import get_settings
from app.services.baserow import db, load_status_map, STATUS_MAP
from app.services import openrouter, freepik, imgbb
from app.services.image_utils import download_image_as_base64

logger = logging.getLogger(__name__)

# (angle_type for openrouter, baserow output field)
ANGLES = [
    ("side",     "angle_side_final"),
    ("back",     "angle_back_final"),
    ("closeup",  "angle_closeup_final"),
    ("movement", "angle_movement_final"),
]


async def run(row: dict) -> None:
    row_id = row["id"]
    s = get_settings()
    name = row.get("Name", f"row_{row_id}")

    # ─── STATUS GUARD ─────────────────────────────────────────────
    current = row.get("Status", {})
    if isinstance(current, dict):
        current = current.get("value", "")
    if current != "APPROVED_FOR_ANGLES":
        logger.warning("Stage 4: row %d status '%s' != APPROVED_FOR_ANGLES", row_id, current)
        return

    logger.info("Stage 4: Processing row %d (%s) — 4 angle images", row_id, name)

    # ─── Get reference image (model_image_upscaled from Stage 2) ──
    reference_url = row.get("model_image_upscaled", "")
    if not reference_url:
        await _fail(row_id, s, "Missing model_image_upscaled")
        return

    try:
        # ── Mark in-progress ──────────────────────────────────────
        await _set_status(row_id, s, "GENERATING_ANGLES")

        updates: dict[str, str] = {}
        errors: list[str] = []

        # ── Generate each angle sequentially ──────────────────────
        for angle_type, output_field in ANGLES:
            # Skip if already generated (resume support)
            if row.get(output_field):
                logger.info("Row %d: %s already exists, skipping", row_id, output_field)
                updates[output_field] = row[output_field]
                continue

            logger.info("Row %d: Generating %s angle...", row_id, angle_type)
            try:
                # Step 1: Generate via OpenRouter
                image_data_url = await openrouter.generate_angle_image(
                    angle_type=angle_type,
                    reference_image_url=reference_url,
                )
                if not image_data_url:
                    errors.append(f"{angle_type}: OpenRouter returned no image")
                    continue

                logger.info("Row %d: Got %s image (len=%d)", row_id, angle_type, len(image_data_url))

                # Step 2: Upload raw to ImgBB
                raw_result = await imgbb.upload_image(
                    image_data_url, name=f"{name}_{angle_type}_raw"
                )
                logger.info("Row %d: %s raw → ImgBB: %s", row_id, angle_type, raw_result["url"])

                # Step 3: Upscale via Freepik
                logger.info("Row %d: Upscaling %s...", row_id, angle_type)
                upscaled_freepik_url = await freepik.upscale_image(
                    image_data_url=image_data_url
                )
                logger.info("Row %d: %s Freepik done → %s", row_id, angle_type, upscaled_freepik_url[:80])

                # Step 4: Download upscaled → upload to ImgBB
                up_b64 = await download_image_as_base64(upscaled_freepik_url)
                if not up_b64:
                    errors.append(f"{angle_type}: Failed to download upscaled from Freepik")
                    continue

                final_result = await imgbb.upload_image(
                    up_b64, name=f"{name}_{angle_type}_final"
                )
                updates[output_field] = final_result["url"]
                logger.info("Row %d: %s final → ImgBB: %s", row_id, angle_type, final_result["url"])

            except Exception as e:
                logger.exception("Row %d: %s angle failed", row_id, angle_type)
                errors.append(f"{angle_type}: {e}")

        # ── SUCCESS CHECK: Require Side, Back, Closeup (Movement Optional) ──
        required = ["angle_side_final", "angle_back_final", "angle_closeup_final"]
        missing = [key for key in required if not updates.get(key) and not row.get(key)]
        
        if missing:
            error_msg = f"Missing required angles: {', '.join(missing)}"
            if errors:
                error_msg += f"; Errors: {'; '.join(errors)}"
            await _fail(row_id, s, error_msg)
            return

        # ── SUCCESS → IMAGES_READY ────────────────────────────────
        await load_status_map()
        images_ready_id = STATUS_MAP.get("IMAGES_READY")
        if not images_ready_id:
            await _fail(row_id, s, "IMAGES_READY status not found in STATUS_MAP")
            return

        updates["Status"] = images_ready_id
        # Keep robustness: if movement failed, note it in error_message but don't fail stage
        if errors:
            updates["error_message"] = f"Partial success. Failed: {'; '.join(errors)}"
        else:
            updates["error_message"] = ""
            
        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, updates)
        logger.info("Stage 4 COMPLETE row %d → IMAGES_READY (Partial: %s)", row_id, updates.get("error_message", "None"))

    except Exception as e:
        logger.exception("Stage 4 error for row %d", row_id)
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
    """Mark row as FAILED_STAGE_ANGLES."""
    logger.error("Stage 4 FAILED row %d: %s", row_id, msg)
    await load_status_map()
    failed_id = STATUS_MAP.get("FAILED_STAGE_ANGLES")
    await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {
        "Status": failed_id,
        "error_message": f"Stage 4: {msg}",
    })
