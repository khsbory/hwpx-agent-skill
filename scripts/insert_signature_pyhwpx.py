#!/usr/bin/env python3
"""Insert signature images into an HWPX document using guarded pyhwpx."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import zipfile
from contextlib import ExitStack
from pathlib import Path
from xml.etree import ElementTree as ET


from hancom_runtime import dispatch_cli, load_hwp, RuntimeErrorDetail

from hancom_automation import hancom_automation_lock
from hancom_security import (
    HancomSecurityError,
    add_security_arguments,
    create_registered_hwp,
    default_backup_dir,
    force_hidden,
    handle_security_utility_args,
    quit_safely,
    restore_command,
)
from hancom_window_guard import HancomWindowGuard, WindowGuardError

NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}
DEFAULT_MARKERS = ["(서명  또는  인)", "(서명 또는 인)", "(인)"]
HWP_REJECTION_MESSAGE = "HWP is not supported directly. Save as HWPX in Hanword and retry."
HWPX_ONLY_MESSAGE = "Only .hwpx is supported."


class SignatureError(RuntimeError):
    """Raised when signature insertion cannot proceed."""


def validate_hwpx_extension(path_value: str, field_name: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    suffix = path.suffix.lower()
    if suffix == ".hwp":
        message = HWP_REJECTION_MESSAGE
    elif suffix != ".hwpx":
        message = HWPX_ONLY_MESSAGE
    else:
        return path
    raise SignatureError(message if field_name == "input" else f"{field_name}: {message}")


def load_markers(cli_markers: list[str], rules_path: str | None) -> list[str]:
    markers = [marker for marker in cli_markers if marker]
    if rules_path:
        path = Path(rules_path)
        if not path.is_file():
            raise SignatureError(f"rules: File not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SignatureError(f"rules: Invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SignatureError("rules: JSON root must be an object.")
        block = payload.get("signature", {})
        if isinstance(block, dict):
            markers.extend(value for value in block.get("markers", []) if isinstance(value, str) and value)
        markers.extend(value for value in payload.get("signature_markers", []) if isinstance(value, str) and value)
    if not markers:
        markers = list(DEFAULT_MARKERS)
    return list(dict.fromkeys(markers))


def count_markers(hwpx_path: Path, markers: list[str]) -> dict[str, int]:
    from document_safety import parse_xml, validate_archive, read_checked_member
    if not hwpx_path.is_file():
        raise SignatureError(f"input: File not found: {hwpx_path}")
    counts = {marker: 0 for marker in markers}
    try:
        with zipfile.ZipFile(hwpx_path, "r") as archive:
            validate_archive(archive)
            sections = sorted(
                name for name in archive.namelist()
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
            if not sections:
                raise SignatureError("input: Missing required section XML.")
            for name in sections:
                root = parse_xml(read_checked_member(archive, name))
                for paragraph in root.findall(".//hp:p", NS):
                    text = "".join("".join(node.itertext()) for node in paragraph.findall("./hp:run/hp:t", NS))
                    for marker in markers:
                        counts[marker] += text.count(marker)
    except SignatureError:
        raise
    except (KeyError, zipfile.BadZipFile, ET.ParseError, OSError) as exc:
        raise SignatureError(f"input: Invalid HWPX package: {exc}") from exc
    return counts


def validate_signature_image(path: Path, width: float, height: float, preserve_aspect: bool) -> tuple[float, float]:
    if width <= 0 or height <= 0:
        raise SignatureError("signature: Width and height must be positive.")
    try:
        from PIL import Image
        with Image.open(path) as image:
            if image.format not in {"PNG", "JPEG", "BMP", "GIF", "TIFF"}:
                raise ValueError("Use PNG, JPEG, BMP, GIF, or TIFF.")
            pixel_width, pixel_height = image.size
            image.verify()
        if preserve_aspect:
            scale = min(width / pixel_width, height / pixel_height)
            return pixel_width * scale, pixel_height * scale
    except Exception as exc:
        raise SignatureError(f"signature: Invalid or unsupported image: {exc}") from exc
    return width, height


def insert_image_at_current_position(hwp, image_path: str, width_mm: int, height_mm: int) -> None:
    import shutil
    # pyhwpx 1.7.2 deletes any input basename starting with 'tmp', even on
    # insertion failure. Only hand the vendor our own private, non-tmp copy.
    with tempfile.TemporaryDirectory(prefix="hwpx-signature-") as folder:
        staged = Path(folder) / ("signature-image" + Path(image_path).suffix)
        shutil.copyfile(image_path, staged)
        result = hwp.insert_picture(
            str(staged),
            embedded=True,
            sizeoption=1,
            width=width_mm,
            height=height_mm,
        )
        if result is False:
            raise SignatureError("[signature-insert] Hancom rejected the signature image.")


def _temporary_output(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}-signature-",
        suffix=".hwpx",
        dir=output_path.parent,
        delete=False,
    )
    handle.close()
    Path(handle.name).unlink()
    return Path(handle.name)


def _validate_saved_hwpx(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0 or not zipfile.is_zipfile(path):
        raise SignatureError(f"Hanword did not create a valid HWPX output: {path}")
    with zipfile.ZipFile(path, "r") as archive:
        from document_safety import validate_archive
        validate_archive(archive)
        if archive.testzip() is not None:
            raise SignatureError(f"Hanword created a corrupt HWPX output: {path}")
    from validate import validate
    errors = validate(str(path))
    if errors:
        raise SignatureError("Invalid HWPX output: " + "; ".join(errors))


def _validate_signature_images(path: Path, *, minimum_count: int, signature_path: Path | None = None) -> int:
    """Require decoded embedded references; this does not check visual placement."""
    from document_safety import validate_archive, read_checked_member, parse_xml
    from PIL import Image
    from io import BytesIO
    import hashlib
    def fingerprint(data):
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise SignatureError("Embedded image exceeds pixel budget.")
            return image.size, hashlib.sha256(image.convert("RGBA").tobytes()).digest()
    expected = fingerprint(signature_path.read_bytes()) if signature_path else None
    count = 0
    try:
        with zipfile.ZipFile(path) as archive:
            validate_archive(archive)
            manifest = parse_xml(read_checked_member(archive, "Contents/content.hpf"))
            items = {node.get("id"): node for node in manifest.iter() if node.tag == "{http://www.idpf.org/2007/opf/}item"}
            for name in archive.namelist():
                if not (name.startswith("Contents/section") and name.endswith(".xml")):
                    continue
                root = parse_xml(read_checked_member(archive, name))
                for image in root.iter("{http://www.hancom.co.kr/hwpml/2011/core}img"):
                    item = items.get(image.get("binaryItemIDRef"))
                    if item is None or not (item.get("media-type") or "").startswith("image/"):
                        raise SignatureError("Picture has no embedded image manifest relationship.")
                    href = item.get("href", "")
                    if href not in archive.namelist():
                        href = "Contents/" + href
                    actual = fingerprint(read_checked_member(archive, href))
                    if expected is None or actual == expected:
                        count += 1
        if count < minimum_count:
            raise SignatureError("Saved package is missing the expected embedded signature images.")
    except SignatureError:
        raise
    except Exception as exc:
        raise SignatureError(f"Invalid embedded signature image: {exc}") from exc
    return count


def insert_signatures(
    input_path: Path,
    output_path: Path,
    signature_path: Path,
    markers: list[str],
    width_mm: int,
    height_mm: int,
    max_count: int,
    *,
    backup_dir: Path | None = None,
    repair_security_module: bool = False,
    guard_factory=HancomWindowGuard,
    return_security_report: bool = False,
    preserve_aspect: bool = False,
    exclusive_use: bool = False,
) -> int | tuple[int, Path | None, list[str]]:
    if exclusive_use:
        print("STAGE: exclusive-use | Keep all Hancom applications closed before starting; "
              "no other Hancom use during this task. Explicit user cooperation, not VM/OS isolation.", flush=True)
    if not input_path.is_file():
        raise SignatureError(f"input: File not found: {input_path}")
    if not signature_path.is_file():
        raise SignatureError(f"signature: File not found: {signature_path}")
    from document_safety import require_distinct_paths
    require_distinct_paths(input_path, output_path)
    require_distinct_paths(signature_path, output_path)
    width_mm, height_mm = validate_signature_image(signature_path, width_mm, height_mm, preserve_aspect)
    if max_count < 1:
        raise SignatureError("signature: max_count must be positive.")
    if not any(count_markers(input_path, markers).values()):
        raise SignatureError("signature: No matching marker was found; no output was written.")
    _validate_saved_hwpx(input_path)
    baseline_images = _validate_signature_images(input_path, minimum_count=0, signature_path=signature_path)
    try:
        Hwp = load_hwp()
    except RuntimeErrorDetail as exc:
        raise SignatureError(str(exc)) from exc

    print("STAGE: signature-runtime-import-complete", flush=True)
    inserted = 0
    hwp = None
    backup_path: Path | None = None
    changes: list[str] = []
    temporary = _temporary_output(output_path)
    try:
        print("STAGE: lock-wait", flush=True)
        with hancom_automation_lock():
            print("STAGE: guard-start", flush=True)
            with (guard_factory(exclusive_use=True) if exclusive_use else guard_factory()) as guard, ExitStack() as cleanup:
                guard.ensure_healthy()
                print("STAGE: session-start", flush=True)
                hwp, backup_path, changes = create_registered_hwp(
                    Hwp,
                    backup_dir=backup_dir or default_backup_dir(),
                    repair_security_module=repair_security_module,
                    guard=guard,
                )
                cleanup.callback(quit_safely, hwp)
                guard.ensure_healthy()
                force_hidden(hwp)
                print("STAGE: document-open", flush=True)
                opened = hwp.open(str(input_path))
                force_hidden(hwp)
                if not opened:
                    raise SignatureError(f"Failed to open input file: {input_path}")
                print("STAGE: signature-insert", flush=True)
                for marker in markers:
                    hwp.MovePos(2)
                    while hwp.find(marker):
                        insert_image_at_current_position(
                            hwp, str(signature_path), width_mm, height_mm
                        )
                        inserted += 1
                        if inserted >= max_count:
                            break
                    if inserted >= max_count:
                        break
                if inserted == 0:
                    raise SignatureError("[signature-search] Hancom found no matching marker; no output was written.")
                print("STAGE: hwpx-save", flush=True)
                saved = hwp.save_as(str(temporary))
                if saved is False:
                    raise SignatureError(f"Failed to save HWPX output: {temporary}")
                guard.ensure_healthy()

        _validate_saved_hwpx(temporary)
        _validate_signature_images(temporary, minimum_count=baseline_images + inserted, signature_path=signature_path)
        os.replace(temporary, output_path)
        if return_security_report:
            return inserted, backup_path, changes
        return inserted
    except (HancomSecurityError, WindowGuardError) as exc:
        raise SignatureError(str(exc)) from exc
    except SignatureError:
        raise
    except Exception as exc:
        raise SignatureError(f"pyhwpx operation failed: {exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Insert signature images into HWPX using guarded pyhwpx.")
    parser.add_argument("--input", help="Path to input .hwpx file.")
    parser.add_argument("--output", help="Path to output .hwpx file.")
    parser.add_argument("--signature", help="Signature image. Required unless --dry-run is used.")
    parser.add_argument("--marker", action="append", default=[], help="Marker text. May be repeated.")
    parser.add_argument("--rules", help="Optional JSON rules file with signature markers.")
    parser.add_argument("--width-mm", type=int, default=8)
    parser.add_argument("--height-mm", type=int, default=8)
    parser.add_argument("--preserve-aspect", action="store_true", help="Fit the signature inside width/height without distortion.")
    parser.add_argument("--max-count", "--max-insertions", dest="max_count", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Scan markers without Hanword automation.")
    parser.add_argument("--exclusive-use", action="store_true", help=(
        "Explicit consent: keep all Hancom applications closed before starting; "
        "no other Hancom use during this task. Requires user cooperation, not VM/OS isolation."
    ))
    add_security_arguments(parser)
    args = parser.parse_args()
    try:
        if not args.dry_run and not args.restore_security_backup:
            child_status = dispatch_cli(__file__, ("PIL.Image", "lxml"))
            if child_status is not None:
                return child_status
        if handle_security_utility_args(args):
            return 0
        if not args.input or (not args.dry_run and not args.output):
            parser.error("--input is required; --output is also required unless --dry-run is used.")
        input_path = validate_hwpx_extension(args.input, "input")
        output_path = validate_hwpx_extension(args.output, "output") if args.output else None
        markers = load_markers(args.marker, args.rules)
        counts = count_markers(input_path, markers)
        if args.dry_run:
            print("SUCCESS: Signature dry-run completed without Hanword automation.")
            print(f"INPUT: {input_path}")
            print(f"MARKERS FOUND: {sum(counts.values())}")
            for marker, count in counts.items():
                print(f" - {marker}: {count}")
            return 0
        if not args.signature:
            raise SignatureError("signature: This argument is required unless --dry-run is used.")
        backup_dir = (
            Path(args.security_backup_dir).expanduser().resolve()
            if args.security_backup_dir else default_backup_dir()
        )
        inserted, backup_path, changes = insert_signatures(
            input_path, output_path, Path(args.signature).expanduser().resolve(), markers,
            args.width_mm, args.height_mm, args.max_count,
            backup_dir=backup_dir,
            repair_security_module=args.repair_security_module,
            return_security_report=True,
            preserve_aspect=args.preserve_aspect,
            exclusive_use=args.exclusive_use,
        )
    except (SignatureError, HancomSecurityError, RuntimeErrorDetail, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print("SUCCESS: Signature insertion completed.")
    print(f"INPUT: {input_path}")
    print(f"OUTPUT: {output_path}")
    print(f"INSERTED: {inserted}")
    if backup_path:
        print(f"REGISTRY_BACKUP: {backup_path}")
        for change in changes:
            print(f"REGISTRY_CHANGE: {change}")
        print(f"RESTORE_COMMAND: {restore_command(backup_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
