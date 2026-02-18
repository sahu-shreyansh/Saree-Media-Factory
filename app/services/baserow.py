"""Baserow REST API client — replaces Airtable.

Baserow row endpoints:
  GET    /api/database/rows/table/{table_id}/           (list / search)
  GET    /api/database/rows/table/{table_id}/{row_id}/  (get one)
  PATCH  /api/database/rows/table/{table_id}/{row_id}/  (update)
  POST   /api/database/rows/table/{table_id}/           (create)

Filtering uses query params like ?filter__field_123__equal=Draft
Baserow also supports human-readable field names via ?user_field_names=true
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class BaserowClient:
    """Thin async wrapper around the Baserow REST API."""

    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.BASEROW_URL.rstrip("/")
        self.token = s.BASEROW_TOKEN
        self.headers = {
            "Authorization": f"Token {self.token}",
            "Content-Type": "application/json",
        }

    # ── helpers ───────────────────────────────────────────────────────

    def _rows_url(self, table_id: int, row_id: int | None = None) -> str:
        base = f"{self.base_url}/api/database/rows/table/{table_id}/"
        if row_id is not None:
            base += f"{row_id}/"
        return base

    # ── public API ────────────────────────────────────────────────────

    async def search_rows(
        self,
        table_id: int,
        filters: dict[str, str] | None = None,
        *,
        limit: int = 1,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search / list rows with optional filtering.

        `filters` maps a Baserow filter expression to its value.
        Example: {"filter__Status__equal": "Draft"}
        """
        params: dict[str, Any] = {
            "user_field_names": "true",
            "size": limit,
        }
        if filters:
            params.update(filters)
        if order_by:
            params["order_by"] = order_by

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                self._rows_url(table_id),
                headers=self.headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("results", [])
            logger.info("Baserow search table=%s filters=%s → %d rows", table_id, filters, len(rows))
            return rows

    async def get_row(self, table_id: int, row_id: int) -> dict[str, Any]:
        """Fetch a single row by ID."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                self._rows_url(table_id, row_id),
                headers=self.headers,
                params={"user_field_names": "true"},
            )
            resp.raise_for_status()
            return resp.json()

    async def update_row(
        self, table_id: int, row_id: int, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a row — only the supplied fields are changed."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                self._rows_url(table_id, row_id),
                headers=self.headers,
                json=fields,
                params={"user_field_names": "true"},
            )
            resp.raise_for_status()
            logger.info("Baserow updated table=%s row=%s fields=%s", table_id, row_id, list(fields.keys()))
            return resp.json()

    async def create_row(
        self, table_id: int, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new row."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                self._rows_url(table_id),
                headers=self.headers,
                json=fields,
                params={"user_field_names": "true"},
            )
            resp.raise_for_status()
            logger.info("Baserow created row in table=%s", table_id)
            return resp.json()
    async def get_fields(self, table_id: int) -> list[dict[str, Any]]:
        """Fetch field definitions for a table."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/api/database/fields/table/{table_id}/",
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_option_id(self, table_id: int, field_name: str, value: str) -> int | None:
        """Find the ID of a single_select option by its text value."""
        fields = await self.get_fields(table_id)
        field = next((f for f in fields if f["name"] == field_name), None)
        if not field:
            logger.warning("Field '%s' not found in table %s", field_name, table_id)
            return None
        
        options = field.get("select_options", [])
        option = next((o for o in options if o["value"] == value), None)
        if not option:
            logger.warning("Option '%s' not found in field '%s'", value, field_name)
            return None
            
        return option["id"]


# Global instance
db = BaserowClient()

# ── Status option ID cache ────────────────────────────────────────────
# Baserow single_select fields require numeric option IDs for filtering.
# This map is loaded once at startup: {"Draft": 1, "MANNEQUIN_UPSCALED": 2, ...}
STATUS_MAP: dict[str, int] = {}


async def load_status_map() -> dict[str, int]:
    """Fetch Status field metadata and cache text→ID mapping.

    Must be called once before the scheduler starts.
    Uses .clear()/.update() to mutate the existing dict in-place,
    so all modules that imported STATUS_MAP see the new data.
    """
    s = get_settings()
    fields = await db.get_fields(s.BASEROW_POSTS_TABLE_ID)

    # Log all fields for debugging
    for f in fields:
        logger.debug("Field: %s | %s | %s", f["id"], f["name"], f["type"])

    status_field = next((f for f in fields if f["name"] == "Status"), None)
    if not status_field:
        raise RuntimeError("Status field not found in Posts table")

    new_map = {
        opt["value"]: opt["id"]
        for opt in status_field.get("select_options", [])
    }

    # Mutate in-place so all importers share the same reference
    STATUS_MAP.clear()
    STATUS_MAP.update(new_map)

    logger.info("Loaded STATUS_MAP (%d options): %s", len(STATUS_MAP), STATUS_MAP)
    return STATUS_MAP
