"""Freepik Precision Image Upscaler v2 service.

Flow (matches n8n workflow):
  1. Download image → convert to base64 → format as data:image/png;base64,...
  2. POST  /v1/ai/image-upscaler-precision-v2  → returns {data: {task_id, status: "CREATED"}}
  3. Wait 20s, then GET /{task_id} → poll every 20s
     Status flow: CREATED → IN_PROGRESS → COMPLETED | FAILED
  4. On COMPLETED → {data: {generated: ["<url>"]}}
  5. Wait 20s before next submission (between images)
"""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.freepik.com/v1/ai/image-upscaler-precision-v2"

from app.services.rate_limiter import FREEPIK_LIMITER

# Use centralized rate limiter (was local _submit_semaphore)
_submit_semaphore = FREEPIK_LIMITER


async def submit_upscale(image_data_url: str) -> str:
    """Submit an image (base64 data-url) for upscaling. Returns task_id.

    The image MUST be in format: data:image/png;base64,<base64data>
    """
    s = get_settings()
    headers = {
        "x-freepik-api-key": s.FREEPIK_API_KEY,
        "Content-Type": "application/json",
    }
    body = {
        "image": image_data_url,
        "scale_factor": str(s.FREEPIK_SCALE_FACTOR),
        "flavor": "photo",
        "sharpen": str(s.FREEPIK_SHARPEN),
        "smart_grain": str(s.FREEPIK_SMART_GRAIN),
        "ultra_detail": str(s.FREEPIK_ULTRA_DETAIL),
    }

    async with _submit_semaphore:
        for attempt in range(5):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(BASE_URL, headers=headers, json=body)

                    if resp.status_code == 429:
                        # Wait 20s + jitter on rate limit
                        wait = 20 + (attempt * 10) + random.uniform(0, 5)
                        logger.warning(
                            "Freepik 429 — waiting %.0fs (attempt %d/5)", wait, attempt + 1
                        )
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()
                    data = resp.json()

                task_id = data["data"]["task_id"]
                status = data["data"].get("status", "UNKNOWN")
                logger.info(
                    "Freepik upscale submitted → task_id=%s status=%s", task_id, status
                )
                return task_id

            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 429 or code >= 500:
                    wait = 20 + (attempt * 10) + random.uniform(0, 5)
                    logger.warning(
                        "Freepik %d — waiting %.0fs (attempt %d/5)", code, wait, attempt + 1
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
            except Exception as e:
                if attempt == 4:
                    raise
                logger.warning("Freepik submit error: %s. Waiting 20s...", e)
                await asyncio.sleep(20)

    raise RuntimeError("Freepik submission failed after 5 retries")


async def check_status(task_id: str) -> dict:
    """Check the status of an upscale task."""
    s = get_settings()
    headers = {"x-freepik-api-key": s.FREEPIK_API_KEY}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{BASE_URL}/{task_id}", headers=headers)
        resp.raise_for_status()
        return resp.json()


async def poll_until_complete(
    task_id: str,
    *,
    interval: int = 20,
    max_retries: int = 30,
) -> str | None:
    """Poll every 20s until COMPLETED or FAILED.

    Status flow: CREATED → IN_PROGRESS → COMPLETED | FAILED
    """
    for attempt in range(1, max_retries + 1):
        await asyncio.sleep(interval)  # 20s wait between polls

        try:
            result = await check_status(task_id)
        except Exception as e:
            logger.warning("Freepik poll error (attempt %d): %s", attempt, e)
            continue

        status = result.get("data", {}).get("status", "UNKNOWN")
        logger.info(
            "Freepik poll task=%s attempt=%d/%d status=%s",
            task_id, attempt, max_retries, status,
        )

        if status == "COMPLETED":
            generated = result["data"].get("generated", [])
            if generated:
                return generated[0]
            logger.warning("Freepik COMPLETED but no generated URLs")
            return None
        elif status == "FAILED":
            logger.error("Freepik upscale FAILED for task=%s", task_id)
            return None
        # CREATED, IN_PROGRESS → keep polling

    logger.error("Freepik timed out after %d polls for task=%s", max_retries, task_id)
    return None


async def upscale_image(image_data_url: str) -> str:
    """Full pipeline: submit → poll 20s intervals → return upscaled URL.

    After completion, waits 20s before returning (cooldown for next image).
    """
    task_id = await submit_upscale(image_data_url)
    result_url = await poll_until_complete(task_id)
    if not result_url:
        raise ValueError(f"Freepik upscale failed or timed out (task={task_id})")

    # ── 20s cooldown before next image submission ──
    logger.info("Freepik cooldown: waiting 20s before next submission...")
    await asyncio.sleep(20)

    return result_url
