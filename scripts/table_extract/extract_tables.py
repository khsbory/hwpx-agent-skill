#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_tables
==============

One-command wrapper that takes a single Hancom document -- either a legacy
binary ``.hwp`` (OLE compound file) or a modern ``.hwpx`` (OWPML / zip) -- and
runs the whole "detect -> convert-if-needed -> extract tables" pipeline.

Pipeline
--------
1. Sniff the file's *magic bytes* to decide the real format, regardless of the
   extension:
     * ``50 4B 03 04`` (``PK\\x03\\x04``, a zip local-file header) -> already
       HWPX, parse it directly.
     * ``D0 CF 11 E0`` (OLE2 compound-file signature) -> legacy binary HWP,
       which must be converted to HWPX first.
     * anything else -> a clear error (exit code 2).
2. For a legacy HWP, convert a *copy* to ``.hwpx`` inside a temp directory using
   the existing ``convert_hwp_to_hwpx.py`` skill script (invoked as a subprocess
   with the dependency-carrying Python interpreter). The original file is never
   touched or moved.
3. Feed the resulting HWPX to ``hwpx_table_parser`` (imported as a library when
   possible, else called as a subprocess) to extract tables (and, for the
   ``shapes`` format, drawing-object / text-box text).
4. Emit output in whatever format the parser supports
   (``grid|markdown|json|matrix|validate|shapes``), to stdout or, with ``-o``,
   to a UTF-8 file. Temp conversion artifacts are cleaned up afterwards unless
   ``--keep-temp`` is given.

This module only *orchestrates* the two existing building blocks; it never
modifies them. All inline comments and log/console messages are intentionally in
English for portability. User-facing docs live in the accompanying README (Korean).

Exit codes
----------
* ``0`` -- success.
* ``1`` -- runtime failure (conversion error, parser error, write error, ...).
* ``2`` -- usage / input problem (file missing, unsupported/unknown format).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from typing import List, Optional, Tuple

# ------------------------------------------------------------------------------
# Locations of the building blocks. Everything is resolved relative to this file
# so the wrapper behaves identically no matter the caller's current working
# directory (agent shells reset cwd between calls) and keeps working after the
# folder is relocated inside the skill tree.
# ------------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# The HWPX table parser lives right next to this wrapper.
_PARSER_PATH = os.path.join(_THIS_DIR, "hwpx_table_parser.py")

# The parent "scripts" directory holds the sibling skill helpers we reuse: the
# HWP -> HWPX converter script and quick_fill_common's pyhwpx-interpreter locator.
_SCRIPTS_DIR = os.path.dirname(_THIS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from document_safety import require_distinct_paths, atomic_output_path

# The HWP -> HWPX converter (pyhwpx / Hancom GUI automation) skill script.
_CONVERTER_PATH = os.path.join(_SCRIPTS_DIR, "convert_hwp_to_hwpx.py")

# Optional interpreter override. Discovery is lazy and HWP-only.
_CONVERTER_PYTHON: List[str] = []

# Supported output formats -- delegated verbatim to hwpx_table_parser.
_FORMATS = ["grid", "markdown", "json", "matrix", "validate", "shapes"]

# Magic-byte signatures.
_SIG_ZIP = b"\x50\x4b\x03\x04"        # PK\x03\x04 -> zip / HWPX
_SIG_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 compound file -> legacy HWP

# Detected-format tokens.
_FMT_HWPX = "hwpx"
_FMT_HWP = "hwp"
_FMT_UNKNOWN = "unknown"


# ------------------------------------------------------------------------------
# Logging helper: everything diagnostic goes to stderr so stdout stays clean for
# the actual extracted content (important when piping/redirecting output).
# ------------------------------------------------------------------------------
def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ------------------------------------------------------------------------------
# Resolve the interpreter command that can run the HWP->HWPX converter.
# Kept below _log() so we can warn on fallback; called only for conversion.
# ------------------------------------------------------------------------------
def _resolve_converter_python() -> List[str]:
    """Return the interpreter command (list form) to run the converter with.

    Follows the skill convention (mirrors quick_fill_common's resolution order):
      1. Probe ``PYHWPX_PYTHON`` exclusively when explicitly set.
      2. Otherwise use shared runtime discovery to verify pyhwpx and lxml.
      3. Fail closed if no candidate passes; do not mark an unprobed fallback
         selected. The result is a list command such as ``["py", "-3"]``.

    Returning a list keeps the subprocess call uniform: the caller always splices
    it as ``[*_CONVERTER_PYTHON, _CONVERTER_PATH, ...]``.
    """
    # Probe explicit overrides too. A selected marker is safe only after the
    # interpreter/dependencies were verified; never silently fall back.
    from hancom_runtime import find_runtime
    command, _label = find_runtime(("lxml",))
    return list(command)


# Resolve only when a legacy HWP actually requires conversion.


# ------------------------------------------------------------------------------
# Format detection by magic bytes.
# ------------------------------------------------------------------------------
def detect_format(path: str) -> str:
    """Return ``"hwpx"``, ``"hwp"``, or ``"unknown"`` by sniffing magic bytes.

    We read only the first few bytes; the extension is ignored on purpose so a
    mislabeled file is still classified correctly.
    """
    with open(path, "rb") as fh:
        head = fh.read(8)

    if head[:4] == _SIG_ZIP:
        # Container recognition, not full package/schema validation.
        from document_safety import validate_archive, read_checked_member
        from hwpx_table_parser import _sorted_sections
        try:
            with zipfile.ZipFile(path) as zf:
                validate_archive(zf)
                if (_sorted_sections(zf.namelist()) and
                        read_checked_member(zf, 'mimetype').strip() == b'application/hwp+zip'):
                    return _FMT_HWPX
        except (ValueError, zipfile.BadZipFile, KeyError):
            pass
        return _FMT_UNKNOWN
    if head[:8] == _SIG_OLE:
        return _FMT_HWP
    return _FMT_UNKNOWN


# ------------------------------------------------------------------------------
# HWP -> HWPX conversion (delegated to the skill script as a subprocess).
# ------------------------------------------------------------------------------
def convert_hwp_to_hwpx(src_hwp: str, temp_dir: str, timeout: float = 120.0) -> str:
    """Convert ``src_hwp`` to an ``.hwpx`` inside ``temp_dir`` and return its path.

    The original file is never modified: we copy it into the temp directory under
    an ASCII-safe name first, then run the converter on that copy. Copying also
    sidesteps any Hancom quirk with long/complex source paths.

    The converter is launched with the dependency-carrying interpreter command
    (``_CONVERTER_PYTHON``, a list) and with ``cwd`` set to the converter's own
    directory so its sibling module ``hancom_automation`` imports cleanly.

    Raises
    ------
    RuntimeError
        If the converter is missing, exits non-zero, or produces no output file.
    """
    if not os.path.isfile(_CONVERTER_PATH):
        raise RuntimeError(
            "converter script not found: %s" % _CONVERTER_PATH
        )
    converter_python = _CONVERTER_PYTHON or _resolve_converter_python()
    if not converter_python:
        raise RuntimeError(
            "no python interpreter resolved for the HWP->HWPX converter "
            "(set PYHWPX_PYTHON or install pyhwpx)."
        )

    # Copy the source into temp under a stable ASCII name. Keeping the original
    # untouched is a hard requirement; the copy is what Hancom opens.
    src_copy = os.path.join(temp_dir, "source_input.hwp")
    shutil.copy2(src_hwp, src_copy)

    out_hwpx = os.path.join(temp_dir, "converted_output.hwpx")

    # _CONVERTER_PYTHON is a list command (e.g. ["py", "-3"] or [".../python.exe"]);
    # splice it so the interpreter and its args are passed correctly.
    cmd = [
        *converter_python,
        _CONVERTER_PATH,
        "--input",
        src_copy,
        "--output",
        out_hwpx,
    ]

    _log("[extract] converting HWP -> HWPX (Hancom GUI automation; serialized "
         "by an internal lock, this can take a while)...")
    _log("[extract]   converter: %s" % _CONVERTER_PATH)
    _log("[extract]   temp hwpx: %s" % out_hwpx)

    started = time.time()
    # Run with cwd at the converter dir so `import hancom_automation` resolves.
    # Capture stdout/stderr (UTF-8) so we can surface the real reason on failure.
    proc = _run_bounded(cmd, timeout, cwd=os.path.dirname(_CONVERTER_PATH),
                        env=dict(os.environ, HWPX_RUNTIME_SELECTED="1"))
    elapsed = time.time() - started

    # Echo the converter's own messages (helpful for diagnosing GUI failures).
    if proc.stdout:
        for line in proc.stdout.splitlines():
            _log("[converter] %s" % line)
    if proc.stderr:
        for line in proc.stderr.splitlines():
            _log("[converter:err] %s" % line)

    if proc.returncode != 0:
        raise RuntimeError(
            "HWP->HWPX conversion failed (exit %d) after %.1fs. See "
            "[converter] lines above for the cause." % (proc.returncode, elapsed)
        )

    if not os.path.isfile(out_hwpx):
        raise RuntimeError(
            "converter reported success but produced no output file: %s"
            % out_hwpx
        )

    _log("[extract] conversion done in %.1fs" % elapsed)
    return out_hwpx


# ------------------------------------------------------------------------------
# Parsing / rendering (prefer in-process import; fall back to subprocess).
# ------------------------------------------------------------------------------
def _import_parser():
    """Import the hwpx_table_parser module from this directory, or return None.

    Importing lets us both log the extracted table count and render output
    without a second process. If anything goes wrong (unexpected layout, syntax
    incompatibility) we return None and the caller falls back to a subprocess.
    """
    try:
        if _THIS_DIR not in sys.path:
            sys.path.insert(0, _THIS_DIR)
        import hwpx_table_parser  # type: ignore

        return hwpx_table_parser
    except Exception as exc:  # pragma: no cover - defensive
        _log("[extract] WARN: could not import hwpx_table_parser (%s); "
             "falling back to subprocess." % exc)
        return None


def _summarize_tables(parser_obj) -> str:
    """Build a short human summary of what was extracted (for the log)."""
    total = len(list(parser_obj.iter_tables()))
    try:
        body = len(parser_obj.body_tables())
        box = len(parser_obj.textbox_tables())
        shapes = len(parser_obj.shape_texts)
        return (
            "%d table(s) total (%d top-level in body, %d top-level in text boxes); "
            "%d text-box block(s)" % (total, body, box, shapes)
        )
    except Exception:
        return "%d table(s) total" % total


def _run_bounded(cmd, timeout, **kwargs):
    if timeout <= 0 or not __import__('math').isfinite(timeout):
        raise ValueError('timeout must be finite and positive')
    if os.name == 'nt':
        from owned_worker import run_windows_owned
        return run_windows_owned(cmd, timeout, **kwargs)
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run terminates/waits only for its own child. Never kill
        # shared Hancom sessions or unrelated processes.
        raise RuntimeError(f'Subprocess timed out after {timeout:g}s') from exc


def parse_and_render(
    hwpx_path: str, fmt: str, table_idx: Optional[int], strict: bool = False,
    timeout: float = 120.0
) -> Tuple[str, Optional[int]]:
    """Parse ``hwpx_path`` and render it in ``fmt``.

    Returns ``(text, table_count)`` where ``table_count`` is the number of
    tables detected (or None when only the subprocess fallback ran and the count
    is unknown).
    """
    mod = _import_parser()

    if mod is not None:
        # In-process path: load once, log the count, then reuse the module's own
        # renderer so output is byte-for-byte identical to the parser's CLI.
        try:
            parser_obj = mod.HwpxTableParser.load(hwpx_path)
        except FileNotFoundError:
            raise RuntimeError("parser could not find HWPX file: %s" % hwpx_path)
        except Exception as exc:
            raise RuntimeError("parser failed to load HWPX: %s" % exc)

        count = len(list(parser_obj.iter_tables()))
        _log("[extract] parsed HWPX: %s" % _summarize_tables(parser_obj))
        text = mod._render(parser_obj, fmt, table_idx)
        if strict:
            warnings = parser_obj.validate(table_idx)
            if warnings:
                raise RuntimeError('strict validation failed:\n' + '\n'.join(warnings))
        return text, count

    # Subprocess fallback: call the parser CLI with the same interpreter that is
    # running this wrapper. Output goes to stdout; we capture and relay it.
    cmd = [sys.executable, _PARSER_PATH, hwpx_path, "-f", fmt]
    if strict:
        cmd.append('--strict')
    if table_idx is not None:
        cmd += ["-t", str(table_idx)]
    proc = _run_bounded(cmd, timeout)
    if proc.stderr:
        for line in proc.stderr.splitlines():
            _log("[parser] %s" % line)
    if proc.returncode != 0:
        raise RuntimeError(
            "hwpx_table_parser failed (exit %d)." % proc.returncode
        )
    return proc.stdout, None


# ------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="extract_tables",
        description=(
            "One-command table extractor for Hancom documents. Auto-detects "
            ".hwp (legacy binary) vs .hwpx by magic bytes, converts .hwp to "
            ".hwpx when needed (never touching the original), then extracts "
            "tables with hwpx_table_parser."
        ),
    )
    p.add_argument(
        "input",
        help="path to the input document (.hwp or .hwpx; Korean paths OK)",
    )
    p.add_argument(
        "--format",
        "-f",
        choices=_FORMATS,
        default="grid",
        help=(
            "output format, delegated to hwpx_table_parser (default: grid). "
            "'shapes' dumps drawing-object / text-box text blocks."
        ),
    )
    p.add_argument(
        "--table",
        "-t",
        type=int,
        default=None,
        help="emit this table index and descendants (0-based); not supported with shapes",
    )
    p.add_argument(
        "--out",
        "-o",
        default=None,
        help="write output to this file (UTF-8) instead of stdout",
    )
    p.add_argument(
        "--keep-temp",
        action="store_true",
        help=(
            "keep the temporary converted .hwpx (and its work dir) instead of "
            "deleting it; the kept path is logged to stderr"
        ),
    )
    p.add_argument('--strict', action='store_true', help='fail with exit 1 on selected structural warnings')
    p.add_argument('--timeout', type=float, default=120.0, help='converter/parser subprocess timeout in seconds (default 120)')
    return p


def run(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.timeout <= 0 or not __import__('math').isfinite(args.timeout):
        _log('[extract] ERROR: timeout must be finite and positive')
        return 2

    in_path = os.path.abspath(args.input)
    try:
        if args.out:
            require_distinct_paths(in_path, args.out)
    except ValueError as exc:
        _log("[extract] ERROR: %s" % exc)
        return 2

    # --- Input existence check (distinct from unsupported-format) -------------
    if not os.path.exists(in_path):
        _log("[extract] ERROR: input file not found: %s" % in_path)
        return 2
    if not os.path.isfile(in_path):
        _log("[extract] ERROR: input path is not a file: %s" % in_path)
        return 2

    # --- Detect format --------------------------------------------------------
    try:
        fmt_detected = detect_format(in_path)
    except OSError as exc:
        _log("[extract] ERROR: cannot read input file: %s" % exc)
        return 2

    _log("[extract] input: %s" % in_path)
    _log("[extract] detected format: %s" % fmt_detected.upper())

    if fmt_detected == _FMT_UNKNOWN:
        _log(
            "[extract] ERROR: unsupported/unknown format. Expected a zip-based "
            "HWPX (magic 50 4B 03 04) or a legacy OLE HWP (magic D0 CF 11 E0)."
        )
        return 2

    # --- Obtain an HWPX to parse (convert only if needed) ---------------------
    temp_dir: Optional[str] = None
    hwpx_to_parse: str
    converted = False

    try:
        if fmt_detected == _FMT_HWPX:
            # Already HWPX: parse in place, no conversion, no temp files.
            hwpx_to_parse = in_path
            _log("[extract] no conversion needed; parsing HWPX directly.")
        else:
            # Legacy HWP: convert a copy inside a fresh temp dir.
            temp_dir = tempfile.mkdtemp(prefix="extract_tables_")
            hwpx_to_parse = convert_hwp_to_hwpx(in_path, temp_dir, timeout=args.timeout)
            converted = True

        # --- Parse & render ---------------------------------------------------
        text, count = parse_and_render(hwpx_to_parse, args.format, args.table,
            strict=args.strict, timeout=args.timeout)

    except RuntimeError as exc:
        _log("[extract] ERROR: %s" % exc)
        # Preserve temp artifacts for post-mortem when the user asked to keep them.
        if temp_dir and not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
        elif temp_dir:
            _log("[extract] kept temp dir for inspection: %s" % temp_dir)
        return 1
    except Exception as exc:  # pragma: no cover - unexpected
        _log("[extract] ERROR: unexpected failure: %s" % exc)
        if temp_dir and not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return 1
    except BaseException:
        if temp_dir and not args.keep_temp:
            shutil.rmtree(temp_dir, ignore_errors=True)
        elif temp_dir:
            _log('[extract] --keep-temp: temp artifacts left at %s' % temp_dir)
        raise

    # --- Emit output ----------------------------------------------------------
    try:
        if args.out:
            out_path = os.path.abspath(args.out)
            with atomic_output_path(out_path) as staged:
                staged.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
            _log("[extract] wrote %s output to %s" % (args.format, out_path))
        else:
            # Force UTF-8 on stdout so Korean text survives a cp949 console.
            try:
                sys.stdout.reconfigure(encoding="utf-8")  # py3.7+
            except Exception:
                pass
            sys.stdout.write(text)
            if not text.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
    except (OSError, ValueError) as exc:
        _log('[extract] ERROR: cannot write output: %s' % exc)
        if not args.out:
            try:
                descriptor = sys.stdout.fileno()
                if isinstance(descriptor, int):
                    with open(os.devnull, 'w') as sink:
                        os.dup2(sink.fileno(), descriptor)
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        return 1
    finally:
        # --- Clean up temp conversion artifacts -------------------------------
        if temp_dir:
            if args.keep_temp:
                _log("[extract] --keep-temp: temp artifacts left at %s" % temp_dir)
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)

    # Final one-line status for quick scanning of the log.
    verb = "converted+extracted" if converted else "extracted"
    if count is not None:
        _log("[extract] DONE (%s): %d table(s)." % (verb, count))
    else:
        _log("[extract] DONE (%s)." % verb)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
