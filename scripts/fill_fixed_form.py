#!/usr/bin/env python3
"""Quick-fill an existing HWPX form using a JSON config."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from lxml import etree
from document_safety import parse_xml, safe_extract

from build_hwpx import pack_hwpx
from page_guard import collect_metrics, compare_metrics
from quick_fill_common import (
    load_json_file,
    prepare_input_hwpx,
    validate_input_document_extension,
    validate_output_document_extension,
)
from validate import validate as validate_hwpx

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}


def _local_name(elem: etree._Element) -> str:
    return etree.QName(elem.tag).localname


def _string_value(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"{label} must be a string.")
    return value


def _list_value(value: object, *, label: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SystemExit(f"{label} must be a list.")
    return value


def _dict_value(value: object, *, label: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be an object.")
    return value


def _match_anchor(text: str, anchor_spec: dict) -> bool:
    match_mode = _string_value(anchor_spec.get("match", "equals"), label="anchor.match")
    value = _string_value(anchor_spec.get("value", ""), label="anchor.value")
    if match_mode == "equals":
        return text == value
    if match_mode == "contains":
        return value in text
    if match_mode == "starts_with":
        return text.startswith(value)
    raise SystemExit(f"Unsupported anchor.match: {match_mode}")


def _find_target_index(
    t_elements: list[etree._Element],
    anchor_index: int,
    target_spec: dict,
) -> int | None:
    mode = _string_value(target_spec.get("mode", "next_blank"), label="target.mode")
    window_raw = target_spec.get("window", 5)
    if not isinstance(window_raw, int) or window_raw < 1:
        raise SystemExit("target.window must be a positive integer.")

    end_index = min(len(t_elements), anchor_index + 1 + window_raw)
    match_value = str(target_spec.get("value", ""))
    marker_prefix = str(target_spec.get("next_marker_prefix", ""))

    matches = []
    for idx in range(anchor_index + 1, end_index):
        anchor_scope = t_elements[anchor_index].xpath("ancestor::hp:tc[1] | ancestor::hp:p[1]", namespaces=NS)
        target_scope = t_elements[idx].xpath("ancestor::hp:tc[1] | ancestor::hp:p[1]", namespaces=NS)
        if anchor_scope != target_scope:
            break
        text = t_elements[idx].text or ""
        stripped = text.strip()

        if mode == "next_text_equals" and text == match_value:
            matches.append(idx)
        if mode == "next_text_contains" and match_value and match_value in text:
            matches.append(idx)
        if mode == "next_blank" and stripped == "":
            matches.append(idx)
        if mode == "next_blank_before_marker":
            if stripped != "":
                continue
            if idx + 1 >= len(t_elements):
                continue
            next_text = t_elements[idx + 1].text or ""
            next_scope = t_elements[idx + 1].xpath("ancestor::hp:tc[1] | ancestor::hp:p[1]", namespaces=NS)
            if next_scope == anchor_scope and (not marker_prefix or next_text.startswith(marker_prefix)):
                matches.append(idx)

    if len(matches) > 1:
        raise SystemExit("Ambiguous relative target within the same paragraph/cell.")
    return matches[0] if matches else None


def _load_section_xml(section_path: Path) -> etree._Element:
    return parse_xml(section_path.read_bytes())


def _save_section_xml(root: etree._Element, section_path: Path) -> None:
    tree = etree.ElementTree(root)
    tree.write(
        str(section_path),
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )


def _extract_hwpx(input_path: Path, temp_dir: Path) -> None:
    with zipfile.ZipFile(input_path, "r") as archive:
        safe_extract(archive, temp_dir)


def _validate_rules(rules: list[dict], prefix: str) -> None:
    names = set()
    for index, spec in enumerate(rules, 1):
        if not isinstance(spec, dict):
            raise SystemExit(f"Each {prefix} entry must be an object.")
        name = str(spec.get("name", spec.get("find", f"{prefix}_{index}")))
        if name in names:
            raise SystemExit(f"Duplicate rule name: {name}")
        names.add(name)
        if "find" in spec and not spec["find"]:
            raise SystemExit(f"{prefix}.find must not be empty.")


def _text_groups(root):
    # Direct paragraph runs only: never combine nested tables/cells/paragraphs.
    for paragraph in root.xpath(".//hp:p", namespaces=NS):
        nodes = paragraph.xpath("./hp:run/hp:t", namespaces=NS)
        if nodes:
            yield nodes


def _replace_group(nodes, find_text, replace_text):
    if any(len(node) for node in nodes):
        # Inline controls carry semantics; do not flatten them across runs.
        count = 0
        for node in nodes:
            if len(node):
                continue
            value = node.text or ""
            count += value.count(find_text)
            node.text = value.replace(find_text, replace_text)
        return count
    text = "".join(node.text or "" for node in nodes)
    starts = []
    offset = 0
    for node in nodes:
        starts.append(offset)
        offset += len(node.text or "")
    import re
    matches = list(re.finditer(re.escape(find_text), text))
    for match in reversed(matches):
        start, end = match.span()
        first = max(i for i, pos in enumerate(starts) if pos <= start)
        last = max(i for i, pos in enumerate(starts) if pos < end)
        head = (nodes[first].text or "")[:start-starts[first]]
        tail = (nodes[last].text or "")[end-starts[last]:]
        if first == last:
            nodes[first].text = head + replace_text + tail
        else:
            nodes[first].text = head + replace_text
            for node in nodes[first+1:last]:
                node.text = ""
            nodes[last].text = tail
    return len(matches)


def apply_text_replacements(root: etree._Element, replacements: list[dict]) -> dict[str, int]:
    _validate_rules(replacements, "replacement")
    results: dict[str, int] = {}
    t_elements = list(root.xpath(".//hp:t", namespaces=NS))

    for index, spec in enumerate(replacements, start=1):
        if not isinstance(spec, dict):
            raise SystemExit("Each replacements entry must be an object.")
        find_text = _string_value(spec.get("find", ""), label="replacements.find")
        replace_text = _string_value(spec.get("replace", ""), label="replacements.replace")
        name = str(spec.get("name", find_text or f"replacement_{index}"))

        if not find_text:
            raise SystemExit("replacements.find must not be empty.")
        count = sum(_replace_group(nodes, find_text, replace_text) for nodes in _text_groups(root))

        if bool(spec.get("required", False)) and count == 0:
            raise SystemExit(f"Required replacement not found: {name}")
        results[name] = count

    return results


def apply_relative_updates(root: etree._Element, updates: list[dict]) -> dict[str, int]:
    _validate_rules(updates, "relative_update")
    results: dict[str, int] = {}
    t_elements = list(root.xpath(".//hp:t", namespaces=NS))

    for index, spec in enumerate(updates, start=1):
        if not isinstance(spec, dict):
            raise SystemExit("Each relative_updates entry must be an object.")

        name = str(spec.get("name", f"relative_update_{index}"))
        anchor = _dict_value(spec.get("anchor"), label="relative_updates.anchor")
        target = _dict_value(spec.get("target"), label="relative_updates.target")
        replacement = _string_value(spec.get("replace", ""), label="relative_updates.replace")
        apply_all = bool(spec.get("apply_to_all_matches", False))

        if not anchor.get("value"):
            raise SystemExit("anchor.value must not be empty.")
        anchors = [i for i, elem in enumerate(t_elements) if _match_anchor(elem.text or "", anchor)]
        if len(anchors) > 1 and not apply_all:
            raise SystemExit(f"Ambiguous relative update anchor: {name}")
        count = 0
        for anchor_index in anchors:
            target_index = _find_target_index(t_elements, anchor_index, target)
            if target_index is None:
                continue

            t_elements[target_index].text = replacement
            count += 1
            if not apply_all:
                break

        if bool(spec.get("required", False)) and count == 0:
            raise SystemExit(f"Required relative update not applied: {name}")
        results[name] = count

    return results


def apply_tail_replacements(root: etree._Element, replacements: list[dict]) -> dict[str, int]:
    _validate_rules(replacements, "tail_replacement")
    if any(not spec.get("find") for spec in replacements):
        raise SystemExit("tail_replacements.find must not be empty.")
    results: dict[str, int] = {}

    for index, spec in enumerate(replacements, start=1):
        if not isinstance(spec, dict):
            raise SystemExit("Each tail_replacements entry must be an object.")

        name = str(spec.get("name", f"tail_replacement_{index}"))
        element_name = _string_value(
            spec.get("element", "fwSpace"),
            label="tail_replacements.element",
        )
        find_text = _string_value(spec.get("find", ""), label="tail_replacements.find")
        replace_text = _string_value(
            spec.get("replace", ""),
            label="tail_replacements.replace",
        )
        apply_all = bool(spec.get("apply_to_all_matches", False))

        count = 0
        for elem in root.iter():
            if _local_name(elem) != element_name:
                continue
            if not elem.tail or find_text not in elem.tail:
                continue
            count += elem.tail.count(find_text)
            elem.tail = elem.tail.replace(find_text, replace_text)
            if not apply_all:
                break

        if bool(spec.get("required", False)) and count == 0:
            raise SystemExit(f"Required tail replacement not applied: {name}")
        results[name] = count

    return results


def run_page_guard(
    *,
    reference_path: Path,
    output_path: Path,
    page_guard_config: dict,
) -> list[str]:
    ref_metrics = collect_metrics(reference_path)
    out_metrics = collect_metrics(output_path)
    max_text_delta_ratio = float(page_guard_config.get("max_text_delta_ratio", 0.15))
    max_paragraph_delta_ratio = float(
        page_guard_config.get("max_paragraph_delta_ratio", 0.25)
    )
    return compare_metrics(
        ref_metrics,
        out_metrics,
        max_text_delta_ratio=max_text_delta_ratio,
        max_paragraph_delta_ratio=max_paragraph_delta_ratio,
    )


def resolve_page_guard_reference(
    *,
    cli_reference: str | None,
    config_reference: object,
    input_path: Path,
) -> Path | None:
    _ = config_reference, input_path
    if cli_reference:
        return Path(cli_reference)
    return None


def quick_fill(
    *, input_path: Path, output_path: Path, config_path: Path,
    page_guard_reference: str | None, report_path: Path | None,
) -> dict:
    from document_safety import atomic_output_path, require_distinct_paths
    require_distinct_paths(input_path, output_path)
    if report_path is not None:
        require_distinct_paths(input_path, report_path)
        require_distinct_paths(output_path, report_path)
    with atomic_output_path(output_path) as staged:
        report = _quick_fill_staged(
            input_path=input_path, output_path=staged, config_path=config_path,
            page_guard_reference=page_guard_reference, report_path=None,
        )
    report["output_path"] = str(output_path)
    if report_path is not None:
        with atomic_output_path(report_path) as staged_report:
            staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _quick_fill_staged(
    *,
    input_path: Path,
    output_path: Path,
    config_path: Path,
    page_guard_reference: str | None,
    report_path: Path | None,
) -> dict:
    validation_error = validate_input_document_extension(input_path)
    if validation_error:
        raise SystemExit(validation_error)

    output_validation_error = validate_output_document_extension(output_path)
    if output_validation_error:
        raise SystemExit(f"output_path: {output_validation_error}")

    config = load_json_file(config_path)
    replacements = _list_value(config.get("replacements"), label="replacements")
    relative_updates = _list_value(
        config.get("relative_updates"),
        label="relative_updates",
    )
    tail_replacements = _list_value(
        config.get("tail_replacements"),
        label="tail_replacements",
    )
    page_guard_config = _dict_value(config.get("page_guard"), label="page_guard")
    preflight_config = _dict_value(config.get("preflight"), label="preflight")

    with prepare_input_hwpx(input_path) as prepared_input:
        if prepared_input.converted:
            print(
                "[INFO] Converted input HWP to temporary HWPX "
                f"using {prepared_input.converter_python}."
            )
            print(f"[INFO] Converted HWPX path: {prepared_input.resolved_path}")

        with tempfile.TemporaryDirectory(prefix="hwpx_quick_fill_") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            print(f"[INFO] Extracting input HWPX: {prepared_input.resolved_path}")
            _extract_hwpx(prepared_input.resolved_path, temp_dir)

            section_path = temp_dir / "Contents" / "section0.xml"
            if not section_path.is_file():
                raise SystemExit(
                    f"Missing section0.xml in input HWPX: {prepared_input.resolved_path}"
                )

            root = _load_section_xml(section_path)

            text_results = apply_text_replacements(root, replacements)
            relative_results = apply_relative_updates(root, relative_updates)
            tail_results = apply_tail_replacements(root, tail_replacements)

            minimum_total_replacements = preflight_config.get(
                "minimum_total_replacements",
                0,
            )
            if not isinstance(minimum_total_replacements, int) or minimum_total_replacements < 0:
                raise SystemExit(
                    "preflight.minimum_total_replacements must be a non-negative integer."
                )
            total_replacements = (
                sum(text_results.values())
                + sum(relative_results.values())
                + sum(tail_results.values())
            )
            if total_replacements < minimum_total_replacements:
                raise SystemExit(
                    "Preflight failed: total replacements below minimum threshold."
                )

            _save_section_xml(root, section_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pack_hwpx(temp_dir, output_path)

        resolved_input_path = prepared_input.resolved_path
        source_input_path = prepared_input.source_path
        input_converted = prepared_input.converted
        converter_python = prepared_input.converter_python

    validation_errors = validate_hwpx(str(output_path))
    if validation_errors:
        print("[ERROR] Structural validation failed.")
        for error in validation_errors:
            print(f"  - {error}")
        raise SystemExit(1)

    reference_path = resolve_page_guard_reference(
        cli_reference=page_guard_reference,
        config_reference=page_guard_config.get("reference"),
        input_path=input_path,
    )
    page_guard_errors: list[str] = []
    if reference_path is not None:
        if not reference_path.is_file():
            raise SystemExit(f"Page guard reference not found: {reference_path}")
        page_guard_errors = run_page_guard(
            reference_path=reference_path,
            output_path=output_path,
            page_guard_config=page_guard_config,
        )
        if page_guard_errors:
            print("[ERROR] Page guard failed.")
            for error in page_guard_errors:
                print(f"  - {error}")
            raise SystemExit(1)
        print("[OK] Page guard passed.")
    else:
        print("[INFO] Page guard skipped.")

    report = {
        "success": True,
        "input_path": str(source_input_path),
        "resolved_input_path": str(resolved_input_path),
        "input_converted": input_converted,
        "converter_python": converter_python,
        "output_path": str(output_path),
        "config_path": str(config_path),
        "replacements": text_results,
        "relative_updates": relative_results,
        "tail_replacements": tail_results,
        "total_replacements": (
            sum(text_results.values())
            + sum(relative_results.values())
            + sum(tail_results.values())
        ),
        "validation_errors": validation_errors,
        "page_guard_errors": page_guard_errors,
    }

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[INFO] Report written to: {report_path}")

    print("[OK] Structural validation passed.")
    print("[DONE] Quick fill completed.")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quick-fill an existing HWPX form using a JSON config"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to input .hwpx or .hwp file.",
    )
    parser.add_argument("--output", required=True, help="Path to output .hwpx file.")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON quick-fill config.",
    )
    parser.add_argument(
        "--page-guard-reference",
        help="Optional reference .hwpx for page guard. Skips when omitted.",
    )
    parser.add_argument(
        "--report-json",
        help="Optional output path for a JSON execution report.",
    )
    args = parser.parse_args()

    input_validation_error = validate_input_document_extension(args.input)
    if input_validation_error:
        print(input_validation_error)
        return 1

    output_validation_error = validate_output_document_extension(args.output)
    if output_validation_error:
        print(f"output_path: {output_validation_error}")
        return 1

    try:
        quick_fill(
            input_path=Path(args.input),
            output_path=Path(args.output),
            config_path=Path(args.config),
            page_guard_reference=args.page_guard_reference,
            report_path=Path(args.report_json) if args.report_json else None,
        )
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if isinstance(exc.code, str):
            print(exc.code)
        return code

    return 0


if __name__ == "__main__":
    sys.exit(main())
