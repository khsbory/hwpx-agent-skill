#!/usr/bin/env python3
"""
page_guard.py - HWPX 레퍼런스 대비 페이지 드리프트 위험 검사

목표:
- 실제 렌더러의 쪽수 계산을 대체할 수는 없지만,
  쪽수 변화로 이어지기 쉬운 구조 변경을 사전에 차단한다.

검사 항목:
- 모든 구역 순서, 문단 수 / 표 수 / 표 구조(rowCnt, colCnt, width, height) 동일성
- XML 레이아웃/스타일 속성, 셀 글자수 및 명시적 break 위치의 변화
- 렌더링 페이지 수·배치·폰트 대체를 검증하지 않음; 동일 길이 텍스트도 배치가 달라질 수 있음
- 명시적 pageBreak / columnBreak 수 동일성
- 전체 텍스트 길이 편차(기본 15%) 한도
- 문단별 텍스트 길이 급변(기본 25%) 감지
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import dataclass, asdict, field
from io import BytesIO
from pathlib import Path
from typing import List, Tuple

from lxml import etree
from document_safety import validate_archive, read_checked_member, parse_xml
from text_extract import _section_entries, _get_para_text

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}


@dataclass
class Metrics:
    paragraph_count: int
    page_break_count: int
    column_break_count: int
    table_count: int
    table_shapes: List[Tuple[str, str, str, str, str, str]]
    text_char_total: int
    text_char_total_nospace: int
    paragraph_text_lengths: List[int]
    section_names: List[str] = field(default_factory=list)
    layout_signature: List = field(default_factory=list)


def _read_section_xml_bytes(hwpx_path: Path) -> bytes:
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        return read_checked_member(zf, "Contents/section0.xml")


def _text_of_t_node(t_node: etree._Element) -> str:
    return "".join(t_node.itertext())


def collect_metrics(hwpx_path: Path) -> Metrics:
    entries = _section_entries(hwpx_path)
    with zipfile.ZipFile(hwpx_path) as archive:
        validate_archive(archive)
        roots = [parse_xml(read_checked_member(archive, entry)) for entry in entries]
        header = parse_xml(read_checked_member(archive, 'Contents/header.xml')) if 'Contents/header.xml' in archive.namelist() else None
    paragraphs = [p for root in roots for p in root.iter('{%s}p' % NS['hp'])]
    tables = [t for root in roots for t in root.iter('{%s}tbl' % NS['hp'])]
    table_shapes = []
    for table in tables:
        sz = table.find('hp:sz', NS)
        table_shapes.append((table.get('rowCnt',''), table.get('colCnt',''),
                             sz.get('width','') if sz is not None else '',
                             sz.get('height','') if sz is not None else '',
                             table.get('repeatHeader',''), table.get('pageBreak','')))
    texts = [_get_para_text(p) for p in paragraphs]
    # XML layout/style risk signals, not rendered pagination.
    def layout(node):
        local = etree.QName(node).localname
        attrs = tuple(sorted((k,v) for k,v in node.attrib.items() if k != 'id'))
        length = sum(len(_get_para_text(p)) for p in node.iter('{%s}p' % NS['hp'])) if local == 'tc' else None
        return (node.tag, attrs, length,
                tuple(layout(child) for child in node if isinstance(child.tag,str)
                      and etree.QName(child).localname not in ('t','linesegarray')))
    signatures = [layout(r) for r in roots]
    if header is not None:
        signatures.append(layout(header))
    return Metrics(
        paragraph_count=len(paragraphs),
        page_break_count=sum(p.get('pageBreak') == '1' for p in paragraphs),
        column_break_count=sum(p.get('columnBreak') == '1' for p in paragraphs),
        table_count=len(tables), table_shapes=table_shapes,
        text_char_total=sum(map(len,texts)),
        text_char_total_nospace=sum(len(''.join(t.split())) for t in texts),
        paragraph_text_lengths=list(map(len,texts)),
        section_names=entries, layout_signature=signatures,
    )


def _ratio_delta(a: int, b: int) -> float:
    base = max(a, 1)
    return abs(b - a) / base


def compare_metrics(
    ref: Metrics,
    out: Metrics,
    max_text_delta_ratio: float,
    max_paragraph_delta_ratio: float,
) -> List[str]:
    errors: List[str] = []
    if ref.section_names != out.section_names:
        errors.append("Section sequence mismatch")
    if ref.layout_signature != out.layout_signature:
        errors.append("XML layout/style/cell-length/break-position signature mismatch (heuristic, not renderer validation)")

    if ref.paragraph_count != out.paragraph_count:
        errors.append(
            f"Paragraph count mismatch: ref={ref.paragraph_count}, out={out.paragraph_count}"
        )
    if ref.page_break_count != out.page_break_count:
        errors.append(
            f"Explicit pageBreak count mismatch: ref={ref.page_break_count}, out={out.page_break_count}"
        )
    if ref.column_break_count != out.column_break_count:
        errors.append(
            f"Explicit columnBreak count mismatch: ref={ref.column_break_count}, out={out.column_break_count}"
        )
    if ref.table_count != out.table_count:
        errors.append(f"Table count mismatch: ref={ref.table_count}, out={out.table_count}")
    if ref.table_shapes != out.table_shapes:
        errors.append("Table structure (rowCnt/colCnt/width/height/pageBreak) mismatch")

    td = _ratio_delta(ref.text_char_total_nospace, out.text_char_total_nospace)
    if td > max_text_delta_ratio:
        errors.append(
            "Total text-length difference exceeds the limit: "
            f"ref={ref.text_char_total_nospace}, out={out.text_char_total_nospace}, "
            f"delta={td:.2%}, limit={max_text_delta_ratio:.2%}"
        )

    if len(ref.paragraph_text_lengths) == len(out.paragraph_text_lengths):
        for idx, (a, b) in enumerate(
            zip(ref.paragraph_text_lengths, out.paragraph_text_lengths), start=1
        ):
            if a == 0 and b == 0:
                continue
            pd = _ratio_delta(a, b)
            if pd > max_paragraph_delta_ratio:
                errors.append(
                    f"Paragraph {idx} text-length difference exceeds the limit: "
                    f"ref={a}, out={b}, delta={pd:.2%}, limit={max_paragraph_delta_ratio:.2%}"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check an HWPX output for page-drift risks against a reference"
    )
    parser.add_argument("--reference", "-r", required=True, help="Reference HWPX path")
    parser.add_argument("--output", "-o", required=True, help="Output HWPX path")
    parser.add_argument(
        "--max-text-delta-ratio",
        type=float,
        default=0.15,
        help="Allowed total text-length difference ratio (default: 0.15)",
    )
    parser.add_argument(
        "--max-paragraph-delta-ratio",
        type=float,
        default=0.25,
        help="Allowed per-paragraph text-length difference ratio (default: 0.25)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print metrics as JSON",
    )
    args = parser.parse_args()

    ref_path = Path(args.reference)
    out_path = Path(args.output)

    if not ref_path.exists():
        print(f"Error: reference not found: {ref_path}", file=sys.stderr)
        return 2
    if not out_path.exists():
        print(f"Error: output not found: {out_path}", file=sys.stderr)
        return 2

    ref = collect_metrics(ref_path)
    out = collect_metrics(out_path)

    if args.json:
        print(
            json.dumps(
                {"reference": asdict(ref), "output": asdict(out)},
                ensure_ascii=False,
                indent=2,
            )
        )

    errors = compare_metrics(
        ref,
        out,
        max_text_delta_ratio=args.max_text_delta_ratio,
        max_paragraph_delta_ratio=args.max_paragraph_delta_ratio,
    )
    if errors:
        print("FAIL: page-guard")
        for e in errors:
            print(f" - {e}")
        return 1

    print("PASS: page-guard")
    print(
        "  Paragraph/table/pageBreak structure and text-length differences are within the allowed range."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
