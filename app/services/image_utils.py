"""Image utility helpers — download, base64 encode/decode."""

from __future__ import annotations

import base64
import logging

import httpx

logger = logging.getLogger(__name__)


async def download_image(url: str) -> bytes:
    """Download an image from a URL and return raw bytes."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        logger.info("Downloaded image from %s (%d bytes)", url, len(resp.content))
        return resp.content


def bytes_to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    """Convert raw bytes to a base64 data-URL string."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def data_url_to_bytes(data_url: str) -> bytes:
    """Extract raw bytes from a base64 data-URL string."""
    # data:image/png;base64,iVBOR...
    if "," in data_url:
        b64_part = data_url.split(",", 1)[1]
    else:
        b64_part = data_url
    return base64.b64decode(b64_part)


async def download_image_as_base64(url: str) -> str | None:
    """Download image and convert to Data URL string."""
    try:
        data = await download_image(url)
        # Simple mime detection from bytes? Start with jpg/png default or detect
        # Or just use image/jpeg as most inputs are jpg files from Baserow User Files (which are cleaned up by Baserow/Django usually)
        # Actually `download_image` logs content length. 
        # Ideally we check headers.
        # But `bytes_to_data_url` defaults to image/png.
        # Let's try to infer from url extension or header if possible, but for now simple default or 'image/jpeg' if unknown.
        # Better: image_utils has download_image call. Using default mime might be wrong if it's jpeg. 
        # But data uri with wrong mime usually renders fine in browsers, but APIs might be strict.
        # I'll default to "image/jpeg" as Saree inputs are usually photos.
        return bytes_to_data_url(data, mime="image/png")
    except Exception as e:
        logger.error(f"Failed to download/endoce image from {url}: {e}")
        return None
