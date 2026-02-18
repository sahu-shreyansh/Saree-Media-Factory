# Saree Media Factory (Janardan V2)

An automated AI pipeline that transforms raw mannequin photos of sarees into premium, e-commerce ready model photography and videography. The system uses a multi-stage approach to upscale, generate models, create multiple angles, and produce cinematic product videos.

## 🚀 Core Features

- **Automated Pipeline**: 5-stage workflow from raw input to final video.
- **State Management**: Robust state machine with failure recovery and partial success logic.
- **AI Integration**: Orchestrates Gemini (OpenRouter), Freepik (Upscaling), and Kling (Kie.ai) models.
- **Database**: Built on Baserow for data management, status tracking, and prompted configuration.
- **Stabilization**: Implements exponential backoff, row locking, and status guards for production reliability.

## 🏗️ Architecture

The application is built as a Python-based async service (`app.main`) that polls a Baserow database for pending tasks. A central scheduler dispatches rows to specific pipeline stages based on their `Status`.

### Tech Stack
- **Backend**: Python 3.11+, FastAPI (for webhooks/status), HTTPX (async requests)
- **Database**: Baserow (Self-hosted or SaaS)
- **AI Services**:
  - **OpenRouter**: Google Gemini 3 Pro (Image Generation)
  - **Freepik**: Magnific/Mystic (Image Upscaling)
  - **Kie.ai**: Kling 2.6 (Image-to-Video)
- **Infrastructure**: Docker (Baserow), Local Async Scheduler

## 🔄 The 5-Stage Pipeline

Each stage is an independent module in `app/pipelines/`, guarded by strict status checks.

### Stage 1: Mannequin Upscale
- **Input**: Raw mannequin image (uploaded to Baserow).
- **Process**: Upscales the image 4x using Freepik to enhance fabric details.
- **Output**: `mannequin_front_upscaled`
- **Status**: `Draft` → `MANNEQUIN_UPSCALED`

### Stage 2: Model Generation
- **Input**: Upscaled mannequin image.
- **Process**: Uses Gemini 3 Pro to generate a photorealistic fashion model wearing the saree. Preserves fabric details while generating a matching face, skin tone, and body.
- **Output**: `model_image_upscaled` (Generated & Upscaled)
- **Status**: `MANNEQUIN_UPSCALED` → `HERO_READY`

### Stage 3: Product Generation (Skipped/Merged)
- *Note: Originally planned for description generation, now integrated or skipped in V2 flow.*

### Stage 4: Angle Generation
- **Input**: `model_image_upscaled`
- **Process**: Generates 4 distinct angles using OpenRouter + Freepik:
  1. **Side Profile**: 45-degree view.
  2. **Back View**: Showing blouse and pallu details.
  3. **Closeup**: High-detail shot of fabric/embroidery.
  4. **Movement/Twirl**: Dynamic action shot (handled with partial success logic if safety filters trigger).
- **Output**: 4x High-res Angle Images
- **Status**: `APPROVED_FOR_ANGLES` → `IMAGES_READY`

### Stage 5: Video Generation
- **Input**: `model_image_upscaled`
- **Process**: Sends a detailed cinematic prompt to Kie.ai (Kling model) to animate the model walking, turning, and twirling (10s).
- **Output**: 4K Product Video URL
- **Status**: `APPROVED_FOR_VIDEO` → `PUBLISHED`

## 📂 Project Structure

```
Janardan/
├── app/
│   ├── main.py                 # Application entry point & Scheduler logic
│   ├── config.py               # Environment configuration (Pydantic)
│   ├── core/                   # Core utilities
│   ├── pipelines/              # Stage logic
│   │   ├── stage_1_upscale.py
│   │   ├── stage_2_model_gen.py
│   │   ├── stage_4_angles.py
│   │   └── stage_5_video.py
│   └── services/               # External API integrations
│       ├── baserow.py          # Database operations
│       ├── openrouter.py       # Gemini generation
│       ├── freepik.py          # Upscaling service
│       ├── kieai.py            # Video generation
│       ├── imgbb.py            # Image hosting
│       └── state_machine.py    # Status enums & transitions
├── scripts/                    # Utility scripts (Verification, Setup)
├── tests/                      # Unit tests
├── .env                        # Secrets & Config
└── requirements.txt            # Dependencies
```

## ⚙️ Setup & Installation

1. **Prerequisites**:
   - Python 3.11+
   - Baserow account (or local instance)
   - API Keys: OpenRouter, Freepik, Kie.ai, ImgBB

2. **Installation**:
   ```bash
   git clone <repo>
   cd Janardan
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configuration**:
   - Copy `.env.example` to `.env`.
   - Fill in all API keys and Baserow Table IDs.

4. **Running**:
   ```bash
   # Start the scheduler & API
   python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

## 🛡️ Reliability Features

- **Row Locking**: prevents race conditions by marking rows as `Processing=true`.
- **Status Guards**: Each stage verifies the row is in the correct start state status before executing.
- **Exponential Backoff**: Freepik and API calls retry with increasing delays to handle rate limits.
- **Partial Success**: Stage 4 allows the pipeline to proceed even if the "Movement" angle triggers safety filters, preventing blockers.
