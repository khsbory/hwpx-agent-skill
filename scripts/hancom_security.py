#!/usr/bin/env python3
"""Shared hidden-session and reversible FilePathChecker safeguards."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SECURITY_MODULE_TYPE = "FilePathCheckDLL"
SECURITY_MODULE_ID = "FilePathCheckerModule"
SECURITY_DLL_NAME = "FilePathCheckerModule.dll"
SECURITY_REGISTRY_PATHS = (
    r"Software\HNC\HwpAutomation\Modules",
    r"Software\Hnc\HwpUserAction\Modules",
)
BACKUP_SCHEMA = "hwpx-file-path-checker-registry-backup-v1"


class HancomSecurityError(RuntimeError):
    """Raised when guarded Hancom automation cannot proceed safely."""


def read_registry_state() -> list[dict[str, object]]:
    """Read supported HKCU values without changing them."""
    if os.name != "nt":
        return [
            {"path": path, "key_exists": False, "value_exists": False, "value": None}
            for path in SECURITY_REGISTRY_PATHS
        ]
    import winreg

    state: list[dict[str, object]] = []
    for registry_path in SECURITY_REGISTRY_PATHS:
        key_exists = False
        value_exists = False
        value: object = None
        value_type = None
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_READ
            ) as key:
                key_exists = True
                try:
                    raw_value, value_type = winreg.QueryValueEx(key, SECURITY_MODULE_ID)
                    value = {"bytes_hex": raw_value.hex()} if isinstance(raw_value, bytes) else raw_value
                    value_exists = True
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        state.append(
            {
                "path": registry_path,
                "key_exists": key_exists,
                "value_exists": value_exists,
                "value": value,
                "value_type": value_type,
            }
        )
    return state


def iter_registered_security_module_values() -> Iterable[str]:
    for entry in read_registry_state():
        value = entry.get("value")
        if entry.get("value_exists") and isinstance(value, str) and value.strip():
            yield value.strip()


def find_registered_security_module(
    values: Iterable[str] | None = None,
) -> Path | None:
    candidates = values if values is not None else iter_registered_security_module_values()
    for value in candidates:
        path = Path(os.path.expandvars(value)).expanduser()
        if path.is_file():
            return path.resolve()
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pe_machine(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read()
    if len(data) < 64 or data[:2] != b"MZ":
        return "not-pe"
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if pe_offset + 6 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        return "invalid-pe"
    machine = int.from_bytes(data[pe_offset + 4 : pe_offset + 6], "little")
    return {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(
        machine, f"0x{machine:04X}"
    )


def discover_trusted_pyhwpx_module() -> Path:
    import importlib.util
    spec = importlib.util.find_spec("pyhwpx")
    if spec is None or not spec.origin:
        raise HancomSecurityError("[runtime] pyhwpx is not installed in this Python environment.")
    package_module = Path(spec.origin).resolve().parent / SECURITY_DLL_NAME
    if not package_module.is_file():
        raise HancomSecurityError(
            f"The installed pyhwpx package does not contain {SECURITY_DLL_NAME}."
        )
    package_hash = sha256(package_module)
    matching: list[Path] = []
    for value in iter_registered_security_module_values():
        candidate = Path(os.path.expandvars(value)).expanduser()
        if candidate.is_file() and sha256(candidate) == package_hash:
            matching.append(candidate.resolve())
    for candidate in matching:
        try:
            str(candidate).encode("ascii")
        except UnicodeEncodeError:
            continue
        return candidate
    return matching[0] if matching else package_module


def default_backup_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / "hwpx-skill" / "backups").resolve()


def restore_command(backup_path: Path) -> str:
    return (
        f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}" '
        f'--restore-security-backup "{backup_path.resolve()}"'
    )


def backup_registry_state(
    backup_dir: Path,
    module_path: Path,
    before_state: list[dict[str, object]] | None = None,
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    backup_path = backup_dir / f"FilePathCheckerModule_registry_{timestamp}.json"
    payload = {
        "schema": BACKUP_SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "registry_root": "HKEY_CURRENT_USER",
        "value_name": SECURITY_MODULE_ID,
        "before": before_state if before_state is not None else read_registry_state(),
        "planned_module_path": str(module_path),
        "planned_module_sha256": sha256(module_path),
        "planned_module_machine": pe_machine(module_path),
        "restore_command": restore_command(backup_path),
    }
    with backup_path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return backup_path


def validate_registry_entries(entries: object) -> list[dict[str, object]]:
    if not isinstance(entries, list):
        raise HancomSecurityError("Registry backup does not contain a valid before state.")
    allowed = set(SECURITY_REGISTRY_PATHS)
    validated: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("path") not in allowed:
            raise HancomSecurityError(
                "Registry backup contains an unexpected registry path."
            )
        for flag in ("key_exists", "value_exists"):
            if type(entry.get(flag)) is not bool:
                raise HancomSecurityError(f"Registry backup has invalid {flag}.")
        if entry["value_exists"]:
            value_type = entry.get("value_type", 1)  # Legacy v1 strings were REG_SZ.
            value = entry.get("value")
            valid = False
            if value_type in (1, 2):
                valid = isinstance(value, str)
            elif value_type in (4, 11):
                valid = type(value) is int and 0 <= value < 2 ** (32 if value_type == 4 else 64)
            elif value_type == 7:
                valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
            elif value_type in (0, 3):
                valid = value is None and value_type == 0
                if isinstance(value, dict) and set(value) == {"bytes_hex"} and isinstance(value["bytes_hex"], str):
                    try:
                        bytes.fromhex(value["bytes_hex"])
                        valid = True
                    except ValueError:
                        pass
            if not valid or type(value_type) is not int:
                raise HancomSecurityError("Registry backup contains an unsupported type/value pair.")
        validated.append(entry)
    if len(validated) != len(allowed) or {
        str(entry["path"]) for entry in validated
    } != allowed:
        raise HancomSecurityError(
            "Registry backup does not cover each supported registry path exactly once."
        )
    return validated


def restore_registry_entries(entries: object) -> list[str]:
    if os.name != "nt":
        raise HancomSecurityError("Registry restore is supported only on Windows.")
    import winreg

    changes: list[str] = []
    for entry in validate_registry_entries(entries):
        registry_path = str(entry["path"])
        if entry.get("value_exists"):
            value = entry.get("value")
            value_type = entry.get("value_type", winreg.REG_SZ)
            if isinstance(value, dict) and set(value) == {"bytes_hex"}:
                value = bytes.fromhex(value["bytes_hex"])
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                registry_path,
                0,
                winreg.KEY_READ | winreg.KEY_WRITE,
            ) as key:
                winreg.SetValueEx(
                    key, SECURITY_MODULE_ID, 0, value_type, value
                )
            changes.append(f"HKCU\\{registry_path}: restored {entry['value']!r}")
            continue
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                registry_path,
                0,
                winreg.KEY_READ | winreg.KEY_WRITE,
            ) as key:
                try:
                    winreg.DeleteValue(key, SECURITY_MODULE_ID)
                    changes.append(f"HKCU\\{registry_path}: removed restored value")
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            continue
        if not entry.get("key_exists"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)
                changes.append(f"HKCU\\{registry_path}: removed repair-created key")
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return changes


def repair_security_module_registration(
    module_path: Path,
    backup_dir: Path,
) -> tuple[Path, list[str]]:
    if os.name != "nt":
        raise HancomSecurityError(
            "Hancom security-module repair is supported only on Windows."
        )
    if not module_path.is_file():
        raise HancomSecurityError(f"Trusted security-module DLL not found: {module_path}")
    import winreg

    before_state = validate_registry_entries(read_registry_state())
    backup_path = backup_registry_state(backup_dir, module_path, before_state)
    changes: list[str] = []
    try:
        for registry_path in SECURITY_REGISTRY_PATHS:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                registry_path,
                0,
                winreg.KEY_READ | winreg.KEY_WRITE,
            ) as key:
                old_value: str | None = None
                try:
                    existing, _ = winreg.QueryValueEx(key, SECURITY_MODULE_ID)
                    if isinstance(existing, str):
                        old_value = existing
                except FileNotFoundError:
                    pass
                new_value = str(module_path)
                if old_value != new_value:
                    winreg.SetValueEx(
                        key, SECURITY_MODULE_ID, 0, winreg.REG_SZ, new_value
                    )
                    changes.append(f"HKCU\\{registry_path}: {old_value!r} -> {new_value!r}")
    except Exception as exc:
        try:
            restore_registry_entries(before_state)
        except Exception as rollback_exc:
            raise HancomSecurityError(
                "HKCU repair failed and automatic rollback also failed. "
                f"Restore from: {backup_path}"
            ) from rollback_exc
        raise HancomSecurityError(
            "HKCU repair failed. All attempted changes were rolled back automatically. "
            f"Backup: {backup_path}"
        ) from exc
    return backup_path, changes


def restore_registry_backup(backup_path: Path) -> list[str]:
    try:
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HancomSecurityError(f"Could not read registry backup: {backup_path}") from exc
    if payload.get("schema") != BACKUP_SCHEMA:
        raise HancomSecurityError("Unsupported or invalid registry backup schema.")
    if payload.get("registry_root") != "HKEY_CURRENT_USER":
        raise HancomSecurityError("Registry backup uses an unsupported registry root.")
    if payload.get("value_name") != SECURITY_MODULE_ID:
        raise HancomSecurityError("Registry backup uses an unexpected value name.")
    return restore_registry_entries(payload.get("before"))


def force_hidden(hwp: object) -> None:
    """Best-effort reinforcement only after positive ownership verification."""
    if getattr(hwp, "_owned_session", False) is not True:
        raise HancomSecurityError("[session-ownership] Refusing to mutate an unowned session.")
    try:
        setattr(hwp, "visible", False)
    except Exception:
        pass
    raw = getattr(hwp, "hwp", None)
    if raw is None:
        return
    try:
        raw.Visible = False
    except Exception:
        pass
    try:
        raw.XHwpWindows.Active_XHwpWindow.Visible = False
    except Exception:
        pass


def new_hidden_hwp(Hwp: object, *, guard=None) -> object:
    if guard is None:
        raise HancomSecurityError("[session-ownership] An armed owner-binding guard is required before COM construction.")
    from hancom_runtime import create_fresh_hwp
    try:
        hwp = create_fresh_hwp(Hwp, guard=guard)
    except Exception as exc:
        raise HancomSecurityError(f"[session-start] Fresh Hancom activation refused: {exc}") from exc
    force_hidden(hwp)
    return hwp


def quit_safely(hwp: object | None) -> None:
    """Quit only the session object created by this workflow."""
    if hwp is not None and getattr(hwp, "_owned_session", False) is True:
        try:
            hwp.close()
        except Exception:
            pass


def register_security_module_with_hancom(hwp: object) -> bool:
    force_hidden(hwp)
    try:
        registered = bool(
            hwp.hwp.RegisterModule(
                ModuleType=SECURITY_MODULE_TYPE, ModuleData=SECURITY_MODULE_ID
            )
        )
    except Exception as exc:
        raise HancomSecurityError(
            "[security-activation] Hancom rejected the FilePathChecker security module. "
            "The document was not opened."
        ) from exc
    force_hidden(hwp)
    return registered


def create_registered_hwp(
    Hwp: object,
    *,
    backup_dir: Path | None = None,
    repair_security_module: bool = False,
    retry_delay_seconds: float = 1.0,
    guard=None,
) -> tuple[object, Path | None, list[str]]:
    """Create one fresh hidden session and permit at most one opt-in repair retry.

    Callers must pass an armed guard to establish ownership before activation.
    Omitting it is a fail-closed error before COM construction.
    """
    if guard is None:
        raise HancomSecurityError("[session-ownership] An armed owner-binding guard is required before COM construction.")
    first = new_hidden_hwp(Hwp, guard=guard)
    try:
        if register_security_module_with_hancom(first):
            return first, None, []
    except HancomSecurityError:
        if not repair_security_module:
            quit_safely(first)
            raise
    except Exception:
        quit_safely(first)
        raise
    quit_safely(first)
    if not repair_security_module:
        raise HancomSecurityError(
            "[security-activation] Hancom could not activate FilePathChecker. No registry changes were made "
            "and the document was not opened. Re-run with --repair-security-module "
            "only if you approve a backed-up HKCU repair."
        )

    trusted_module = discover_trusted_pyhwpx_module()
    backup_path, changes = repair_security_module_registration(
        trusted_module, backup_dir or default_backup_dir()
    )
    time.sleep(retry_delay_seconds)
    second = None
    try:
        guard.release_owner()
        second = new_hidden_hwp(Hwp, guard=guard)
        if not register_security_module_with_hancom(second):
            raise HancomSecurityError(
                "Hancom could not activate FilePathChecker after one explicit HKCU "
                f"repair. The document was not opened. Restore from: {backup_path}"
            )
        return second, backup_path, changes
    except HancomSecurityError as exc:
        quit_safely(second)
        raise HancomSecurityError(f"{exc} Restore from: {backup_path}") from exc
    except Exception as exc:
        quit_safely(second)
        raise HancomSecurityError(
            "The replacement Hancom COM session disconnected after the explicit HKCU "
            f"repair. The document was not opened. Restore from: {backup_path}"
        ) from exc


def security_inspection_lines() -> list[str]:
    registered = find_registered_security_module()
    trusted = discover_trusted_pyhwpx_module()
    lines = [
        "SUCCESS: Security-module inspection completed without launching Hancom.",
        f"REGISTERED_EXISTING_MODULE: {registered or 'none'}",
        f"TRUSTED_PYHWPX_MODULE: {trusted}",
        f"TRUSTED_MODULE_SHA256: {sha256(trusted)}",
        f"TRUSTED_MODULE_MACHINE: {pe_machine(trusted)}",
    ]
    for entry in read_registry_state():
        lines.append(
            "REGISTRY: "
            f"HKCU\\{entry['path']} | value={entry['value']!r} | "
            f"exists={entry['value_exists']}"
        )
    return lines


def add_security_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--check-security-only",
        action="store_true",
        help="Inspect security DLLs without launching Hancom.",
    )
    parser.add_argument(
        "--repair-security-module",
        action="store_true",
        help="Explicitly allow one backed-up repair of two HKCU module values.",
    )
    parser.add_argument(
        "--security-backup-dir",
        help="Registry backup directory. Default: LOCALAPPDATA\\hwpx-skill\\backups",
    )
    parser.add_argument(
        "--restore-security-backup",
        help="Restore HKCU values from a backup JSON without launching Hancom.",
    )


def handle_security_utility_args(args: argparse.Namespace) -> bool:
    if args.restore_security_backup:
        changes = restore_registry_backup(
            Path(args.restore_security_backup).expanduser().resolve()
        )
        print("SUCCESS: HKCU security-module registration was restored.")
        for change in changes:
            print(f"RESTORED: {change}")
        return True
    if args.check_security_only:
        for line in security_inspection_lines():
            print(line)
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or restore shared Hancom FilePathChecker security state."
    )
    parser.add_argument("--check-security-only", action="store_true")
    parser.add_argument("--restore-security-backup")
    args = parser.parse_args()
    if not args.check_security_only and not args.restore_security_backup:
        parser.error("Choose --check-security-only or --restore-security-backup.")
    try:
        if args.restore_security_backup:
            changes = restore_registry_backup(
                Path(args.restore_security_backup).expanduser().resolve()
            )
            print("SUCCESS: HKCU security-module registration was restored.")
            for change in changes:
                print(f"RESTORED: {change}")
        else:
            for line in security_inspection_lines():
                print(line)
    except HancomSecurityError as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
