# Janardan — Saree Media Factory

Automated AI-powered media pipeline for premium Indian saree e-commerce. Transforms raw mannequin photos into professional model images, multi-angle shots, and product videos — fully automated.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Server                        │
│  ┌─────────────┐  ┌──────────┐  ┌────────────────────┐ │
│  │  Scheduler   │  │ REST API │  │  Startup Recovery  │ │
│  │  (polling)   │  │ /health  │  │  (stuck row reset) │ │
│  └──────┬───────┘  └──────────┘  └────────────────────┘ │
│         │                                                │
│  ┌──────▼────────────────────────────────────────────┐  │
│  │          Dispatcher Engine (DISPATCH_MAP)          │  │
│  │  Draft → Stage 1 → Stage 2 → Stage 4 → Stage 5   │  │
│  └──────┬────────────────────────────────────────────┘  │
│         │                                                │
│  ┌──────▼──────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ Auto-Retry  │  │ Rate Limiter│  │  Row Locking   │  │
│  │ (exp. back) │  │ (semaphores)│  │  (Processing)  │  │
│  └─────────────┘  └─────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │
    ┌────▼────────────────────────────┐
    │        External APIs            │
    │  Freepik · OpenRouter · ImgBB   │
    │  Kie.ai · Baserow               │
    └─────────────────────────────────┘
```

## Pipeline Stages

| Stage | Name | Input | Output | API |
|-------|------|-------|--------|-----|
| 1 | Mannequin Upscale | 4 raw saree photos | 4 upscaled images | Freepik |
| 2 | Model Generation | 4 upscaled images | AI model wearing saree | OpenRouter (Gemini) |
| 4 | Angle Generation | Model image | Side, Back, Closeup, Movement | OpenRouter (Gemini) |
| 5 | Video Generation | Best angle image | Product video | Kie.ai (Kling 2.6) |

## Status Flow

```
Draft → UPSCALING_MANNEQUIN → MANNEQUIN_UPSCALED
     → GENERATING_MODEL → MODEL_GENERATED → UPSCALING_MODEL → MODEL_UPSCALED
     → GENERATING_HERO → HERO_READY
     → APPROVED_FOR_ANGLES (manual) → GENERATING_ANGLES → IMAGES_READY
     → APPROVED_FOR_VIDEO (manual) → GENERATING_VIDEO → PUBLISHED

Failures: FAILED_STAGE_X → auto-retry (5x) → DEAD_LETTER
```

## Production Features

- **Auto-Retry**: Failed stages retry with exponential backoff (max 5 attempts)
- **Dead-Letter Queue**: Permanently failed rows move to `DEAD_LETTER` for manual review
- **Startup Recovery**: Resets stuck `Processing=true` rows on server boot
- **Parallel Processing**: Up to 3 rows processed concurrently per scheduler tick
- **Rate Limiting**: Centralized semaphores per API (Freepik: 1, OpenRouter: 2, ImgBB: 3, Kie.ai: 1)
- **Row Locking**: `Processing` boolean prevents double execution
- **State Machine**: Hard transition rules enforce valid status changes
- **Watermarking**: "AI GENERATED" overlay on all public-facing final images (Pillow) and videos (FFmpeg)

## Project Structure

```
app/
├── main.py                 # FastAPI app, scheduler, dispatcher, API endpoints
├── config.py               # Environment configuration (pydantic-settings)
├── pipelines/
│   ├── stage_1_upscale.py  # Mannequin image upscaling
│   ├── stage_2_model_gen.py # AI model image generation
│   ├── stage_4_angles.py   # Multi-angle shot generation
│   └── stage_5_video.py    # Product video generation
└── services/
    ├── baserow.py           # Baserow API client (state store)
    ├── freepik.py           # Freepik upscaler API
    ├── openrouter.py        # OpenRouter/Gemini image generation
    ├── imgbb.py             # ImgBB image hosting
    ├── kieai.py             # Kie.ai video generation
    ├── image_utils.py       # Image format conversion utilities
    ├── state_machine.py     # Status enum, transitions, dispatch map
    ├── rate_limiter.py      # Global API semaphores
    ├── retry.py             # Auto-retry engine with exponential backoff
    └── watermark.py         # AI watermark for images (Pillow) and videos (FFmpeg)

scripts/
├── setup_db.py             # Baserow schema setup & migration
├── verify_imports.py       # Import health check
└── monitor_test.py         # Live monitoring script
```

## Setup

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (for Baserow)
- FFmpeg (for video watermarking): `brew install ffmpeg`

### 1. Start Baserow

```bash
docker compose up -d
```

### 2. Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Setup Database

**First time (fresh install):**
```bash
python3 scripts/setup_db.py fresh
```

**Existing database (add missing fields/options):**
```bash
python3 scripts/setup_db.py migrate --table-id YOUR_TABLE_ID
```

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 5. Run the Server

```bash
python3 -m app.main
```

The server starts at `http://localhost:8000` with auto-polling every 60 seconds.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check with feature list |
| `GET` | `/pipeline/status` | All rows grouped by status |
| `POST` | `/trigger/{row_id}/{stage}` | Manually trigger a pipeline stage |
| `POST` | `/retry-dead-letter/{row_id}` | Retry a dead-lettered row |
| `GET` | `/docs` | Swagger UI |

## Baserow Schema (Posts Table)

| Field | Type | Purpose |
|-------|------|---------|
| `Name` | text | Product name (primary) |
| `Saree Front/Closer/Border/Pallu` | file | Input mannequin photos |
| `Status` | single_select | Pipeline status (23 options) |
| `Processing` | boolean | Row lock flag |
| `mannequin_*_upscaled` | url | Stage 1 outputs (4 fields) |
| `model_image_raw/upscaled` | url | Stage 2 outputs |
| `angle_*_final` | url | Stage 4 outputs (4 fields) |
| `video_url` | url | Stage 5 output |
| `retry_count` | number | Auto-retry counter |
| `error_message` | long_text | Current error |
| `last_error` | long_text | Last retry error |
| `last_attempt_at` | text | Last retry timestamp (ISO) |

## Environment Variables

See [`.env.example`](.env.example) for all configuration options.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BASEROW_TOKEN` | ✅ | — | Database API token |
| `BASEROW_POSTS_TABLE_ID` | ✅ | — | Posts table ID |
| `FREEPIK_API_KEY` | ✅ | — | Freepik upscaler key |
| `OPENROUTER_API_KEY` | ✅ | — | OpenRouter/Gemini key |
| `IMGBB_API_KEY` | ✅ | — | ImgBB hosting key |
| `KIEAI_API_KEY` | ✅ | — | Kie.ai video gen key |
| `MAX_RETRIES` | ❌ | `5` | Retries before dead-letter |
| `MAX_CONCURRENT_ROWS` | ❌ | `3` | Parallel row processing |
| `POLL_INTERVAL_SECONDS` | ❌ | `60` | Scheduler poll interval |
