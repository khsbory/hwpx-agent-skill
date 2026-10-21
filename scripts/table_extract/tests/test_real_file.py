#!/usr/bin/env python3
"""Optional integration smoke test for an explicitly supplied HWPX file.

Set HWPX_TABLE_TEST_FILE to enable the test. Optional expectations:
HWPX_TABLE_EXPECTED_TABLES (integer) and HWPX_TABLE_EXPECTED_TEXT (substring).
No private fixture path or document-specific assertion belongs in source control.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hwpx_table_parser import HwpxTableParser  # noqa: E402


def main() -> int:
    target_value = os.environ.get("HWPX_TABLE_TEST_FILE")
    if not target_value:
        print("SKIP: Set HWPX_TABLE_TEST_FILE to enable the integration smoke test.")
        return 0

    target = Path(target_value).expanduser().resolve()
    if target.suffix.lower() != ".hwpx" or not target.is_file():
        print("FAIL: HWPX_TABLE_TEST_FILE must point to an existing .hwpx file.")
        return 1

    try:
        expected_tables = int(os.environ.get("HWPX_TABLE_EXPECTED_TABLES", "0"))
    except ValueError:
        print("FAIL: HWPX_TABLE_EXPECTED_TABLES must be an integer.")
        return 1
    expected_text = os.environ.get("HWPX_TABLE_EXPECTED_TEXT", "")

    print(f"Loading integration fixture: {target.name}")
    document = HwpxTableParser.load(str(target))
    failures: list[str] = []
    if expected_tables and len(document.tables) != expected_tables:
        failures.append(
            f"expected {expected_tables} tables, found {len(document.tables)}"
        )
    if expected_text:
        combined_text = "\n".join(
            cell.text or "" for table in document.tables for cell in table.cells
        )
        if expected_text not in combined_text:
            failures.append("expected text was not found")
    warnings = document.validate()
    if warnings:
        failures.append(f"validation returned {len(warnings)} warning(s)")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(f"PASS: Parsed {len(document.tables)} table(s) without validation warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
