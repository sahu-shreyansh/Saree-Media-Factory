"""OpenRouter / Gemini image generation service.

Handles:
  1. Stage 2: Front model image (4 mannequin reference images)
  2. Stage 4: Angle images (uses model_image_upscaled as single reference)

Flow (matches n8n):
  POST to OpenRouter → response.message.images[] → data:image/png;base64,...
"""

from __future__ import annotations

import base64
import logging
import re

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ═══════════════════════════════════════════════════════════════════════
# PROMPTS — extracted exactly from n8n workflow / image_prompt.md
# ═══════════════════════════════════════════════════════════════════════

# Stage 2: Front model image (uses 4 mannequin reference images)
SAREE_MODEL_PROMPT = (
    "Create a professional e-commerce and realistic catalog image for premium Indian saree marketplace. "
    "Model: Indian female, age 25-32, professional appearance, standing straight facing camera. "
    "Pose: Front-facing full-length view, feet shoulder-width apart, right hand on waist, "
    "left hand gently holding pallu or relaxed. Expression: Soft welcoming smile, natural and confident. "
    "Styling: Minimal makeup with warm undertones, hair in low bun or side braid, subtle gold jewelry "
    "(small earrings, thin necklace). "
    "Saree Details: The saree must match exactly from the 4 reference images - "
    "Image 1 shows base color and overall design, Image 2 shows fabric texture and weave pattern, "
    "Image 3 shows border design with all motifs and zari work, Image 4 shows pallu pattern with "
    "exact spacing and embellishments. Pay forensic attention to colors, patterns, border work, "
    "and fabric texture. NO invented elements or creative modifications. "
    "Background: Professional studio setup with warm gradient backdrop. "
    "If saree is light-colored (white, cream, pastel, light pink, baby blue), use soft gradient "
    "background with warm beige, soft peach, or earthy tones for contrast. "
    "If saree is dark-colored (black, navy blue, maroon, dark green), use clean white or soft "
    "light grey background. Background creates honey beige to soft gold gradient effect, lighter "
    "in center, warmer at edges. "
    "Lighting: Three-point studio lighting setup - Key light at 45 degree angle with softbox "
    "diffusion for dimensional facial contours, Fill light on opposite side at 60% power for "
    "even skin tone, Rim light from behind at 135 degrees to separate model from background "
    "and highlight silk sheen. Warm color temperature 3200-4000K. Soft flattering light on face "
    "with no harsh shadows. Lighting reveals silk texture depth, zari metallic shimmer, and "
    "embroidery relief. Balanced exposure with no overexposure on face or fabric. "
    "Composition: Full-length vertical frame showing complete saree from head to toe, "
    "Eye-level camera height with centered model and proper headroom, Sharp focus across "
    "entire saree and face, Shallow depth of field (f/2.8 to f/4) with soft background bokeh, "
    "4K resolution zoom-ready for product inspection. "
    "Output Quality: Hyper-realistic studio photoshoot aesthetic, Natural skin texture not "
    "over-retouched, Silk fabric with authentic material physics, Premium marketplace standard "
    "matching Myntra Ajio Tata CLiQ quality, Warm heritage luxury feel matching 60+ year silk "
    "brand positioning."
)

# Stage 4: Angle prompts (each uses model_image_upscaled as single reference)
ANGLE_PROMPTS: dict[str, dict] = {
    "side": {
        "prompt": (
            "Generate SIDE PROFILE VIEW (45° angle) of SAME MODEL from reference wearing EXACT SAME saree. "
            "This is different camera angle from same photoshoot session. "
            "CRITICAL: Identical model features - same face, age, skin tone, body type, styling. "
            "Model at 45° showing right side, face turned towards camera, soft smile. "
            "Right hand holding pallu near shoulder, left relaxed. "
            "FOCUS: Pallu cascading down back (full design visible), side silhouette, border along edge, fabric drape physics. "
            "SAREE: Exact match to reference - same colors, texture, border motifs, pallu pattern. View from side but ALL details identical. "
            "STYLING: Same makeup, same hairstyle (visible from side), same jewelry (jhumkas prominent). "
            "BACKGROUND: Same warm gradient (honey beige to soft gold). "
            "LIGHTING: 3-point studio - key 45° on face, fill 60%, rim behind highlighting pallu. Warm 3500K. "
            "COMPOSITION: Full-length 45° angle, sharp focus, shallow DOF, 4K. "
            "OUTPUT: Frame 2 of same shoot - different angle, SAME MODEL, SAME SAREE, SAME SESSION."
        ),
        "temperature": 0.15,
    },
    "back": {
        "prompt": (
            "Generate BACK VIEW of SAME MODEL from reference wearing EXACT SAME saree. "
            "This is different angle from same photoshoot session. "
            "CRITICAL: Identical hair, body type, skin tone, styling. "
            "Model back to camera, head slightly turned (30° side profile), elegant expression. "
            "Hands relaxed at sides or one touching hair naturally. "
            "FOCUS: Complete back drape neck-to-floor, pallu cascading down back (full design visible), "
            "blouse back and neckline clearly shown, back border visible, fabric fall physics. "
            "SAREE: Exact match to reference - same colors, texture, border motifs, pallu pattern. Back view but ALL details identical. "
            "STYLING: Same hairstyle from back, same blouse design, same jewelry (earrings/necklace visible). "
            "BACKGROUND: Same warm gradient (honey beige to soft gold). "
            "LIGHTING: 3-point studio - key from front-side, fill for even back, rim from above-behind highlighting hair and pallu edge. Warm 3500K. "
            "COMPOSITION: Full-length back view, eye level, sharp focus, shallow DOF, 4K. "
            "OUTPUT: Frame 3 of same shoot - back angle, SAME MODEL, SAME SAREE, SAME SESSION."
        ),
        "temperature": 0.15,
    },
    "closeup": {
        "prompt": (
            "Generate EXTREME CLOSE-UP DETAIL VIEW of SAME MODEL from reference wearing EXACT SAME saree. "
            "Macro detail shot from same photoshoot session. "
            "CRITICAL: Identical face, skin tone, makeup, styling. "
            "FRAMING: Upper body close-up (waist to face), tight crop on intricate details. "
            "FOCUS: Fabric texture at thread level, embroidery magnified (zari, stitches visible), "
            "embellishments close-up (sequins/beads/stones individual), print patterns ink-sharp, "
            "border craftsmanship, unique artisanal elements. Hand gracefully near detail area highlighting work. "
            "SAREE: Forensic macro accuracy - weave thread-by-thread, embroidery exact stitch count, "
            "embellishment sequin-by-sequin placement, color perfect under magnification, print motifs sharp. NO modifications. "
            "STYLING: Same face/expression, same makeup in close-up, same jewelry if visible. "
            "BACKGROUND: Same warm gradient (honey beige to soft gold), soft bokeh blur at close-up. "
            "LIGHTING: Macro setup - key 45° diffused, fill even, rim highlighting embroidery relief and bead facets. Warm 3500K. "
            "Reveals every thread/bead clearly. "
            "COMPOSITION: Close-up vertical waist-to-face, razor-sharp focus on details, shallow DOF f/1.8-f/2.8, "
            "extreme bokeh, macro clarity (threads countable), 4K zoom-ready. "
            "OUTPUT: Detail shot of same shoot - macro view, SAME MODEL, SAME SAREE, SAME SESSION, extreme craftsmanship showcase."
        ),
        "temperature": 0.15,
    },
    "movement": {
        "prompt": (
            "Generate DYNAMIC TWIRL/MOVEMENT SHOT of SAME MODEL from reference wearing EXACT SAME saree. "
            "Action shot from same photoshoot session. "
            "CRITICAL: Identical face, skin tone, body type, styling. "
            "MOVEMENT: Model gracefully twirling mid-spin. Pallu and pleats spreading outward flowing in air. "
            "Fabric extending showing natural motion physics. Arms gracefully extended for balance. Hair flowing with movement. "
            "Head in profile/three-quarter view during rotation. "
            "EXPRESSION: Joyful natural smile, bright eyes, genuine happiness captured. "
            "MOTION: Fabric motion blur on flowing areas (pallu/pleats) while model sharp. "
            "Pallu creating arc pattern. Pleats fanning out. Border visible on edges. Natural fluid movement at peak twirl moment. "
            "SAREE: Exact match to reference in motion - same color across fabric, same texture/sheen (silk flow), "
            "same border on moving edges, same pallu pattern recognizable, same embellishments. NO modifications. "
            "STYLING: Same face/expression (joyful), same makeup, hairstyle flowing (bun loosening/braid swaying), jewelry moving naturally. "
            "BACKGROUND: Same warm gradient (honey beige to soft gold), slightly blurred from motion, radial blur emphasizing spin. "
            "LIGHTING: High-speed studio - key 45° fast flash, fill even, multiple rims on fabric edges and hair. Warm 3500K. "
            "Freezes face/body, motion blur on fabric. "
            "COMPOSITION: Full-length dynamic frame, fabric spread visible, sharp focus on face/torso with elegant fabric motion blur, "
            "f/4-f/5.6, peak twirl moment, 4K. "
            "OUTPUT: Dynamic shot of same shoot - movement captured, SAME MODEL, SAME SAREE, SAME SESSION, fabric flow celebration."
        ),
        "temperature": 0.18,
    },
}


# ═══════════════════════════════════════════════════════════════════════
# IMAGE EXTRACTION (shared by all generators)
# ═══════════════════════════════════════════════════════════════════════

async def _extract_image_from_response(data: dict) -> str | None:
    """Extract generated image from OpenRouter/Gemini response.

    Checks (in order):
      0. message.images[] — most common for Gemini image gen
      1. message.content[] — multipart with inline_data or image_url
      2. message.content (string) — embedded base64 or raw base64
    """
    try:
        message = data["choices"][0]["message"]

        # Method 0: message.images[]
        images = message.get("images", [])
        if images:
            for img in images:
                if isinstance(img, dict):
                    url = img.get("url", "") or img.get("image_url", {}).get("url", "")
                    if url:
                        if url.startswith("data:image"):
                            logger.info("OpenRouter: Got image via message.images[] data URL (len=%d)", len(url))
                            return url
                        elif url.startswith("http"):
                            logger.info("OpenRouter: Got hosted image URL via message.images[]: %s", url[:80])
                            async with httpx.AsyncClient(timeout=60) as dl_client:
                                dl_resp = await dl_client.get(url)
                                dl_resp.raise_for_status()
                                img_bytes = dl_resp.content
                                b64 = base64.b64encode(img_bytes).decode("utf-8")
                                data_url = f"data:image/png;base64,{b64}"
                                logger.info("OpenRouter: Downloaded and converted (len=%d)", len(data_url))
                                return data_url
                    b64_data = img.get("data", "") or img.get("b64_json", "")
                    if b64_data:
                        mime = img.get("mime_type", "image/png")
                        data_url = f"data:{mime};base64,{b64_data}"
                        logger.info("OpenRouter: Got image via message.images[] base64 (len=%d)", len(data_url))
                        return data_url
                elif isinstance(img, str):
                    if img.startswith("data:image"):
                        logger.info("OpenRouter: Got image string via message.images[] (len=%d)", len(img))
                        return img
                    elif img.startswith("http"):
                        async with httpx.AsyncClient(timeout=60) as dl_client:
                            dl_resp = await dl_client.get(img)
                            dl_resp.raise_for_status()
                            img_bytes = dl_resp.content
                            b64 = base64.b64encode(img_bytes).decode("utf-8")
                            data_url = f"data:image/png;base64,{b64}"
                            logger.info("OpenRouter: Downloaded string URL (len=%d)", len(data_url))
                            return data_url

        # Method 1: multipart content
        if isinstance(message.get("content"), list):
            for part in message["content"]:
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:image"):
                        logger.info("OpenRouter: Got image via multipart content (len=%d)", len(url))
                        return url
                if part.get("type") == "inline_data":
                    mime = part.get("mime_type", "image/png")
                    b64 = part.get("data", "")
                    data_url = f"data:{mime};base64,{b64}"
                    logger.info("OpenRouter: Got image via inline_data (len=%d)", len(data_url))
                    return data_url

        # Method 2: string content
        content = message.get("content", "")
        if isinstance(content, str):
            match = re.search(r'(data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=]+)', content)
            if match:
                data_url = match.group(1)
                logger.info("OpenRouter: Got image via regex in text (len=%d)", len(data_url))
                return data_url
            if len(content) > 1000 and not content.startswith("{"):
                data_url = f"data:image/png;base64,{content.strip()}"
                logger.info("OpenRouter: Treating raw response as base64 (len=%d)", len(data_url))
                return data_url

        logger.error("OpenRouter: Could not extract image. Response keys: %s", list(data.keys()))
        logger.error("OpenRouter: Message keys: %s", list(message.keys()))
        logger.error("OpenRouter: Content preview: %s", str(message.get("content", ""))[:500])
        return None

    except (KeyError, IndexError) as exc:
        logger.error("OpenRouter: Failed to parse response: %s — %s", exc, str(data)[:500])
        return None


# ═══════════════════════════════════════════════════════════════════════
# STAGE 2: Front model image (4 mannequin references)
# ═══════════════════════════════════════════════════════════════════════

async def generate_model_image(
    front_url: str,
    closer_url: str,
    border_url: str,
    pallu_url: str,
) -> str | None:
    """Send 4 reference images to Gemini and get a generated model image.

    Returns the base64 data-URL (data:image/png;base64,...) or None on failure.
    """
    s = get_settings()
    headers = {
        "Authorization": f"Bearer {s.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": s.OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": SAREE_MODEL_PROMPT},
                    {"type": "image_url", "image_url": {"url": front_url}},
                    {"type": "image_url", "image_url": {"url": closer_url}},
                    {"type": "image_url", "image_url": {"url": border_url}},
                    {"type": "image_url", "image_url": {"url": pallu_url}},
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
        "route": "fallback",
    }

    logger.info("OpenRouter: Sending 4 images to %s (front model)...", s.OPENROUTER_MODEL)

    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return await _extract_image_from_response(data)


# ═══════════════════════════════════════════════════════════════════════
# STAGE 4: Angle images (single reference = model_image_upscaled)
# ═══════════════════════════════════════════════════════════════════════

async def generate_angle_image(
    angle_type: str,
    reference_image_url: str,
) -> str | None:
    """Generate a specific angle view using model_image_upscaled as reference.

    Args:
        angle_type: One of 'side', 'back', 'closeup', 'movement'
        reference_image_url: The model_image_upscaled URL from Stage 2

    Returns the base64 data-URL or None on failure.
    """
    angle_config = ANGLE_PROMPTS.get(angle_type)
    if not angle_config:
        logger.error("OpenRouter: Unknown angle type '%s'", angle_type)
        return None

    s = get_settings()
    headers = {
        "Authorization": f"Bearer {s.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": s.OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": angle_config["prompt"]},
                    {"type": "image_url", "image_url": {"url": reference_image_url}},
                ],
            }
        ],
        "temperature": angle_config["temperature"],
        "max_tokens": 2048,
        "route": "fallback",
    }

    logger.info("OpenRouter: Generating %s angle via %s...", angle_type, s.OPENROUTER_MODEL)

    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return await _extract_image_from_response(data)
