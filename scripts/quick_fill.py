#!/usr/bin/env python3
"""High-level quick-fill workflow for existing HWPX forms."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from fill_fixed_form import quick_fill as run_fixed_form_quick_fill
from insert_signature_pyhwpx import (
    SignatureError,
    count_markers,
    insert_signatures,
    load_markers,
)
from page_guard import collect_metrics, compare_metrics
from quick_fill_common import (
    enforce_hwpx_extension_or_raise,
    load_json_file,
    validate_input_document_extension,
    validate_output_document_extension,
)
from validate import validate as validate_hwpx
from document_safety import require_distinct_paths, atomic_output_path


class QuickFillError(RuntimeError):
    """Raised when the high-level quick-fill workflow cannot proceed."""


def get_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the field mapping from the data payload."""
    candidate = payload.get("fields", payload)
    if not isinstance(candidate, dict):
        raise QuickFillError("data: 'fields' must be an object.")
    return candidate


def render_value(rule: dict[str, Any], fields: dict[str, Any]) -> str:
    """Render a replacement value from a constant or a format template."""
    if "value" in rule:
        return str(rule["value"])

    template = rule.get("template")
    if not isinstance(template, str):
        raise QuickFillError(
            "rules: each rule must define either 'value' or 'template'."
        )

    try:
        return template.format_map(
            {key: "" if value is None else value for key, value in fields.items()}
        )
    except KeyError as exc:
        raise QuickFillError(
            f"rules: missing field for template: {exc.args[0]}"
        ) from exc


def compile_target(rule: dict[str, Any], search_limit: int) -> dict[str, Any]:
    """Compile a high-level target rule into fill_fixed_form target config."""
    target = rule.get("target", {"kind": "blank"})
    if not isinstance(target, dict):
        raise QuickFillError("rules.special_rules: 'target' must be an object.")

    kind = target.get("kind", "blank")
    compiled: dict[str, Any] = {"window": max(search_limit, 1)}

    if kind == "blank":
        compiled["mode"] = "next_blank"
        if target.get("next_marker_prefix"):
            compiled["mode"] = "next_blank_before_marker"
            compiled["next_marker_prefix"] = str(target["next_marker_prefix"])
        return compiled
    if kind == "exact":
        compiled["mode"] = "next_text_equals"
        compiled["value"] = str(target.get("value", ""))
        return compiled
    if kind == "contains":
        compiled["mode"] = "next_text_contains"
        compiled["value"] = str(target.get("value", ""))
        return compiled

    raise QuickFillError(f"rules.special_rules: unsupported target kind: {kind}")


def compile_config(
    data_payload: dict[str, Any],
    rules_payload: dict[str, Any],
    *,
    skip_page_guard: bool,
) -> dict[str, Any]:
    """Convert high-level data/rules JSON into fill_fixed_form config JSON."""
    fields = get_fields(data_payload)
    text_rules = rules_payload.get("text_rules", [])
    special_rules = rules_payload.get("special_rules", [])
    page_guard = rules_payload.get("page_guard", {})
    preflight = rules_payload.get("preflight", {})

    if not isinstance(text_rules, list):
        raise QuickFillError("rules: 'text_rules' must be an array.")
    if not isinstance(special_rules, list):
        raise QuickFillError("rules: 'special_rules' must be an array.")
    if not isinstance(page_guard, dict):
        raise QuickFillError("rules: 'page_guard' must be an object.")
    if not isinstance(preflight, dict):
        raise QuickFillError("rules: 'preflight' must be an object.")

    replacements: list[dict[str, Any]] = []
    relative_updates: list[dict[str, Any]] = []
    tail_replacements: list[dict[str, Any]] = []

    for index, rule in enumerate(text_rules, start=1):
        if not isinstance(rule, dict):
            raise QuickFillError(f"rules.text_rules[{index}]: each rule must be an object.")
        find_text = rule.get("find")
        if not isinstance(find_text, str) or not find_text:
            raise QuickFillError(
                f"rules.text_rules[{index}]: 'find' must be a non-empty string."
            )

        replacements.append(
            {
                "name": rule.get("name", find_text),
                "find": find_text,
                "replace": render_value(rule, fields),
                "required": bool(rule.get("required", False)),
            }
        )

    for index, rule in enumerate(special_rules, start=1):
        if not isinstance(rule, dict):
            raise QuickFillError(
                f"rules.special_rules[{index}]: each rule must be an object."
            )

        kind = rule.get("kind")
        label = rule.get("name", f"special_rule_{index}")
        search_limit = int(rule.get("search_limit", 5))
        required = bool(rule.get("required", False))

        if kind == "insert_after_text":
            match_text = rule.get("match_text")
            if not isinstance(match_text, str) or not match_text:
                raise QuickFillError(
                    f"rules.special_rules[{index}]: 'match_text' must be a non-empty string."
                )
            relative_updates.append(
                {
                    "name": label,
                    "anchor": {
                        "match": str(rule.get("match_mode", "equals")),
                        "value": match_text,
                    },
                    "target": compile_target(rule, search_limit),
                    "replace": render_value(rule, fields),
                    "required": required,
                    "apply_to_all_matches": bool(
                        rule.get("apply_to_all_matches", False)
                    ),
                }
            )
            continue

        if kind == "replace_fwspace_tail":
            contains = rule.get("contains")
            if not isinstance(contains, str) or not contains:
                raise QuickFillError(
                    f"rules.special_rules[{index}]: 'contains' must be a non-empty string."
                )
            tail_replacements.append(
                {
                    "name": label,
                    "element": str(rule.get("element", "fwSpace")),
                    "find": contains,
                    "replace": render_value(rule, fields),
                    "required": required,
                    "apply_to_all_matches": bool(
                        rule.get("apply_to_all_matches", False)
                    ),
                }
            )
            continue

        raise QuickFillError(
            f"rules.special_rules[{index}]: unsupported kind: {kind}"
        )

    compiled_page_guard = {} if skip_page_guard else dict(page_guard)

    return {
        "replacements": replacements,
        "relative_updates": relative_updates,
        "tail_replacements": tail_replacements,
        "page_guard": compiled_page_guard,
        "preflight": preflight,
    }


def append_page_guard_result(
    *,
    reference_path: Path,
    output_path: Path,
    signature_report: dict[str, Any],
    page_guard_config: dict[str, Any],
) -> None:
    """Run the same page guard heuristics after optional signature insertion."""
    errors = compare_metrics(
        collect_metrics(reference_path),
        collect_metrics(output_path),
        max_text_delta_ratio=float(page_guard_config.get("max_text_delta_ratio", 0.15)),
        max_paragraph_delta_ratio=float(
            page_guard_config.get("max_paragraph_delta_ratio", 0.25)
        ),
    )
    signature_report["page_guard_errors"] = errors
    if errors:
        raise QuickFillError("Signature output failed page guard.")


def run_signature_step(
    *,
    filled_output_path: Path,
    data_payload: dict[str, Any],
    rules_path: Path,
    report: dict[str, Any],
    signature_output_override: str | None,
    signature_dry_run_override: bool,
    page_guard_reference: str | None,
    skip_page_guard: bool,
    page_guard_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Optionally run the signature workflow after quick fill."""
    signature_block = data_payload.get("signature")
    if not isinstance(signature_block, dict) or not signature_block.get("enabled", False):
        return None

    markers = load_markers([], str(rules_path))
    counts = count_markers(filled_output_path, markers)
    total_matches = sum(counts.values())

    dry_run = signature_dry_run_override or bool(signature_block.get("dry_run", False))
    signature_report: dict[str, Any] = {
        "enabled": True,
        "dry_run": dry_run,
        "markers": counts,
        "matches": total_matches,
    }

    if dry_run:
        print("SUCCESS: Signature dry-run completed.")
        print(f"INPUT: {filled_output_path}")
        print(f"MARKERS FOUND: {total_matches}")
        for marker, count in counts.items():
            print(f" - {marker}: {count}")
        return signature_report

    image_path = signature_block.get("image_path")
    if not isinstance(image_path, str) or not image_path:
        raise QuickFillError("data.signature.image_path is required when signature is enabled.")

    target_output = signature_output_override or signature_block.get("target_output")
    if not isinstance(target_output, str) or not target_output:
        target_output = str(
            filled_output_path.with_name(f"{filled_output_path.stem}_signed.hwpx")
        )
    signed_output_path = Path(target_output)
    try:
        enforce_hwpx_extension_or_raise(signed_output_path, field_name="signature.output")
        from quick_fill_common import run_signature_helper
        inserted = run_signature_helper(
            input_path=filled_output_path,
            output_path=signed_output_path,
            signature_path=Path(image_path),
            markers=markers,
            width_mm=max(int(signature_block.get("width_mm", 8)), 1),
            height_mm=max(int(signature_block.get("height_mm", 8)), 1),
            max_count=max(int(signature_block.get("max_count", 3)), 1),
        )
    except SignatureError as exc:
        raise QuickFillError(str(exc)) from exc

    validation_errors = validate_hwpx(str(signed_output_path))
    if validation_errors:
        raise QuickFillError("Signature output failed structural validation.")

    signature_report.update(
        {
            "output_path": str(signed_output_path),
            "inserted": inserted,
            "validation_errors": validation_errors,
        }
    )

    reference_path: Path | None = None
    if not skip_page_guard and page_guard_reference:
        reference_path = Path(page_guard_reference)

    if reference_path is not None:
        append_page_guard_result(
            reference_path=reference_path,
            output_path=signed_output_path,
            signature_report=signature_report,
            page_guard_config=page_guard_config,
        )

    print("SUCCESS: Signature insertion completed.")
    print(f"INPUT: {filled_output_path}")
    print(f"OUTPUT: {signed_output_path}")
    print(f"INSERTED: {inserted}")
    return signature_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill an existing HWPX form from JSON data and JSON rules."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to input .hwpx or .hwp file.",
    )
    parser.add_argument("--output", required=True, help="Path to output .hwpx file.")
    parser.add_argument("--data", required=True, help="Path to JSON data file.")
    parser.add_argument("--rules", required=True, help="Path to JSON rules file.")
    parser.add_argument(
        "--page-guard-reference",
        help="Enable page guard with the provided reference .hwpx. Quick Fill skips page guard when omitted.",
    )
    parser.add_argument(
        "--skip-page-guard",
        action="store_true",
        help="Skip page guard even when --page-guard-reference is provided.",
    )
    parser.add_argument(
        "--signature-output",
        help="Optional override path for the signed .hwpx output.",
    )
    parser.add_argument(
        "--signature-dry-run",
        action="store_true",
        help="Scan signature markers without inserting the image.",
    )
    parser.add_argument(
        "--report-json",
        help="Optional path to write a combined JSON execution report.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the combined fill summary as JSON.",
    )
    args = parser.parse_args()

    input_validation_error = validate_input_document_extension(args.input)
    if input_validation_error:
        print(f"ERROR: input: {input_validation_error}")
        return 1

    output_validation_error = validate_output_document_extension(args.output)
    if output_validation_error:
        print(f"ERROR: output: {output_validation_error}")
        return 1

    try:
        enforce_hwpx_extension_or_raise(args.output, field_name="output")
        data_payload = load_json_file(args.data)
        rules_payload = load_json_file(args.rules)
        # Validate the report against every caller-owned file before even the
        # temporary config, fill/conversion, or optional signature can mutate.
        if args.report_json:
            protected = [args.input, args.output, args.data, args.rules,
                         args.page_guard_reference, args.signature_output]
            signature = data_payload.get("signature")
            if isinstance(signature, dict):
                protected.extend([signature.get("image_path"),
                                  signature.get("target_output")])
                if signature.get("enabled", False):
                    protected.append(str(Path(args.output).with_name(
                        f"{Path(args.output).stem}_signed.hwpx")))
            for path in protected:
                if path:
                    require_distinct_paths(path, args.report_json)
        compiled_config = compile_config(
            data_payload,
            rules_payload,
            skip_page_guard=args.skip_page_guard,
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as temp_config:
            json.dump(compiled_config, temp_config, ensure_ascii=False, indent=2)
            temp_config_path = Path(temp_config.name)

        try:
            try:
                report = run_fixed_form_quick_fill(
                    input_path=Path(args.input),
                    output_path=Path(args.output),
                    config_path=temp_config_path,
                    page_guard_reference=None if args.skip_page_guard else args.page_guard_reference,
                    report_path=None,
                )
            except SystemExit as exc:
                if isinstance(exc.code, str):
                    raise QuickFillError(exc.code) from exc
                raise QuickFillError("Low-level quick fill failed.") from exc
        finally:
            temp_config_path.unlink(missing_ok=True)

        signature_report = run_signature_step(
            filled_output_path=Path(args.output),
            data_payload=data_payload,
            rules_path=Path(args.rules),
            report=report,
            signature_output_override=args.signature_output,
            signature_dry_run_override=args.signature_dry_run,
            page_guard_reference=args.page_guard_reference,
            skip_page_guard=args.skip_page_guard,
            page_guard_config=compiled_config.get("page_guard", {}),
        )

        combined_report = {
            "success": True,
            "input": args.input,
            "output": args.output,
            "data": args.data,
            "rules": args.rules,
            "fill_report": report,
            "signature_report": signature_report,
        }
    except (QuickFillError, ValueError) as exc:
        message = str(exc)
        if message:
            print(f"ERROR: {message}")
        return 1

    if args.report_json:
        try:
            with atomic_output_path(args.report_json) as staged:
                staged.write_text(
                    json.dumps(combined_report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except (OSError, ValueError) as exc:
            print(f"ERROR: cannot write report: {exc}")
            return 1
        print(f"SUCCESS: Combined report written to {args.report_json}")

    if args.json:
        print(json.dumps(combined_report, ensure_ascii=False, indent=2))
        return 0

    print("SUCCESS: quick fill completed.")
    print(f"INPUT: {args.input}")
    print(f"OUTPUT: {args.output}")
    if signature_report and signature_report.get("output_path"):
        print(f"SIGNED OUTPUT: {signature_report['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
