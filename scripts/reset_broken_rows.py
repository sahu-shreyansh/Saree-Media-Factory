"""Reset broken rows in Baserow.

Usage:
  python scripts/reset_broken_rows.py [row_id ...]

Defaults to rows 2 and 3 if no IDs given (as identified in debugging).
Resets: Status → Draft, Processing → false, clears generated fields.
"""

import asyncio
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.services.baserow import db


FIELDS_TO_CLEAR = [
    "mannequin_front_upscaled",
    "mannequin_closer_upscaled",
    "mannequin_pallu_upscaled",
    "mannequin_border_upscaled",
    "model_image_upscaled",
    "angle_side_final",
    "angle_back_final",
    "angle_closeup_final",
    "angle_movement_final",
    "video_url",
    "error_message",
]


async def reset_row(table_id: int, row_id: int) -> None:
    draft_id = await db.get_option_id(table_id, "Status", "Draft")

    payload: dict = {
        "Status": draft_id,
        "Processing": False,
    }
    for field in FIELDS_TO_CLEAR:
        payload[field] = ""

    await db.update_row(table_id, row_id, payload)
    print(f"  ✓ Row {row_id} → Draft (all fields cleared)")


async def main():
    s = get_settings()
    table_id = s.BASEROW_POSTS_TABLE_ID

    row_ids = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else [2, 3]

    print(f"Resetting rows {row_ids} in table {table_id}...")
    for rid in row_ids:
        try:
            await reset_row(table_id, rid)
        except Exception as e:
            print(f"  ✗ Row {rid} failed: {e}")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
