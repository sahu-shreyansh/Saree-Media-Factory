"""ImgBB image hosting service.

Uploads base64 image data to ImgBB and returns the hosted URL.
Matches n8n flow: convert image to base64 → POST to ImgBB → get public URL.
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.config import get_settings
from app.services.rate_limiter import IMGBB_LIMITER

logger = logging.getLogger(__name__)

IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"


async def upload_image(image_data: str | bytes, name: str = "saree") -> dict:
    """Upload image to ImgBB.

    Args:
        image_data: Either:
            - base64 string (raw base64 without data: prefix)
            - data URL string (data:image/png;base64,...)
            - raw bytes
        name: Image name for reference

    Returns:
        dict with 'url' key containing the public URL.
    """
    s = get_settings()

    # Convert to raw base64 string for ImgBB
    if isinstance(image_data, bytes):
        b64_str = base64.b64encode(image_data).decode("utf-8")
    elif isinstance(image_data, str):
        if image_data.startswith("data:"):
            # Strip the data:image/...;base64, prefix
            b64_str = image_data.split(",", 1)[1] if "," in image_data else image_data
        else:
            b64_str = image_data
    else:
        raise ValueError(f"Unsupported image_data type: {type(image_data)}")

    async with IMGBB_LIMITER:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                IMGBB_UPLOAD_URL,
                data={
                    "key": s.IMGBB_API_KEY,
                    "image": b64_str,
                    "name": name,
                },
            )
            resp.raise_for_status()
            data = resp.json()

    if data.get("success"):
        url = data["data"]["url"]
        logger.info("ImgBB upload success → %s", url)
        return {"url": url, "data": data["data"]}

    logger.error("ImgBB upload failed: %s", data)
    raise ValueError(f"ImgBB upload failed: {data}")
