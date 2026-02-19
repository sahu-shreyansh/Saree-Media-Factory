"""Centralized API rate limiters — global semaphores for all external APIs.

Prevents 429 errors when processing multiple rows concurrently.
Import these semaphores in each service module to limit concurrent calls.
"""

from __future__ import annotations

import asyncio

# ── Rate Limit Semaphores ─────────────────────────────────────────────
# Each semaphore controls maximum concurrent requests to that API.

FREEPIK_LIMITER = asyncio.Semaphore(1)     # 1 concurrent (rate-limited API)
OPENROUTER_LIMITER = asyncio.Semaphore(2)  # 2 concurrent gen calls
IMGBB_LIMITER = asyncio.Semaphore(3)       # 3 concurrent uploads
KIEAI_LIMITER = asyncio.Semaphore(1)       # 1 video at a time
