#!/usr/bin/env python3
"""Capture each page of a single PDF as individual PNG files.

Renders pages at a configurable DPI using PyMuPDF and writes a JSON
summary (input path, total/captured page counts, per-file dimensions)
to stdout so callers can consume it programmatically.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

from pdf_render_limits import validate_dpi, page_pixel_cost, check_total_pixels
from document_safety import require_distinct_paths


def _parse_pages(spec: str, total: int) -> List[int]:
    """Parse a page spec into sorted 1-based page numbers.

    Formats: "1-3" -> [1,2,3], "1,3,5" -> [1,3,5], "2-4,7" -> [2,3,4,7].
    """
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo_i, hi_i = int(lo), int(hi)
            if lo_i < 1 or hi_i > total or lo_i > hi_i:
                raise SystemExit(
                    f"ERROR: Invalid page range '{part}' for a {total}-page document."
                )
            pages.update(range(lo_i, hi_i + 1))
        else:
            num = int(part)
            if num < 1 or num > total:
                raise SystemExit(f"ERROR: Page {num} is out of range (1-{total}).")
            pages.add(num)
    return sorted(pages)


def capture(
    input_pdf: Path,
    output_dir: Path,
    dpi: int = 300,
    page_spec: Optional[str] = None,
) -> dict:
    """Render *input_pdf* pages to PNG files inside *output_dir*."""
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SystemExit(
            "ERROR: PyMuPDF is required. Install it with: pip install pymupdf"
        ) from exc

    validate_dpi(dpi)
    if not input_pdf.is_file():
        raise SystemExit(f"ERROR: File not found: {input_pdf}")

    doc = fitz.open(input_pdf)
    files: list[dict] = []
    try:
        total_pages = len(doc)
        page_numbers = (
            _parse_pages(page_spec, total_pages)
            if page_spec
            else list(range(1, total_pages + 1))
        )
        total_pixels = 0
        for page_num in page_numbers:
            require_distinct_paths(input_pdf, output_dir / f"page_{page_num:03d}.png")
            total_pixels += page_pixel_cost(doc[page_num - 1], dpi)
            check_total_pixels(total_pixels)
        output_dir.mkdir(parents=True, exist_ok=True)
        for page_num in page_numbers:
            pix = doc[page_num - 1].get_pixmap(dpi=dpi, alpha=False)
            out_path = output_dir / f"page_{page_num:03d}.png"
            pix.save(str(out_path))
            files.append(
                {
                    "page": page_num,
                    "path": str(out_path),
                    "width": pix.width,
                    "height": pix.height,
                }
            )
    finally:
        doc.close()

    return {
        "input": str(input_pdf),
        "total_pages": total_pages,
        "captured_pages": len(files),
        "dpi": dpi,
        "output_dir": str(output_dir),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture each page of a PDF as an individual PNG file."
    )
    parser.add_argument("--input", required=True, help="Path to the input PDF file.")
    parser.add_argument(
        "--output-dir", required=True, help="Directory to save the PNG files."
    )
    parser.add_argument(
        "--dpi", type=int, default=300, help="Render resolution (default: 300)."
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Page specification: '1-3' for range or '1,3,5' for individual pages.",
    )
    args = parser.parse_args()

    input_pdf = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    result = capture(input_pdf, output_dir, args.dpi, args.pages)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
