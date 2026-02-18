"""State machine for product processing — V3 Schema.

Pipeline flow:
  Draft → UPSCALING_MANNEQUIN → MANNEQUIN_UPSCALED
  → GENERATING_MODEL → MODEL_GENERATED → UPSCALING_MODEL → MODEL_UPSCALED
  → GENERATING_HERO → HERO_READY
  → APPROVED_FOR_ANGLES (manual)
  → GENERATING_ANGLES → ANGLES_GENERATED → UPSCALING_ANGLES → IMAGES_READY
  → APPROVED_FOR_VIDEO (manual)
  → GENERATING_VIDEO → PUBLISHED

Failure statuses per stage allow human triage and retry.
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class ProductStatus(str, Enum):
    DRAFT = "Draft"
    MANNEQUIN_UPLOADED = "MANNEQUIN_UPLOADED"
    UPSCALING_MANNEQUIN = "UPSCALING_MANNEQUIN"
    MANNEQUIN_UPSCALED = "MANNEQUIN_UPSCALED"
    GENERATING_MODEL = "GENERATING_MODEL"
    MODEL_GENERATED = "MODEL_GENERATED"
    UPSCALING_MODEL = "UPSCALING_MODEL"
    MODEL_UPSCALED = "MODEL_UPSCALED"
    GENERATING_HERO = "GENERATING_HERO"
    HERO_READY = "HERO_READY"
    APPROVED_FOR_ANGLES = "APPROVED_FOR_ANGLES"
    GENERATING_ANGLES = "GENERATING_ANGLES"
    ANGLES_GENERATED = "ANGLES_GENERATED"
    UPSCALING_ANGLES = "UPSCALING_ANGLES"
    IMAGES_READY = "IMAGES_READY"
    APPROVED_FOR_VIDEO = "APPROVED_FOR_VIDEO"
    GENERATING_VIDEO = "GENERATING_VIDEO"
    PUBLISHED = "PUBLISHED"
    # Per-stage failures
    FAILED_STAGE_1 = "FAILED_STAGE_1"
    FAILED_STAGE_2 = "FAILED_STAGE_2"
    FAILED_STAGE_ANGLES = "FAILED_STAGE_ANGLES"
    FAILED_STAGE_VIDEO = "FAILED_STAGE_VIDEO"


# Valid transitions: current → [allowed targets]
VALID_TRANSITIONS: dict[str, list[str]] = {
    "Draft":                ["UPSCALING_MANNEQUIN", "FAILED_STAGE_1"],
    "UPSCALING_MANNEQUIN":  ["MANNEQUIN_UPSCALED", "FAILED_STAGE_1"],
    "MANNEQUIN_UPSCALED":   ["GENERATING_MODEL", "FAILED_STAGE_2"],
    "GENERATING_MODEL":     ["MODEL_GENERATED", "FAILED_STAGE_2"],
    "MODEL_GENERATED":      ["UPSCALING_MODEL", "FAILED_STAGE_2"],
    "UPSCALING_MODEL":      ["MODEL_UPSCALED", "FAILED_STAGE_2"],
    "MODEL_UPSCALED":       ["GENERATING_HERO", "FAILED_STAGE_2"],
    "GENERATING_HERO":      ["HERO_READY", "FAILED_STAGE_2"],
    "HERO_READY":           ["APPROVED_FOR_ANGLES"],
    "APPROVED_FOR_ANGLES":  ["GENERATING_ANGLES", "FAILED_STAGE_ANGLES"],
    "GENERATING_ANGLES":    ["ANGLES_GENERATED", "FAILED_STAGE_ANGLES"],
    "ANGLES_GENERATED":     ["UPSCALING_ANGLES", "FAILED_STAGE_ANGLES"],
    "UPSCALING_ANGLES":     ["IMAGES_READY", "FAILED_STAGE_ANGLES"],
    "IMAGES_READY":         ["APPROVED_FOR_VIDEO"],
    "APPROVED_FOR_VIDEO":   ["GENERATING_VIDEO", "FAILED_STAGE_VIDEO"],
    "GENERATING_VIDEO":     ["PUBLISHED", "FAILED_STAGE_VIDEO"],
    "PUBLISHED":            [],
    # Recovery paths
    "FAILED_STAGE_1":       ["Draft"],
    "FAILED_STAGE_2":       ["MANNEQUIN_UPSCALED"],
    "FAILED_STAGE_ANGLES":  ["APPROVED_FOR_ANGLES"],
    "FAILED_STAGE_VIDEO":   ["APPROVED_FOR_VIDEO"],
}

# Dispatch map: status → pipeline module name
# Only statuses that AUTO-trigger a pipeline
DISPATCH_MAP: dict[str, str] = {
    "Draft":                "stage_1_upscale",
    "MANNEQUIN_UPSCALED":   "stage_2_model_gen",
    "APPROVED_FOR_ANGLES":  "stage_4_angles",
    "APPROVED_FOR_VIDEO":   "stage_5_video",
}


def can_transition(current: str, target: str) -> bool:
    allowed = VALID_TRANSITIONS.get(current, [])
    return target in allowed


def transition(current: str, target: str) -> str:
    if not can_transition(current, target):
        raise ValueError(
            f"Invalid transition: {current!r} → {target!r}. "
            f"Allowed: {VALID_TRANSITIONS.get(current, [])}"
        )
    logger.info("State transition: %s → %s", current, target)
    return target
