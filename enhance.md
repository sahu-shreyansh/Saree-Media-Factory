thinking like a system owner, not a script runner



🏗️ Production-Grade Architecture (No Manual Fixes Ever)

You’re using:

Baserow as state store

FastAPI as API host

External AI APIs (Freepik, OpenRouter)

Now we convert this into a robust state machine system.

✅ 1️⃣ Replace “Status-based polling” with a Dispatcher Engine

Instead of:

if Status == Draft → stage_1
if Status == X → stage_2


Use a deterministic stage router:

PIPELINE = {
    "Draft": stage_1,
    "MANNEQUIN_UPSCALED": stage_2,
    "MODEL_GENERATED": stage_3,
    "APPROVED_FOR_ANGLES": stage_4,
    "APPROVED_FOR_VIDEO": stage_5,
}


On each tick:

fetch N rows
for each row:
    route by status


No scattered checks.

Single dispatch brain.

✅ 2️⃣ Add HARD State Transition Rules

Never allow skipping.

Define allowed transitions:

ALLOWED_TRANSITIONS = {
    "Draft": "MANNEQUIN_UPSCALED",
    "MANNEQUIN_UPSCALED": "MODEL_GENERATED",
    ...
}


Before updating status:

if next_status != ALLOWED_TRANSITIONS[current_status]:
    raise InvalidTransition


Prevents corruption forever.

✅ 3️⃣ Add Automatic Retry System (Self-Healing)

Add these fields in Baserow:

retry_count (number)

last_error (text)

last_attempt_at (datetime)

Every stage wrapped in:

MAX_RETRIES = 5

try:
    run_stage()
    retry_count = 0
except Exception as e:
    retry_count += 1
    if retry_count >= MAX_RETRIES:
        mark FAILED_STAGE_X
    else:
        sleep with exponential backoff


Now rows auto-retry.

No manual resets.

✅ 4️⃣ Global API Rate Limiter (Critical)

Use a central semaphore:

FREEPIK_LIMITER = asyncio.Semaphore(1)
OPENROUTER_LIMITER = asyncio.Semaphore(2)


Wrap all external calls.

Prevents 429 permanently.

✅ 5️⃣ Row Locking (Mandatory)

Field:

Processing (boolean)


Before running:

if row["Processing"]:
    return

set Processing = True


After finish/failure:

Processing = False


Prevents double execution.

✅ 6️⃣ Dead-Letter Queue (Auto-Recovery)

Instead of FAILED forever:

Add status:

DEAD_LETTER


If retries exhausted:

→ move to DEAD_LETTER
→ system keeps running for others

Later you can build:

/retry-dead-letter


But no system crash.

✅ 7️⃣ Stage Atomicity (All-or-Nothing)

Example: Stage 1

If 4 mannequins required:

if success_count != 4:
    rollback written fields
    raise StageFailure


Never partially promote.

✅ 8️⃣ Automatic Recovery on Startup

On FastAPI startup:

Scan for rows:

Processing = True


These are crashed mid-run.

Reset:

Processing = False


So system resumes cleanly after restart.

✅ 9️⃣ Parallel Safe Multiple Rows

Instead of:

size=1


Fetch:

size=10


Process in background tasks.

Bound concurrency to safe limits.

Now 20 rows can process smoothly.

✅ 🔟 Centralized Logging + Observability

Log every transition:

Row 23 | Draft → MANNEQUIN_UPSCALED
Row 23 | Retry 2 | Freepik 429
Row 23 | Success


No blind debugging ever again.

🧪 Final End-to-End Stress Test Plan

Do this once system updated.

Test 1 — Multiple rows

Create 5 rows in Draft.
Verify:

All move forward.

No duplicate processing.

No deadlocks.

Test 2 — API Failure Simulation

Temporarily break API key.
Observe:

retries increase

no crash

status eventually FAILED_STAGE_X

system continues processing other rows

Test 3 — Restart During Processing

While a stage running:

Kill server.

Restart.

Verify:

Processing flags reset

Rows resume

No stuck items

Test 4 — High Volume Burst

Add 10 Draft rows.

Verify:

Rate limits respected

No 429 flood

Smooth sequential execution

🎯 What “Production Ready” Actually Means

It means:

Every external failure handled

Every state deterministic

No manual reset required

Crashes recover automatically

No silent data corruption

Concurrency safe

Retry bounded

API costs controlled

Not just “it works once.”


Correct Architecture (Do NOT watermark inside generation prompt)

Never rely on AI model to render text watermark.

Bad:

Prompt: add watermark text "AI Generated"


Unreliable.

Correct approach:

✅ Post-process all generated assets before saving to storage.

🧠 Where To Insert Watermark Layer

Your pipeline currently:

AI generates image
→ Upscale
→ Upload to CDN
→ Save URL to Baserow


Change to:

AI generates image
→ Upscale
→ WATERMARK LAYER
→ Upload to CDN
→ Save URL


Watermark becomes mandatory middleware.

✅ Implementation (Images)

Use Pillow (PIL) in Python.

Install:

pip install pillow


Create:

app/services/watermark.py

Production-safe watermark function:
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

def add_ai_watermark(image_bytes: bytes) -> bytes:
    image = Image.open(BytesIO(image_bytes)).convert("RGBA")

    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    text = "AI GENERATED"
    font_size = int(width * 0.04)

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except:
        font = ImageFont.load_default()

    text_width, text_height = draw.textsize(text, font=font)

    x = width - text_width - 30
    y = height - text_height - 30

    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 140)  # semi-transparent white
    )

    watermarked = Image.alpha_composite(image, overlay)
    output = BytesIO()
    watermarked.convert("RGB").save(output, format="JPEG", quality=95)

    return output.getvalue()

🔄 Integrate into Pipeline

After upscaling:

upscaled_bytes = await upscale(...)
final_bytes = add_ai_watermark(upscaled_bytes)
upload(final_bytes)


Do this for:

model_image

hero_image

all angles

any regenerated image

Now watermark is automatic.

🎥 Watermarking Videos (Production Safe)

Use FFmpeg (best way).

Install:

brew install ffmpeg


Then:

import subprocess

def watermark_video(input_path, output_path):
    subprocess.run([
        "ffmpeg",
        "-i", input_path,
        "-vf", "drawtext=text='AI GENERATED':\
        fontcolor=white@0.6:fontsize=36:\
        x=w-tw-20:y=h-th-20",
        "-codec:a", "copy",
        output_path
    ])


This overlays text bottom-right on every frame.

Then upload output_path.

🔒 Make It Impossible To Skip

Centralize watermarking.

Never call upload directly.

Instead:

def upload_image(image_bytes):
    watermarked = add_ai_watermark(image_bytes)
    return upload_to_storage(watermarked)


Now even future devs can’t bypass it.

🧪 Production Safeguards

Add validation before status promotion:

if not image_contains_watermark(final_bytes):
    raise SecurityError("Watermark missing")


(Advanced optional — can hash pixel area to verify overlay.)


More premium.

📌 Legal Recommendation

Use:

AI GENERATED


or

AI GENERATED CONTENT


NOT just “AI”.

Clear disclosure.

🏆 Final Result

After this:

Every image → always watermarked

Every video → always watermarked

No manual control required

No human error possible

Fully compliant with AI disclosure norms

If you want next level:

✅ Invisible digital watermark (for detection)