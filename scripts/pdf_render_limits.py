"""Bounded PDF rendering for local preview and comparison tools."""
from __future__ import annotations

import math

MIN_DPI = 72
MAX_DPI = 600
MAX_PAGE_PIXELS = 40_000_000
MAX_TOTAL_PIXELS = 200_000_000


def validate_dpi(dpi: int) -> None:
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not MIN_DPI <= dpi <= MAX_DPI:
        raise ValueError(f"DPI must be an integer between {MIN_DPI} and {MAX_DPI}.")


def page_pixel_cost(page, dpi: int) -> int:
    validate_dpi(dpi)
    width = float(page.rect.width)
    height = float(page.rect.height)
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError("PDF page dimensions must be positive and finite.")
    pixels = math.ceil(width * dpi / 72) * math.ceil(height * dpi / 72)
    if pixels > MAX_PAGE_PIXELS:
        raise ValueError(f"PDF page exceeds the {MAX_PAGE_PIXELS}-pixel rendering budget.")
    return pixels


def check_total_pixels(total: int) -> None:
    if total > MAX_TOTAL_PIXELS:
        raise ValueError(f"PDF job exceeds the {MAX_TOTAL_PIXELS}-pixel rendering budget.")
