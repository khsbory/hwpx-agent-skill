#!/usr/bin/env python3
"""Pack a directory back into an HWPX (ZIP) file.

The mimetype file is stored as the first entry with ZIP_STORED (no compression),
per OPC packaging conventions.

Usage:
    python pack.py input_dir/ output.hwpx
"""

import argparse
import os
import sys
from pathlib import Path



_SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPT_DIR))
from build_hwpx import pack_hwpx


def pack(input_dir: str, hwpx_path: str) -> None:
    """Create and validate a staged HWPX before replacing the destination."""
    pack_hwpx(Path(input_dir), Path(hwpx_path))
    print(f'Packed: {input_dir} -> {hwpx_path}')


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pack a directory into an HWPX (ZIP) file"
    )
    parser.add_argument("input", help="Input directory path")
    parser.add_argument("output", help="Output .hwpx file path")
    args = parser.parse_args()

    if not os.path.isdir(args.input):
        print(f"Error: Directory not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    pack(args.input, args.output)


if __name__ == "__main__":
    main()
