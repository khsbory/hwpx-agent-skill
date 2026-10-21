#!/usr/bin/env python3
"""Extract quick-fill placeholders and markers from an HWPX form."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from lxml import etree
from document_safety import (parse_xml, read_checked_member,
                             require_distinct_paths, atomic_output_path)
from quick_fill_common import prepare_input_hwpx, validate_input_document_extension

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
}

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def load_section_root(hwpx_path: Path) -> etree._Element:
    """Load section0.xml from an HWPX file."""
    try:
        with zipfile.ZipFile(hwpx_path, "r") as zf:
            data = read_checked_member(zf, "Contents/section0.xml")
    except FileNotFoundError as exc:
        raise SystemExit(f"ERROR: File not found: {hwpx_path}") from exc
    except KeyError as exc:
        raise SystemExit("ERROR: Missing required file: Contents/section0.xml") from exc
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"ERROR: Not a valid ZIP archive: {hwpx_path}") from exc

    try:
        return parse_xml(data)
    except etree.XMLSyntaxError as exc:
        raise SystemExit(f"ERROR: Malformed XML in section0.xml: {exc}") from exc


def extract_schema(hwpx_path: Path, sample_limit: int) -> dict[str, object]:
    """Extract placeholders, marker-like strings, and sample text nodes."""
    root = load_section_root(hwpx_path)
    text_nodes = root.xpath(".//hp:t", namespaces=NS)

    placeholders: dict[str, int] = {}
    signature_markers: dict[str, int] = {}
    samples: list[str] = []

    for paragraph in root.xpath(".//hp:p", namespaces=NS):
        nodes = paragraph.xpath("./hp:run/hp:t", namespaces=NS)
        text_value = "".join("".join(node.itertext()) for node in nodes)
        if not text_value:
            continue

        if len(samples) < sample_limit:
            samples.append(text_value)

        for placeholder in PLACEHOLDER_RE.findall(text_value):
            placeholders[placeholder] = placeholders.get(placeholder, 0) + 1

        if "서명" in text_value and "인" in text_value:
            signature_markers[text_value] = signature_markers.get(text_value, 0) + 1

    return {
        "input": str(hwpx_path),
        "placeholder_fields": [
            {"name": name, "occurrences": count}
            for name, count in sorted(placeholders.items())
        ],
        "signature_markers": [
            {"text": text, "occurrences": count}
            for text, count in sorted(signature_markers.items())
        ],
        "sample_text_nodes": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract quick-fill placeholders and marker text from an HWPX file."
    )
    parser.add_argument("input", help="Path to input .hwpx or .hwp file.")
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        help="Maximum number of sample text nodes to include.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the extracted schema JSON.",
    )
    args = parser.parse_args()

    validation_error = validate_input_document_extension(args.input)
    if validation_error:
        print(f"ERROR: input: {validation_error}")
        return 1

    try:
        if args.output:
            require_distinct_paths(args.input, args.output)
        with prepare_input_hwpx(args.input) as prepared_input:
            if args.output:
                require_distinct_paths(prepared_input.resolved_path, args.output)
            if prepared_input.converted:
                print(
                    "[INFO] Converted input HWP to temporary HWPX "
                    f"using {prepared_input.converter_python}."
                )
                print(f"[INFO] Converted HWPX path: {prepared_input.resolved_path}")
            schema = extract_schema(prepared_input.resolved_path, max(args.sample_limit, 1))
            schema["input"] = str(prepared_input.source_path)
            schema["resolved_input"] = str(prepared_input.resolved_path)
            schema["input_converted"] = prepared_input.converted
            schema["converter_python"] = prepared_input.converter_python
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    output = json.dumps(schema, ensure_ascii=False, indent=2)

    if args.output:
        try:
            with atomic_output_path(args.output) as staged:
                staged.write_text(output, encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"ERROR: cannot write schema: {exc}")
            return 1
        print(f"SUCCESS: Schema written to {args.output}")
        return 0

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
