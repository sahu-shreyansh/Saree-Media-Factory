"""Image and video watermarking — "AI GENERATED" overlay.

All generated images are watermarked before final upload.
Videos are watermarked via FFmpeg after download from Kie.ai.

Usage:
    from app.services.watermark import add_ai_watermark, watermark_video

    watermarked_bytes = add_ai_watermark(image_bytes)
    watermarked_video_path = await watermark_video(video_url)
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────

WATERMARK_TEXT = "AI GENERATED"
WATERMARK_OPACITY = 140       # 0-255 (semi-transparent)
WATERMARK_SIZE_RATIO = 0.035  # Text size = 3.5% of image width
WATERMARK_MARGIN = 30         # Pixels from bottom-right corner


# ── Image Watermarking ────────────────────────────────────────────────

def add_ai_watermark(image_bytes: bytes, text: str = WATERMARK_TEXT) -> bytes:
    """Add semi-transparent watermark text to bottom-right of image.

    Args:
        image_bytes: Raw image bytes (JPEG/PNG)
        text: Watermark text (default: "AI GENERATED")

    Returns:
        Watermarked image as JPEG bytes (quality=95)
    """
    image = Image.open(BytesIO(image_bytes)).convert("RGBA")
    width, height = image.size

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(int(width * WATERMARK_SIZE_RATIO), 16)

    try:
        # Try system fonts (macOS)
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

    # Get text bounding box
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = width - text_w - WATERMARK_MARGIN
    y = height - text_h - WATERMARK_MARGIN

    # Draw with semi-transparent white + subtle shadow
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 80))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, WATERMARK_OPACITY))

    watermarked = Image.alpha_composite(image, overlay)
    output = BytesIO()
    watermarked.convert("RGB").save(output, format="JPEG", quality=95)

    logger.info("Watermarked image (%dx%d, text='%s')", width, height, text)
    return output.getvalue()


# ── Video Watermarking ────────────────────────────────────────────────

async def watermark_video(input_path: str, output_path: str | None = None) -> str:
    """Add "AI GENERATED" text overlay to video using FFmpeg.

    Args:
        input_path: Path to input video file
        output_path: Path for output. If None, creates in temp directory.

    Returns:
        Path to watermarked video file
    """
    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", (
            f"drawtext=text='{WATERMARK_TEXT}':"
            "fontcolor=white@0.5:fontsize=36:"
            "x=w-tw-20:y=h-th-20:"
            "borderw=1:bordercolor=black@0.3"
        ),
        "-codec:a", "copy",
        "-preset", "fast",
        output_path,
    ]

    logger.info("Watermarking video: %s → %s", input_path, output_path)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error = stderr.decode()[:500]
        logger.error("FFmpeg failed (rc=%d): %s", proc.returncode, error)
        raise RuntimeError(f"FFmpeg watermark failed: {error}")

    logger.info("Video watermarked: %s (%.1f MB)", output_path, os.path.getsize(output_path) / 1e6)
    return output_path
