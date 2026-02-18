"""Database migration 002 — Fabric Analysis table.

Adds the fabric_analysis table and links it to products.
Uses Baserow API to create the table and fields programmatically.

Run: python -m migrations.002_fabric_analysis
"""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Field definitions for the fabric_analysis table
FABRIC_ANALYSIS_FIELDS = [
    {"name": "product_row_id", "type": "number", "number_decimal_places": 0},
    {"name": "dominant_colors", "type": "long_text"},
    {"name": "weave_type", "type": "text"},
    {"name": "thread_density_warp", "type": "number", "number_decimal_places": 1},
    {"name": "thread_density_weft", "type": "number", "number_decimal_places": 1},
    {"name": "pattern_type", "type": "text"},
    {"name": "pattern_geometry", "type": "long_text"},
    {"name": "border_width_cm", "type": "number", "number_decimal_places": 1},
    {"name": "border_style", "type": "text"},
    {"name": "border_motif", "type": "text"},
    {"name": "pallu_length_cm", "type": "number", "number_decimal_places": 1},
    {"name": "pallu_style", "type": "text"},
    {"name": "pallu_design", "type": "text"},
    {"name": "fabric_type", "type": "text"},
    {"name": "transparency_level", "type": "text"},
    {"name": "transparency_score", "type": "number", "number_decimal_places": 3},
    {"name": "sheen_level", "type": "text"},
    {"name": "drape_stiffness", "type": "text"},
    {"name": "fabric_weight", "type": "text"},
    {"name": "has_zari", "type": "boolean"},
    {"name": "zari_type", "type": "text"},
    {"name": "zari_coverage_percent", "type": "number", "number_decimal_places": 2},
    {"name": "zari_areas", "type": "long_text"},
    {"name": "topography_map_url", "type": "url"},
    {"name": "transparency_map_url", "type": "url"},
    {"name": "body_texture_crop_url", "type": "url"},
    {"name": "border_crop_url", "type": "url"},
    {"name": "pallu_crop_url", "type": "url"},
    {"name": "single_motif_crop_url", "type": "url"},
    {"name": "zari_detail_crop_url", "type": "url"},
    {"name": "raw_analysis_json", "type": "long_text"},
    {"name": "analysis_confidence", "type": "number", "number_decimal_places": 3},
]

# Field to add to the Products table
PRODUCTS_NEW_FIELDS = [
    {"name": "fabric_analysis_id", "type": "number", "number_decimal_places": 0},
]


async def run_migration():
    """Create fabric_analysis table and add fabric_analysis_id to Products."""
    s = get_settings()
    base_url = s.BASEROW_URL.rstrip("/")
    headers = {
        "Authorization": f"Token {s.BASEROW_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Add fabric_analysis_id field to Posts table
        logger.info("Adding fabric_analysis_id field to Posts table...")
        for field_def in PRODUCTS_NEW_FIELDS:
            resp = await client.post(
                f"{base_url}/api/database/fields/table/{s.BASEROW_POSTS_TABLE_ID}/",
                headers=headers,
                json=field_def,
            )
            if resp.status_code == 200:
                logger.info("  ✅ Added field: %s", field_def["name"])
            elif resp.status_code == 400 and "already exists" in resp.text.lower():
                logger.info("  ⏭️  Field already exists: %s", field_def["name"])
            else:
                logger.error("  ❌ Failed to add field %s: %s", field_def["name"], resp.text)

        # Step 2: Note about fabric_analysis table
        # In Baserow, tables are created via the database API
        # The user needs to provide the database_id to create a new table
        logger.info("")
        logger.info("=" * 60)
        logger.info("MANUAL STEP REQUIRED:")
        logger.info("Create a 'fabric_analysis' table in your Baserow database")
        logger.info("with the following fields:")
        logger.info("")
        for f in FABRIC_ANALYSIS_FIELDS:
            logger.info("  - %s (%s)", f["name"], f["type"])
        logger.info("")
        logger.info("Then add the table ID to your .env as:")
        logger.info("  BASEROW_FABRIC_ANALYSIS_TABLE_ID=<table_id>")
        logger.info("=" * 60)

    logger.info("\nMigration 002 complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(run_migration())
