"""Stage 1: Mannequin Upscale.

Trigger:  Status = Draft
Input:    Saree Front, Saree Closer, Saree Border, Saree Pallu (Baserow FILE fields)
Output:   mannequin_front_upscaled, mannequin_closer_upscaled, mannequin_border_upscaled, mannequin_pallu_upscaled (URL fields)
Success:  MANNEQUIN_UPSCALED  (only if ALL 4 succeed)
Failure:  FAILED_STAGE_1
"""

import logging
import asyncio

from app.config import get_settings
from app.services.baserow import db
from app.services.freepik import upscale_image
from app.services.image_utils import download_image_as_base64
from app.services.state_machine import ProductStatus

logger = logging.getLogger(__name__)

# (Baserow file field, output URL field)
IMAGE_MAP = [
    ("Saree Front",  "mannequin_front_upscaled"),
    ("Saree Closer", "mannequin_closer_upscaled"),
    ("Saree Border", "mannequin_border_upscaled"),
    ("Saree Pallu",  "mannequin_pallu_upscaled"),
]


def _extract_file_url(row: dict, field_name: str) -> str | None:
    """Extract the download URL from a Baserow file field.

    File fields return: [{"url": "http://...", "visible_name": "...", ...}]
    """
    val = row.get(field_name)
    if isinstance(val, list) and val:
        return val[0].get("url")
    if isinstance(val, str) and val.startswith("http"):
        return val
    return None


async def run(row: dict) -> None:
    row_id = row["id"]
    s = get_settings()

    # ─── STATUS GUARD ─────────────────────────────────────────────
    current = row.get("Status", {})
    if isinstance(current, dict):
        current = current.get("value", "")
    if current != ProductStatus.DRAFT:
        logger.warning("Stage 1: row %d status '%s' != Draft. Skipping.", row_id, current)
        return

    logger.info("Stage 1: Processing row %d (Upscaling mannequin)", row_id)

    # Mark in-progress
    upscaling_id = await db.get_option_id(
        s.BASEROW_POSTS_TABLE_ID, "Status", ProductStatus.UPSCALING_MANNEQUIN
    )
    if upscaling_id:
        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, {"Status": upscaling_id})

    updates: dict[str, str] = {}
    errors: list[str] = []

    # ─── PROCESS SEQUENTIALLY ─────────────────────────────────────
    for input_field, output_field in IMAGE_MAP:
        # Idempotency: skip if output already done
        if row.get(output_field):
            logger.info("Row %d: %s already exists, skipping.", row_id, output_field)
            updates[output_field] = row[output_field]
            continue

        img_url = _extract_file_url(row, input_field)
        if not img_url:
            errors.append(f"No image in '{input_field}'")
            continue

        logger.info("Row %d: Upscaling %s → %s ...", row_id, input_field, output_field)
        try:
            b64 = await download_image_as_base64(img_url)
            if not b64:
                errors.append(f"Download failed for {input_field}")
                continue

            upscaled_url = await upscale_image(image_data_url=b64)
            updates[output_field] = upscaled_url
            await asyncio.sleep(0.5)

        except Exception as e:
            errors.append(f"{input_field}: {e}")

    # ─── HARD STOP: ALL 4 MUST SUCCEED ────────────────────────────
    if errors or len(updates) < 4:
        error_msg = "; ".join(errors) if errors else "Not all images upscaled"
        logger.error("Stage 1 FAILED row %d: %s", row_id, error_msg)

        save = dict(updates)
        failed_id = await db.get_option_id(
            s.BASEROW_POSTS_TABLE_ID, "Status", ProductStatus.FAILED_STAGE_1
        )
        save["Status"] = failed_id
        save["error_message"] = f"Stage 1: {error_msg}"
        await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, save)
        return

    # ─── SUCCESS ──────────────────────────────────────────────────
    next_id = await db.get_option_id(
        s.BASEROW_POSTS_TABLE_ID, "Status", ProductStatus.MANNEQUIN_UPSCALED
    )
    updates["Status"] = next_id
    updates["error_message"] = ""
    await db.update_row(s.BASEROW_POSTS_TABLE_ID, row_id, updates)
    logger.info("Stage 1 COMPLETE row %d → MANNEQUIN_UPSCALED", row_id)
