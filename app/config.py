"""Application configuration — loads from .env via pydantic-settings."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """All environment variables for the Saree Workflow Automation."""

    # ── Baserow ───────────────────────────────────
    BASEROW_URL: str = "http://localhost"  # e.g. http://baserow.example.com
    BASEROW_TOKEN: str = ""  # Database token from Baserow settings
    BASEROW_POSTS_TABLE_ID: int = 0  # "Posts" table (Social Media Post Management)
    BASEROW_CLIENT_HUB_TABLE_ID: int = 0  # "Posts" table (Client Content Hub)
    BASEROW_FABRIC_ANALYSIS_TABLE_ID: int = 0  # "fabric_analysis" table
    BASEROW_PROMPTS_TABLE_ID: int = 0  # "Prompts" table (Configuration)

    # ── Freepik Upscaler ──────────────────────────────────────────────
    FREEPIK_API_KEY: str = ""
    FREEPIK_SCALE_FACTOR: int = 4
    FREEPIK_SHARPEN: int = 7
    FREEPIK_SMART_GRAIN: int = 7
    FREEPIK_ULTRA_DETAIL: int = 30

    # ── OpenRouter (Gemini image generation) ──────────────────────────
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "google/gemini-3-pro-image-preview"

    # ── ImgBB (image hosting) ─────────────────────────────────────────
    IMGBB_API_KEY: str = ""

    # ── Kie.ai (video generation) ─────────────────────────────────────
    KIEAI_API_KEY: str = ""
    KIEAI_MODEL: str = "kling-2.6/image-to-video"
    KIEAI_VIDEO_DURATION: str = "10"

    # ── App settings ──────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_DEBUG: bool = False
    POLL_INTERVAL_SECONDS: int = 60
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
