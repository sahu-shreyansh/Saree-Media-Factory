"""Fabric processor — pure computer vision analysis.

This module uses OpenCV, scikit-image, and scipy for fabric analysis.
NO AI calls — only math and classical computer vision.

Methods:
  - extract_colors_lab:      K-means clustering in LAB color space
  - estimate_thread_density:  FFT peak detection → warp/weft per cm
  - estimate_transparency:    Luminance variance analysis
  - generate_topography_map:  Sobel edge detection → depth map
  - detect_metallic_regions:  HSV analysis for zari/metallic areas
  - isolate_single_motif:     Contour detection → single motif crop
  - extract_reference_crops:  Extract body, border, pallu, motif regions
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
    from scipy import fft as scipy_fft
    from scipy.signal import find_peaks
    from skimage import color as sk_color
    HAS_CV = True
except ImportError:
    HAS_CV = False
    logger.warning("OpenCV/scipy/scikit-image not installed — fabric processor will be limited")


@dataclass
class FabricAnalysisCV:
    """Results from computer vision fabric analysis."""
    dominant_colors_lab: list[dict] = field(default_factory=list)
    thread_density_warp: float = 0.0
    thread_density_weft: float = 0.0
    transparency_level: str = "opaque"  # opaque | semi-sheer | sheer
    transparency_score: float = 0.0
    metallic_coverage_percent: float = 0.0
    metallic_regions: list[dict] = field(default_factory=list)
    topography_map: bytes | None = None
    transparency_map: bytes | None = None
    reference_crops: dict[str, bytes] = field(default_factory=dict)
    single_motif_crop: bytes | None = None


def analyze_image(image_bytes: bytes, dpi: float = 300.0) -> FabricAnalysisCV:
    """Run full CV analysis on a fabric image.

    Args:
        image_bytes: Raw image bytes (PNG/JPG)
        dpi: Image DPI for physical measurement calculations
    """
    if not HAS_CV:
        logger.warning("CV libraries not available — returning empty analysis")
        return FabricAnalysisCV()

    # Decode image
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        logger.error("Failed to decode image for fabric analysis")
        return FabricAnalysisCV()

    result = FabricAnalysisCV()

    # Run all analysis steps
    result.dominant_colors_lab = _extract_colors_lab(img, k=6)
    result.thread_density_warp, result.thread_density_weft = _estimate_thread_density(img, dpi)
    result.transparency_level, result.transparency_score = _estimate_transparency(img)
    result.metallic_coverage_percent, result.metallic_regions = _detect_metallic_regions(img)
    result.topography_map = _generate_topography_map(img)
    result.reference_crops = _extract_reference_crops(img)
    result.single_motif_crop = _isolate_single_motif(img)

    logger.info(
        "CV analysis complete: %d colors, thread density %.1f/%.1f, "
        "transparency=%s (%.2f), metallic=%.1f%%",
        len(result.dominant_colors_lab),
        result.thread_density_warp, result.thread_density_weft,
        result.transparency_level, result.transparency_score,
        result.metallic_coverage_percent,
    )
    return result


# ── Color extraction (LAB space) ─────────────────────────────────────

def _extract_colors_lab(img: np.ndarray, k: int = 6) -> list[dict]:
    """K-means clustering in LAB color space for device-independent colors."""
    # Convert to LAB
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    pixels = lab.reshape(-1, 3).astype(np.float32)

    # K-means
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.2)
    _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)

    # Calculate percentages
    unique, counts = np.unique(labels, return_counts=True)
    total = len(labels)

    colors = []
    for i, center in enumerate(centers):
        pct = (counts[i] / total) * 100 if i < len(counts) else 0
        colors.append({
            "L": round(float(center[0]), 1),
            "A": round(float(center[1]) - 128, 1),  # OpenCV LAB range: 0-255 → -128 to 127
            "B": round(float(center[2]) - 128, 1),
            "percentage": round(float(pct), 1),
        })

    # Sort by percentage descending
    colors.sort(key=lambda c: c["percentage"], reverse=True)
    return colors


# ── Thread density (FFT) ─────────────────────────────────────────────

def _estimate_thread_density(img: np.ndarray, dpi: float = 300.0) -> tuple[float, float]:
    """FFT-based thread density estimation (threads per cm)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # Analyze horizontal scan line (for warp threads)
    mid_row = gray[h // 2, :]
    warp = _fft_peak_frequency(mid_row, dpi)

    # Analyze vertical scan line (for weft threads)
    mid_col = gray[:, w // 2]
    weft = _fft_peak_frequency(mid_col, dpi)

    return warp, weft


def _fft_peak_frequency(signal: np.ndarray, dpi: float) -> float:
    """Find dominant frequency in 1D signal via FFT, convert to threads/cm."""
    n = len(signal)
    if n < 64:
        return 0.0

    # Remove DC and apply window
    signal = signal.astype(np.float64) - np.mean(signal)
    signal *= np.hanning(n)

    # FFT
    fft_vals = np.abs(scipy_fft.rfft(signal))
    freqs = scipy_fft.rfftfreq(n, d=1.0)

    # Skip DC component and very low frequencies
    fft_vals[:5] = 0

    # Find peaks
    peaks, properties = find_peaks(fft_vals, height=np.max(fft_vals) * 0.3)
    if len(peaks) == 0:
        return 0.0

    # Get the dominant peak frequency
    dominant_peak = peaks[np.argmax(properties["peak_heights"])]
    freq_per_pixel = freqs[dominant_peak]

    # Convert to threads per cm: freq_per_pixel * dpi * (1 inch / 2.54 cm)
    threads_per_cm = freq_per_pixel * dpi / 2.54
    return round(threads_per_cm, 1)


# ── Transparency detection ───────────────────────────────────────────

def _estimate_transparency(img: np.ndarray) -> tuple[str, float]:
    """Estimate fabric transparency via luminance variance analysis."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Compute local standard deviation using a sliding window
    kernel_size = 15
    local_mean = cv2.blur(gray.astype(np.float64), (kernel_size, kernel_size))
    local_sq_mean = cv2.blur((gray.astype(np.float64)) ** 2, (kernel_size, kernel_size))
    local_var = local_sq_mean - local_mean ** 2
    local_std = np.sqrt(np.maximum(local_var, 0))

    # High local variance with high brightness suggests transparency
    bright_mask = gray > 200
    if np.sum(bright_mask) < 100:
        return "opaque", 0.0

    bright_std = np.mean(local_std[bright_mask]) if np.any(bright_mask) else 0
    bright_ratio = np.sum(bright_mask) / gray.size

    # Score: combination of brightness ratio and variance in bright areas
    score = min(1.0, bright_ratio * 2 + bright_std / 50)

    if score > 0.6:
        level = "sheer"
    elif score > 0.3:
        level = "semi-sheer"
    else:
        level = "opaque"

    return level, round(float(score), 3)


# ── Topography map (edge detection) ──────────────────────────────────

def _generate_topography_map(img: np.ndarray) -> bytes:
    """Sobel edge detection → raised pattern depth map."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Sobel in X and Y
    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.sqrt(sobel_x ** 2 + sobel_y ** 2)

    # Normalize to 0-255
    topo = cv2.normalize(magnitude, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    # Encode to PNG bytes
    _, buf = cv2.imencode(".png", topo)
    return buf.tobytes()


# ── Metallic/zari detection ──────────────────────────────────────────

def _detect_metallic_regions(img: np.ndarray) -> tuple[float, list[dict]]:
    """HSV analysis to detect zari/metallic areas."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Gold/metallic: high saturation + high value in yellow-orange hue range
    # Gold: H 15-35, S 80-255, V 150-255
    lower_gold = np.array([15, 80, 150])
    upper_gold = np.array([35, 255, 255])
    gold_mask = cv2.inRange(hsv, lower_gold, upper_gold)

    # Silver/metallic: low saturation + high value (shiny grey)
    lower_silver = np.array([0, 0, 180])
    upper_silver = np.array([180, 50, 255])
    silver_mask = cv2.inRange(hsv, lower_silver, upper_silver)

    # Combine masks
    metallic_mask = cv2.bitwise_or(gold_mask, silver_mask)

    total_pixels = img.shape[0] * img.shape[1]
    metallic_pixels = np.sum(metallic_mask > 0)
    coverage = (metallic_pixels / total_pixels) * 100

    # Find contiguous metallic regions
    contours, _ = cv2.findContours(metallic_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > 100:  # Filter noise
            x, y, w, h = cv2.boundingRect(cnt)
            regions.append({
                "x": int(x), "y": int(y),
                "width": int(w), "height": int(h),
                "area_px": int(area),
            })

    # Sort by area descending, keep top 10
    regions.sort(key=lambda r: r["area_px"], reverse=True)
    regions = regions[:10]

    return round(float(coverage), 2), regions


# ── Reference crops ──────────────────────────────────────────────────

def _extract_reference_crops(img: np.ndarray) -> dict[str, bytes]:
    """Extract body texture, border, and pallu reference crops."""
    h, w = img.shape[:2]
    crops = {}

    # Body texture: center region (30-70% height, 30-70% width)
    body = img[int(h * 0.3):int(h * 0.7), int(w * 0.3):int(w * 0.7)]
    _, buf = cv2.imencode(".png", body)
    crops["body_texture"] = buf.tobytes()

    # Border: left edge strip (full height, 0-15% width)
    border_left = img[:, :int(w * 0.15)]
    _, buf = cv2.imencode(".png", border_left)
    crops["border"] = buf.tobytes()

    # Pallu: top region (0-25% height, full width)
    pallu = img[:int(h * 0.25), :]
    _, buf = cv2.imencode(".png", pallu)
    crops["pallu"] = buf.tobytes()

    # Bottom border: bottom strip
    border_bottom = img[int(h * 0.85):, :]
    _, buf = cv2.imencode(".png", border_bottom)
    crops["border_bottom"] = buf.tobytes()

    return crops


# ── Single motif isolation ───────────────────────────────────────────

def _isolate_single_motif(img: np.ndarray) -> bytes | None:
    """Contour detection to isolate a single decorative motif."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Edge detection
    edges = cv2.Canny(gray, 50, 150)

    # Dilate to connect nearby edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dilated = cv2.dilate(edges, kernel, iterations=2)

    # Find contours
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Filter contours by size (looking for medium-sized motifs)
    h, w = img.shape[:2]
    min_area = (h * w) * 0.002  # At least 0.2% of image
    max_area = (h * w) * 0.15   # At most 15% of image

    motif_contours = [
        c for c in contours
        if min_area < cv2.contourArea(c) < max_area
    ]

    if not motif_contours:
        return None

    # Pick the most central motif
    center_x, center_y = w // 2, h // 2
    best = min(motif_contours, key=lambda c: (
        abs(cv2.boundingRect(c)[0] + cv2.boundingRect(c)[2] // 2 - center_x) +
        abs(cv2.boundingRect(c)[1] + cv2.boundingRect(c)[3] // 2 - center_y)
    ))

    x, y, mw, mh = cv2.boundingRect(best)
    # Add padding
    pad = 20
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(w, x + mw + pad), min(h, y + mh + pad)

    motif = img[y1:y2, x1:x2]
    _, buf = cv2.imencode(".png", motif)
    return buf.tobytes()
