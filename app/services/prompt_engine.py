"""Prompt engine — manages prompt templates via Baserow and fabric constraint injection.

Fetches templates from Baserow 'Prompts' table and renders them using Jinja2.
Injects fabric analysis data into prompts that contain {{ fabric_constraints }}.
"""

from __future__ import annotations

import json
import logging
from typing import Any
import time

import httpx
from jinja2 import Template

from app.config import get_settings
from app.services.baserow import db

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Simple in-memory cache: {slug: {"template": str, "timestamp": float}}
_prompts_cache: dict[str, dict] = {}
CACHE_TTL = 300  # 5 minutes


async def get_prompt_template(prompt_type: str) -> str | None:
    """Fetch active prompt template from Baserow by Type."""
    now = time.time()
    if prompt_type in _prompts_cache:
        entry = _prompts_cache[prompt_type]
        if now - entry["timestamp"] < CACHE_TTL:
            return entry["template"]

    s = get_settings()
    if not s.BASEROW_PROMPTS_TABLE_ID:
        logger.error("BASEROW_PROMPTS_TABLE_ID not set")
        return None

    # Query key: filter__Type__single_select_equal=prompt_type
    # AND filter__Is_Active__boolean=true
    try:
        rows = await db.search_rows(
            table_id=s.BASEROW_PROMPTS_TABLE_ID,
            filters={
                f"filter__Type__single_select_equal": prompt_type,
                "filter__Is_Active__boolean": "true"
            },
            limit=1
        )
    except Exception as e:
        logger.error(f"Failed to fetch prompt '{prompt_type}': {e}")
        return None

    if not rows:
        logger.warning(f"No active prompt found for type '{prompt_type}'")
        return None

    template_str = rows[0].get("Template")
    if not template_str:
        logger.warning(f"Prompt '{prompt_type}' has empty Template field")
        return None

    _prompts_cache[prompt_type] = {"template": template_str, "timestamp": now}
    logger.info(f"Loaded/Cached prompt template: {prompt_type}")
    return template_str


def _format_fabric_constraints(analysis: dict) -> str:
    """Format fabric analysis data into text for prompt injection."""
    lines = ["=== FABRIC CONSTRAINTS (from analysis) ==="]

    if analysis.get("dominant_colors"):
        colors = analysis["dominant_colors"]
        # Handle different structures if needed, assuming list of dicts
        if isinstance(colors, list) and len(colors) > 0 and isinstance(colors[0], dict):
            color_desc = ", ".join(
                f"LAB({c.get('L',0)}, {c.get('A',0)}, {c.get('B',0)}) at {c.get('percentage',0)}%"
                for c in colors[:4]
            )
            lines.append(f"COLORS: {color_desc}")

    # Add other fields safely
    for key, label in [
        ("weave_type", "WEAVE"),
        ("pattern_type", "PATTERN"),
        ("fabric_type", "FABRIC TYPE"),
        ("transparency_level", "TRANSPARENCY"),
        ("sheen_level", "SHEEN"),
        ("drape_stiffness", "DRAPE STIFFNESS")
    ]:
        if val := analysis.get(key):
            lines.append(f"{label}: {val}")

    if analysis.get("has_zari"):
        zari_type = analysis.get("zari_type", "unknown")
        lines.append(f"ZARI: {zari_type} (Reflective)")

    lines.extend([
        "",
        "MANDATORY RULES:",
        "1. COLOR ACCURACY: exact LAB values match",
        "2. PATTERN ACCURACY: motif count proportional",
        "3. FABRIC BEHAVIOR: drape matches stiffness",
        "4. TRANSPARENCY: skin shows through if sheer",
        "5. ZARI: reflectivity per type",
        "6. SHEEN: specular highlights correct",
    ])

    return "\n".join(lines)


async def build_payload(
    prompt_type: str,
    image_urls: list[str],
    row_data: dict[str, Any] | None = None,
    fabric_analysis: dict | None = None,
) -> dict | None:
    """Build OpenRouter API payload by rendering Jinja2 template from Baserow."""
    template_str = await get_prompt_template(prompt_type)
    if not template_str:
        return None

    # Prepare context
    context = row_data or {}
    
    # Inject fabric constraints
    if fabric_analysis:
        context["fabric_constraints"] = _format_fabric_constraints(fabric_analysis)
    else:
        context["fabric_constraints"] = "Note: No fabric analysis available."

    # Render template
    try:
        jinja_template = Template(template_str)
        rendered_prompt = jinja_template.render(**context)
    except Exception as e:
        logger.error(f"Jinja2 render failed for '{prompt_type}': {e}")
        return None

    s = get_settings()

    # Build content array
    content: list[dict[str, Any]] = [{"type": "text", "text": rendered_prompt}]
    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    payload = {
        "model": s.OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": content}],
        # Default params, maybe also fetch from Baserow if we add columns? 
        # For now hardcode or use defaults
        "temperature": 0.2,
        "max_tokens": 4096,
        "route": "fallback",
    }

    return payload


async def execute_prompt(
    prompt_type: str,
    image_urls: list[str],
    row_data: dict[str, Any] | None = None,
    fabric_analysis: dict | None = None,
) -> dict | None:
    """Execute prompt via OpenRouter."""
    payload = await build_payload(prompt_type, image_urls, row_data, fabric_analysis)
    if not payload:
        return None

    s = get_settings()
    headers = {
        "Authorization": f"Bearer {s.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": s.BASEROW_URL, # Optional
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()
