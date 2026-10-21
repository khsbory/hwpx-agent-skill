#!/usr/bin/env python3
"""Shared helpers for HWPX quick-fill workflows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

HWPX_ONLY_MESSAGE = "Only .hwpx is supported."
HWP_OR_HWPX_INPUT_MESSAGE = "Only .hwpx or .hwp input is supported."
HWP_CONVERSION_RUNTIME_MESSAGE = (
    "HWP conversion requires Hancom HWP + pyhwpx. "
    "Install pyhwpx or set PYHWPX_PYTHON to a compatible Python executable."
)


@dataclass(frozen=True)
class PreparedHwpxInput:
    """Resolved input document information for quick-fill workflows."""

    source_path: Path
    resolved_path: Path
    converted: bool
    converter_python: str | None = None


def validate_input_document_extension(input_path: str | Path) -> str | None:
    """Return the mandated error message when the input extension is invalid."""
    suffix = Path(input_path).suffix.lower()
    if suffix not in {".hwp", ".hwpx"}:
        return HWP_OR_HWPX_INPUT_MESSAGE
    return None


def validate_output_document_extension(output_path: str | Path) -> str | None:
    """Return the mandated error message when the output extension is invalid."""
    if Path(output_path).suffix.lower() != ".hwpx":
        return HWPX_ONLY_MESSAGE
    return None


def enforce_hwpx_extension_or_raise(
    input_path: str | Path,
    *,
    field_name: str = "input_path",
) -> None:
    """Raise ValueError when the path is not a supported HWPX path."""
    validation_error = validate_output_document_extension(input_path)
    if validation_error:
        raise ValueError(f"{field_name}: {validation_error}")


def _python_candidate_specs() -> list[tuple[list[str], str]]:
    """Return Python executable candidates that may have pyhwpx installed."""
    candidates: list[tuple[list[str], str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(command: list[str], label: str) -> None:
        key = tuple(command)
        if key in seen or not command:
            return
        if len(command) == 1 and not Path(command[0]).is_file() and shutil.which(command[0]) is None:
            return
        seen.add(key)
        candidates.append((command, label))

    env_python = os.environ.get("PYHWPX_PYTHON")
    if env_python:
        add([env_python], env_python)

    add([sys.executable], sys.executable)

    # Search common per-user Python install directories dynamically.
    user_python_base = (
        Path.home() / "AppData" / "Local" / "Programs" / "Python"
    )
    if user_python_base.is_dir():
        for version_dir in sorted(user_python_base.glob("Python3*"), reverse=True):
            candidate = version_dir / "python.exe"
            if candidate.is_file():
                add([str(candidate)], str(candidate))

    for command_name in ("python", "python3"):
        resolved = shutil.which(command_name)
        if resolved:
            add([resolved], resolved)

    py_launcher = shutil.which("py")
    if py_launcher:
        add([py_launcher, "-3"], f"{py_launcher} -3")

    return candidates


def _find_pyhwpx_python() -> tuple[list[str], str]:
    """Locate a Python command that can import pyhwpx."""
    from hancom_runtime import find_runtime
    return find_runtime(("lxml",))


def convert_hwp_to_hwpx(input_path: str | Path, output_path: str | Path) -> str:
    from document_safety import atomic_output_path, require_distinct_paths
    from insert_signature_pyhwpx import _validate_saved_hwpx
    require_distinct_paths(input_path, output_path)
    try:
        with atomic_output_path(output_path) as staged:
            label = _convert_hwp_helper(input_path, staged)
            _validate_saved_hwpx(staged)
        return label
    except Exception as exc:
        raise RuntimeError(f"Conversion output was not published: {exc}") from exc


def _convert_hwp_helper(input_path: str | Path, output_path: str | Path) -> str:
    """Convert an HWP file to HWPX using the pyhwpx-enabled Python runtime."""
    source_path = Path(input_path)
    target_path = Path(output_path)
    if source_path.suffix.lower() != ".hwp":
        raise RuntimeError(f"input_path: Expected an .hwp file, got: {source_path}")

    converter_script = Path(__file__).with_name("convert_hwp_to_hwpx.py")
    if not converter_script.is_file():
        raise RuntimeError(f"Conversion helper not found: {converter_script}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    command, label = _find_pyhwpx_python()

    try:
        completed = subprocess.run(
            [
                *command,
                str(converter_script),
                "--input",
                str(source_path),
                "--output",
                str(target_path),
            ],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            env=dict(os.environ, HWPX_RUNTIME_SELECTED="1"),
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to launch HWP conversion helper: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("HWP conversion timed out.") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if not detail:
            detail = "HWP conversion helper exited with a non-zero status."
        raise RuntimeError(detail)

    if not target_path.is_file():
        raise RuntimeError(f"HWP conversion did not produce output: {target_path}")

    return label


def run_signature_helper(*, input_path: Path, output_path: Path, signature_path: Path,
                         markers: list[str], width_mm: int, height_mm: int, max_count: int) -> int:
    """Run integrated signatures in the selected runtime with a bounded helper."""
    from hancom_runtime import find_runtime
    from insert_signature_pyhwpx import SignatureError, _validate_saved_hwpx
    from document_safety import atomic_output_path, require_distinct_paths
    import re
    require_distinct_paths(input_path, output_path)
    command, _ = find_runtime(("PIL.Image", "lxml"))
    with atomic_output_path(output_path) as staged:
        argv = [*command, str(Path(__file__).with_name("insert_signature_pyhwpx.py")),
                "--input", str(input_path.resolve()), "--output", str(staged),
                "--signature", str(signature_path.resolve()), "--width-mm", str(width_mm),
                "--height-mm", str(height_mm), "--max-count", str(max_count)]
        for marker in markers:
            argv.extend(["--marker", marker])
        try:
            result = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                    timeout=900, env=dict(os.environ, HWPX_RUNTIME_SELECTED="1"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SignatureError(f"Signature helper failed or timed out: {exc}") from exc
        if result.returncode:
            raise SignatureError((result.stderr or result.stdout or "Signature helper failed").strip())
        counts = re.findall(r"^INSERTED: ([1-9][0-9]*)$", result.stdout, re.MULTILINE)
        if len(counts) != 1:
            raise SignatureError("Signature helper returned no unambiguous insertion receipt.")
        _validate_saved_hwpx(staged)
    return int(counts[0])


@contextmanager
def prepare_input_hwpx(input_path: str | Path) -> Iterator[PreparedHwpxInput]:
    """Yield an HWPX path, converting from HWP when needed."""
    source_path = Path(input_path)
    validation_error = validate_input_document_extension(source_path)
    if validation_error:
        raise ValueError(f"input: {validation_error}")

    if source_path.suffix.lower() == ".hwpx":
        yield PreparedHwpxInput(
            source_path=source_path,
            resolved_path=source_path,
            converted=False,
        )
        return

    with tempfile.TemporaryDirectory(prefix="hwpx_input_conversion_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        converted_path = temp_dir / f"{source_path.stem}.hwpx"
        converter_python = convert_hwp_to_hwpx(source_path, converted_path)
        yield PreparedHwpxInput(
            source_path=source_path,
            resolved_path=converted_path,
            converted=True,
            converter_python=converter_python,
        )


def load_json_file(path: str | Path) -> dict:
    """Load a JSON object from disk with a clear error surface."""
    filepath = Path(path)
    if not filepath.is_file():
        raise SystemExit(f"Config file not found: {filepath}")

    try:
        raw = filepath.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Failed to read config file: {filepath}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON config: {filepath}: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit("Config root must be a JSON object.")

    return data
