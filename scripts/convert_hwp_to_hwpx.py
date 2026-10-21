#!/usr/bin/env python3
"""Convert an HWP document to HWPX using pyhwpx."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from hancom_automation import hancom_automation_lock


class ConversionError(RuntimeError):
    """Raised when HWP to HWPX conversion cannot proceed."""


def validate_input_path(path_value: str) -> Path:
    """Validate the input HWP path."""
    path = Path(path_value).expanduser().resolve()
    if path.suffix.lower() != ".hwp":
        raise ConversionError("input: Only .hwp input is supported.")
    if not path.is_file():
        raise ConversionError(f"input: File not found: {path}")
    return path


def validate_output_path(path_value: str) -> Path:
    """Validate the output HWPX path."""
    path = Path(path_value).expanduser().resolve()
    if path.suffix.lower() != ".hwpx":
        raise ConversionError("output: Only .hwpx output is supported.")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def convert_hwp_to_hwpx(input_path: Path, output_path: Path, *, exclusive_use: bool = False) -> None:
    """Fresh, fail-closed conversion; publish only a validated staged package."""
    if exclusive_use:
        print("STAGE: exclusive-use | Keep all Hancom applications closed before starting; "
              "no other Hancom use during this task. Explicit user cooperation, not VM/OS isolation.", flush=True)
    from document_safety import atomic_output_path, require_distinct_paths
    from hancom_runtime import load_hwp
    from hancom_security import create_registered_hwp, quit_safely
    from hancom_window_guard import HancomWindowGuard
    from insert_signature_pyhwpx import _validate_saved_hwpx
    try:
        require_distinct_paths(input_path, output_path)
        Hwp = load_hwp()
        with atomic_output_path(output_path) as staged, hancom_automation_lock():
            with (HancomWindowGuard(exclusive_use=True) if exclusive_use else HancomWindowGuard()) as guard:
                hwp = None
                try:
                    guard.ensure_healthy()
                    hwp, _, _ = create_registered_hwp(Hwp, guard=guard)
                    if not hwp.open(str(input_path)):
                        raise ConversionError(f"Failed to open input file: {input_path}")
                    if not hwp.save_as(str(staged), format="HWPX", arg=""):
                        raise ConversionError(f"Failed to save output file: {output_path}")
                    guard.ensure_healthy()
                    _validate_saved_hwpx(staged)
                finally:
                    quit_safely(hwp)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"pyhwpx operation failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an HWP document to HWPX using pyhwpx."
    )
    parser.add_argument("--input", required=True, help="Path to input .hwp file.")
    parser.add_argument("--output", required=True, help="Path to output .hwpx file.")
    parser.add_argument("--exclusive-use", action="store_true", help=(
        "Explicit consent: keep all Hancom applications closed before starting; "
        "no other Hancom use during this task. Requires user cooperation, not VM/OS isolation."
    ))
    args = parser.parse_args()

    try:
        from hancom_runtime import dispatch_cli, RuntimeErrorDetail
        child_status = dispatch_cli(__file__, ("lxml",))
        if child_status is not None:
            return child_status
        input_path = validate_input_path(args.input)
        output_path = validate_output_path(args.output)
        convert_hwp_to_hwpx(input_path, output_path, exclusive_use=args.exclusive_use)
    except (ConversionError, RuntimeErrorDetail) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("SUCCESS: HWP to HWPX conversion completed.")
    print(f"INPUT: {input_path}")
    print(f"OUTPUT: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
