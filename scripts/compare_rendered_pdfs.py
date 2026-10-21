#!/usr/bin/env python3
"""Render two PDFs and compare them page-by-page."""

from __future__ import annotations

import argparse
import json
import math
from io import BytesIO
from pathlib import Path

from pdf_render_limits import validate_dpi, page_pixel_cost, check_total_pixels
from document_safety import require_distinct_paths, atomic_output_path


class CompareError(RuntimeError):
    """Raised when PDF comparison cannot proceed."""


def _require_pdf(path_value: str, field_name: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if path.suffix.lower() != ".pdf":
        raise CompareError(f"{field_name}: Only .pdf input is supported.")
    if not path.is_file():
        raise CompareError(f"{field_name}: File not found: {path}")
    return path


def _import_runtime():
    try:
        import fitz  # type: ignore
        from PIL import Image, ImageChops, ImageStat  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment specific
        raise CompareError(
            "PyMuPDF and Pillow are required. Install pymupdf and pillow first."
        ) from exc
    return fitz, Image, ImageChops, ImageStat


def compare_pdfs(left_pdf: Path, right_pdf: Path, output_dir: Path, dpi: int, *, report_path: Path | None = None) -> dict:
    validate_dpi(dpi)
    if report_path is not None:
        require_distinct_paths(left_pdf, report_path)
        require_distinct_paths(right_pdf, report_path)
    fitz, Image, ImageChops, ImageStat = _import_runtime()

    left_pages_dir = output_dir / "left_pages"
    right_pages_dir = output_dir / "right_pages"
    diff_pages_dir = output_dir / "diff_pages"

    left_doc = fitz.open(left_pdf)
    right_doc = None
    try:
        right_doc = fitz.open(right_pdf)
        left_page_count = len(left_doc)
        right_page_count = len(right_doc)
        page_count = min(left_page_count, right_page_count)
        pages: list[dict] = []
        total_pixels = 0
        for index in range(page_count):
            total_pixels += page_pixel_cost(left_doc[index], dpi)
            total_pixels += page_pixel_cost(right_doc[index], dpi)
            check_total_pixels(total_pixels)
            destinations = [left_pages_dir / f"page_{index + 1:03d}.png", right_pages_dir / f"page_{index + 1:03d}.png", diff_pages_dir / f"diff_{index + 1:03d}.png"]
            for destination in destinations:
                require_distinct_paths(left_pdf, destination)
                require_distinct_paths(right_pdf, destination)
                if report_path is not None:
                    require_distinct_paths(report_path, destination)
            for a in range(len(destinations)):
                for b in range(a):
                    require_distinct_paths(destinations[a], destinations[b])
        output_dir.mkdir(parents=True, exist_ok=True)
        left_pages_dir.mkdir(exist_ok=True)
        right_pages_dir.mkdir(exist_ok=True)
        diff_pages_dir.mkdir(exist_ok=True)
        for index in range(page_count):
            left_pixmap = left_doc[index].get_pixmap(dpi=dpi, alpha=False)
            right_pixmap = right_doc[index].get_pixmap(dpi=dpi, alpha=False)

            left_image = Image.open(BytesIO(left_pixmap.tobytes("png"))).convert("RGB")
            right_image = Image.open(BytesIO(right_pixmap.tobytes("png"))).convert("RGB")

            left_path = left_pages_dir / f"page_{index + 1:03d}.png"
            right_path = right_pages_dir / f"page_{index + 1:03d}.png"
            diff_path = diff_pages_dir / f"diff_{index + 1:03d}.png"

            left_image.save(left_path)
            right_image.save(right_path)

            same_size = left_image.size == right_image.size
            if not same_size:
                diff_image = Image.new("RGB", left_image.size, color="white")
                diff_ratio = 1.0
                rms = None  # Different image sizes have no comparable RMS.
            else:
                diff_image = ImageChops.difference(left_image, right_image)
                diff_stat = ImageStat.Stat(diff_image)
                rms = math.sqrt(sum((value ** 2 for value in diff_stat.rms)) / len(diff_stat.rms))
                bbox = diff_image.getbbox()
                if bbox is None:
                    diff_pixels = 0
                else:
                    red, green, blue = diff_image.split()
                    any_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
                    diff_mask = any_channel.point(lambda value: 255 if value else 0)
                    diff_pixels = diff_mask.histogram()[255]
                diff_ratio = diff_pixels / float(left_image.width * left_image.height)
            diff_image.save(diff_path)

            pages.append(
                {
                    "page": index + 1,
                    "left_image": str(left_path),
                    "right_image": str(right_path),
                    "diff_image": str(diff_path),
                    "same_size": same_size,
                    "image_size": list(left_image.size),
                    "has_visual_diff": diff_ratio > 0.0 or not same_size,
                    "diff_ratio": diff_ratio,
                    "rms": rms,
                }
            )
    finally:
        left_doc.close()
        if right_doc is not None:
            right_doc.close()

    summary = {
        "page_count_match": left_page_count == right_page_count,
        "left_page_count": left_page_count,
        "right_page_count": right_page_count,
        "pages_with_diff": sum(1 for page in pages if page["has_visual_diff"]),
        "max_diff_ratio": max((page["diff_ratio"] for page in pages), default=0.0),
        "max_rms": max((page["rms"] for page in pages if page["rms"] is not None), default=None),
    }

    return {
        "left_pdf": str(left_pdf),
        "right_pdf": str(right_pdf),
        "left_page_count": left_page_count,
        "right_page_count": right_page_count,
        "pages": pages,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render two PDFs and compare them page-by-page."
    )
    parser.add_argument("--left", required=True, help="Path to the first PDF.")
    parser.add_argument("--right", required=True, help="Path to the second PDF.")
    parser.add_argument("--output-dir", required=True, help="Directory for rendered pages and diff images.")
    parser.add_argument("--report-json", help="Optional path to write the comparison report JSON.")
    parser.add_argument("--dpi", type=int, default=144, help="Render DPI.")
    args = parser.parse_args()

    try:
        left_pdf = _require_pdf(args.left, "left")
        right_pdf = _require_pdf(args.right, "right")
        output_dir = Path(args.output_dir).expanduser().resolve()
        report_path = (
            Path(args.report_json).expanduser().resolve()
            if args.report_json
            else output_dir / "comparison_report.json"
        )
        require_distinct_paths(left_pdf, report_path)
        require_distinct_paths(right_pdf, report_path)
        report = compare_pdfs(left_pdf, right_pdf, output_dir, args.dpi, report_path=report_path)
        with atomic_output_path(report_path) as staged_report:
            staged_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
    except (CompareError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("SUCCESS: PDF comparison completed.")
    print(f"LEFT: {left_pdf}")
    print(f"RIGHT: {right_pdf}")
    print(f"REPORT: {report_path}")
    print(f"PAGES WITH DIFF: {report['summary']['pages_with_diff']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
