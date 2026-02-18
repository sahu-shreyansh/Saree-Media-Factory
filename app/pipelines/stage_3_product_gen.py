"""Stage 3: Product Generation — DEPRECATED.

In V3 schema, hero image generation is handled by Stage 2 (stage_2_model_gen.py).
This module exists only to prevent ImportErrors from main.py.
"""

import logging

logger = logging.getLogger(__name__)


async def run(row: dict) -> None:
    logger.info("Stage 3 is deprecated in V3 schema. Hero gen merged into Stage 2.")
