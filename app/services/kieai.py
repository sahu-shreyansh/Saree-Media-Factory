"""Kie.ai video generation service (Kling model).

Flow:
  1. POST /api/v1/jobs/createTask  → returns {data: {taskId}}
  2. GET  /api/v1/jobs/recordInfo?taskId=...  → poll until complete
     On completion:  data.resultJson contains JSON with resultUrls[]
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

KIEAI_BASE = "https://api.kie.ai/api/v1/jobs"

# The video prompt from the n8n workflow node "Video Gen9"
VIDEO_PROMPT = (
    "Premium product video of EXACT MODEL from reference wearing EXACT SAME saree. "
    "CRITICAL: Complete continuity - same face, styling, saree design throughout. "
    "Single continuous showcase. SEQUENCE (10s): [0-2s] Static front view, full-length, "
    "confident smile, establishing shot. [2-4s] 2-3 graceful steps forward, fabric flowing "
    "naturally, pleats swaying. [4-6s] Smooth 360° rotation - side profile, hip fit, back "
    "view with blouse and pallu, return to front. Slow elegant rotation. [6-8s] Lift and "
    "extend pallu showing border, embroidery, zari. Hand natural and elegant. Close-ups on "
    "craftsmanship. [8-10s] Graceful 360° twirl, fabric extending outward, arms elegant, "
    "ending front-facing. SAREE: Exact match throughout - same color, texture, border, "
    "pallu pattern. NO changes. CAMERA: Smooth cinematic - static wide open, gentle zoom "
    "during walk, slow pan following rotation, close-ups on details, pull back for twirl. "
    "Sharp focus, soft background bokeh. LIGHTING: 3-point studio - key 45°, fill even, "
    "rim highlighting fabric. Warm 3500K. Reveals zari, sequins, embroidery depth. "
    "BACKGROUND: Warm gradient (honey beige to soft gold) matching reference. Consistent "
    "throughout. STYLING: Same face, makeup, hair, jewelry as reference. Complete continuity. "
    "FABRIC: Natural realistic movement - silk weight, pleats sway, pallu flows, authentic "
    "draping. OUTPUT: Premium e-commerce videography, smooth transitions, 4K, cinematic "
    "grading, warm heritage aesthetic. SAME MODEL, SAME SAREE throughout 10s."
)


async def create_video_task(image_urls: list[str], prompt: str | None = None) -> str | None:
    """Create a Kie.ai video generation task. Returns taskId."""
    s = get_settings()
    headers = {
        "Authorization": f"Bearer {s.KIEAI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    # User spec: "image_urls": [ "{{ $json.frontImage }}" ] (implies single image)
    # We'll pass whatever list is provided, but typically expected to be length 1 for this prompt.
    
    body = {
        "model": s.KIEAI_MODEL,
        "callBackUrl": "https://your-domain.com/api/callback",
        "input": {
            "prompt": prompt or VIDEO_PROMPT,
            "image_urls": image_urls,
            "sound": False,
            "duration": s.KIEAI_VIDEO_DURATION,
            "aspect_ratio": "9:16",
            "cfg_scale": 0.5,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{KIEAI_BASE}/createTask", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    task_id = data.get("data", {}).get("taskId")
    if task_id:
        logger.info("Kie.ai video task created → taskId=%s", task_id)
    else:
        logger.error("Kie.ai createTask response missing taskId: %s", data)
    return task_id


async def check_task(task_id: str) -> dict:
    """Check the status of a Kie.ai video task."""
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.KIEAI_API_KEY}"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{KIEAI_BASE}/recordInfo",
            headers=headers,
            params={"taskId": task_id},
        )
        resp.raise_for_status()
        return resp.json()


async def poll_until_complete(
    task_id: str,
    *,
    interval: int = 150,
    max_retries: int = 5,
) -> str | None:
    """Poll Kie.ai until video is ready. Returns the video URL or None."""
    for attempt in range(1, max_retries + 1):
        await asyncio.sleep(interval)
        result = await check_task(task_id)
        data = result.get("data", {})
        logger.info("Kie.ai poll taskId=%s attempt=%d", task_id, attempt)

        result_json_str = data.get("resultJson")
        if result_json_str:
            try:
                parsed = json.loads(result_json_str)
                urls = parsed.get("resultUrls", [])
                if urls:
                    logger.info("Kie.ai video ready → %s", urls[0])
                    return urls[0]
            except json.JSONDecodeError:
                logger.warning("Kie.ai resultJson not valid JSON: %s", result_json_str)

    logger.error("Kie.ai video timed out after %d attempts for taskId=%s", max_retries, task_id)
    return None
