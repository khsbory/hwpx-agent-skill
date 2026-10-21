#!/usr/bin/env python3
"""Export HWP/HWPX to PDF through a guarded hidden Hancom session."""

from __future__ import annotations

import argparse
import time
import os
import tempfile

from hancom_runtime import dispatch_cli, load_hwp, runtime_inspection_lines, RuntimeErrorDetail
from hancom_window_guard import HancomWindowGuard
from pathlib import Path

from hancom_automation import hancom_automation_lock
from hancom_security import (
    BACKUP_SCHEMA,
    SECURITY_DLL_NAME,
    SECURITY_MODULE_ID,
    SECURITY_MODULE_TYPE,
    SECURITY_REGISTRY_PATHS,
    HancomSecurityError,
    add_security_arguments,
    backup_registry_state,
    create_registered_hwp,
    default_backup_dir,
    discover_trusted_pyhwpx_module,
    find_registered_security_module,
    force_hidden,
    handle_security_utility_args,
    pe_machine,
    quit_safely,
    read_registry_state,
    repair_security_module_registration,
    restore_command,
    restore_registry_backup,
    sha256,
    validate_registry_entries,
)

SUPPORTED_INPUTS = {".hwp", ".hwpx"}
ExportError = HancomSecurityError

# Backward-compatible names for existing callers and tests.
_read_registry_state = read_registry_state
_sha256 = sha256
_pe_machine = pe_machine
_default_backup_dir = default_backup_dir
_restore_command = restore_command
_validate_registry_entries = validate_registry_entries
_quit_safely = quit_safely


def validate_input_path(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if path.suffix.lower() not in SUPPORTED_INPUTS:
        raise ExportError("input: Only .hwp or .hwpx input is supported.")
    if not path.is_file():
        raise ExportError(f"input: File not found: {path}")
    return path


def validate_output_path(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if path.suffix.lower() != ".pdf":
        raise ExportError("output: Only .pdf output is supported.")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def export_document_to_pdf(
    input_path: Path,
    output_path: Path,
    *,
    backup_dir: Path | None = None,
    repair_security_module: bool = False,
    exclusive_use: bool = False,
) -> tuple[Path | None, list[str]]:
    if exclusive_use:
        print("STAGE: exclusive-use | Keep all Hancom applications closed before starting; "
              "no other Hancom use during this task. Explicit user cooperation, not VM/OS isolation.", flush=True)
    print("STAGE: runtime-import", flush=True)
    try:
        Hwp = load_hwp()
    except RuntimeErrorDetail as exc:
        raise ExportError(str(exc)) from exc
    hwp = None
    backup_path = None
    changes = []
    stage = "output"
    temporary = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f".{output_path.stem}-", suffix=".pdf",
                                         dir=output_path.parent, delete=False) as stream:
            temporary = Path(stream.name)
        temporary.unlink()
        stage = "lock-wait"
        print(f"STAGE: {stage}", flush=True)
        with hancom_automation_lock():
            stage = "guard-start"
            print(f"STAGE: {stage}", flush=True)
            with (HancomWindowGuard(exclusive_use=True) if exclusive_use else HancomWindowGuard()) as guard:
                try:
                    guard.ensure_healthy()
                    stage = "session-start"
                    print(f"STAGE: {stage}", flush=True)
                    hwp, backup_path, changes = create_registered_hwp(
                        Hwp, backup_dir=backup_dir or default_backup_dir(),
                        repair_security_module=repair_security_module, guard=guard,
                    )
                    guard.ensure_healthy()
                    force_hidden(hwp)
                    stage = "document-open"
                    print(f"STAGE: {stage}", flush=True)
                    if not hwp.open(str(input_path)):
                        raise ExportError("[document-open] Hancom could not open the input document.")
                    force_hidden(hwp)
                    stage = "pdf-save"
                    print(f"STAGE: {stage}", flush=True)
                    if not hwp.save_as(str(temporary), format="PDF", arg=""):
                        raise ExportError("[pdf-save] Hancom PDF export returned failure. Check the installed PDF export component and destination permissions.")
                    guard.ensure_healthy()
                finally:
                    # Release the verified session while ownership protection
                    # and the serialization lock are still active.
                    quit_safely(hwp)
                    hwp = None
        stage = "pdf-validation"
        print(f"STAGE: {stage}", flush=True)
        validate_pdf(temporary)
        stage = "output-replace"
        print(f"STAGE: {stage}", flush=True)
        os.replace(temporary, output_path)
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError(f"[{stage}] {exc}") from exc
    finally:
        quit_safely(hwp)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup_path, changes


def validate_pdf(path: Path) -> None:
    try:
        import fitz
        # Read and close the OS file before parsing. On Windows, a failed
        # fitz.open(path) can retain its file handle through the exception's
        # traceback and prevent the exporter's finally block from deleting it.
        with fitz.open(stream=path.read_bytes(), filetype="pdf") as document:
            if not document.is_pdf or document.page_count < 1 or document.needs_pass:
                raise ValueError("PDF must contain readable pages.")
            for page in document:
                page.get_pixmap(matrix=fitz.Matrix(0.1, 0.1))
    except Exception as exc:
        raise ExportError(f"[pdf-validation] Export did not produce a readable PDF: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export HWP/HWPX to PDF in a fresh hidden Hancom session. The default "
            "mode does not write the registry."
        )
    )
    parser.add_argument("--input", help="Path to input .hwp or .hwpx file.")
    parser.add_argument("--output", help="Path to output .pdf file.")
    parser.add_argument("--exclusive-use", action="store_true", help=(
        "Explicit consent: keep all Hancom applications closed before starting; "
        "no other Hancom use during this task. Requires user cooperation, not VM/OS isolation."
    ))
    parser.add_argument("--diagnose", action="store_true", help="Read Python, COM and security registration without opening Hancom.")
    add_security_arguments(parser)
    args = parser.parse_args()

    try:
        if not args.restore_security_backup:
            child_status = dispatch_cli(__file__, ("fitz",))
            if child_status is not None:
                return child_status
        if args.diagnose:
            for line in runtime_inspection_lines():
                print(line)
            from hancom_security import security_inspection_lines
            for line in security_inspection_lines():
                print(line)
            return 0
        if handle_security_utility_args(args):
            return 0
        if not args.input or not args.output:
            parser.error("--input and --output are required for PDF export.")
        input_path = validate_input_path(args.input)
        output_path = validate_output_path(args.output)
        backup_dir = (
            Path(args.security_backup_dir).expanduser().resolve()
            if args.security_backup_dir
            else default_backup_dir()
        )
        for line in runtime_inspection_lines():
            print(line)
        backup_path, changes = export_document_to_pdf(
            input_path,
            output_path,
            backup_dir=backup_dir,
            repair_security_module=args.repair_security_module,
            exclusive_use=args.exclusive_use,
        )
    except (ExportError, RuntimeErrorDetail, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print("SUCCESS: PDF export completed.")
    print(f"INPUT: {input_path}")
    print(f"OUTPUT: {output_path}")
    if backup_path:
        print(f"REGISTRY_BACKUP: {backup_path}")
        for change in changes:
            print(f"REGISTRY_CHANGE: {change}")
        print(f"RESTORE_COMMAND: {restore_command(backup_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
