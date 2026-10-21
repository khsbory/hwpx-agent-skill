#!/usr/bin/env python3
"""Safely unpack an HWPX file, checking XML while preserving its bytes.

Usage:
    python unpack.py input.hwpx output_dir/
"""

import argparse
import os
import sys
from pathlib import Path
from zipfile import ZipFile


# Resolve the sibling scripts directory, never an ambient working directory.
_SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPT_DIR))
from document_safety import safe_extract


def unpack(hwpx_path: str, output_dir: str) -> None:
    """Extract bounded, checked members without following links."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    with ZipFile(hwpx_path, "r") as zf:
        safe_extract(zf, output)

    print(f"Unpacked: {hwpx_path} -> {output_dir}")
    print(f"  Files: {len(list(output.rglob('*')))} entries")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely unpack HWPX with bounded ZIP and XML checks"
    )
    parser.add_argument("input", help="Path to .hwpx file")
    parser.add_argument("output", help="Output directory path")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    unpack(args.input, args.output)


if __name__ == "__main__":
    main()
