#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test D: regression guards for the four review-reported defects.

Fix 1 [High]  In-cell shape text was mis-attributed to the cell (and the shape
              was dropped from shape_texts). Guard: a <hp:rect> text box placed
              inside a <hp:tc> must (a) NOT leak its drawText into the cell text,
              (b) NOT leave a phantom empty paragraph, and (c) BE harvested into
              shape_texts. Also verified alongside a real nested table so both
              paths (cell text vs. child table vs. child shape) stay separate.

Fix 2 [High]  Cell background color was pulled from a border winBrush instead of
              fillBrush. Guard: a borderFill whose leftBorder/rightBorder brushes
              precede fillBrush must resolve to the fill color, both through the
              file-level resolver (Contents/header.xml) and a bare in-memory
              _StyleResolver.

Fix 3 [Medium] to_matrix(expand_merges=True) crashed on an occupied cell whose
              origin fell outside the grid. Guard: such a grid returns "" for the
              bad slot instead of raising IndexError.

Fix 4 [Low]   Documentation-only. Not directly asserted here, but the (0,0)
              cellAddr fallback and namespace-prefix-agnostic (local-name)
              behavior are exercised by dedicated checks below so the documented
              contract has live coverage.

Extra robustness (requested): coordinate-collision warning, multi-section order,
and an empty table (no <tr>) parsing without crashing.

Run:  python tests/test_fixes.py
Exit code 0 = PASS, 1 = FAIL.
"""

import io
import os
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from hwpx_table_parser import (  # noqa: E402
    HwpxTableParser,
    Table,
    Cell,
    _StyleResolver,
    _parse_xml_bytes,
)
import make_style_fixture  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class Checker:
    def __init__(self):
        self.failures = []
        self.passed = 0

    def check(self, cond, label):
        if cond:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failures.append(label)
            print(f"  FAIL  {label}")

    def summary(self):
        total = self.passed + len(self.failures)
        print(f"\n=== {self.passed}/{total} checks passed ===")
        for f in self.failures:
            print("  - FAIL:", f)
        return not self.failures


# Namespace block used by the in-memory HWPX documents below. Deliberately uses
# the hp/hs/hh prefixes only in the section; the parser must key off local names.
_SEC_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
)
_SEC_CLOSE = "</hs:sec>"


def _mem_hwpx(sections):
    """Build an in-memory .hwpx (bytes) from a list of section XML strings."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        for i, sxml in enumerate(sections):
            zf.writestr(f"Contents/section{i}.xml", sxml)
    buf.seek(0)
    return buf


def _load_bytes(byts):
    """Load a parser from an in-memory zip (ZipFile accepts a file-like)."""
    doc = HwpxTableParser()
    # Reuse the private loader against a file-like object.
    with zipfile.ZipFile(byts, "r") as zf:
        names = zf.namelist()
        from hwpx_table_parser import _pick_first, _sorted_sections

        header_name = _pick_first(names, ["Contents/header.xml", "header.xml"])
        header_bytes = zf.read(header_name) if header_name else None
        doc._resolver = _StyleResolver(header_bytes)
        for sname in _sorted_sections(names):
            doc.sections.append(sname)
            root = _parse_xml_bytes(zf.read(sname))
            doc._parse_section(root, sname)
    return doc


# -----------------------------------------------------------------------------
# Fix 1: in-cell shape
# -----------------------------------------------------------------------------
def test_fix1_incell_shape(chk, cell_shape_path):
    print("\n[Fix 1] in-cell text box (file fixture) ", cell_shape_path)
    doc = HwpxTableParser.load(cell_shape_path)
    print(doc.grid_dump_all())
    print(doc.shape_text_dump())

    t = doc.tables[0]
    host = t.cell_at(0, 1)
    chk.check(host is not None and host.text == "HOST",
              f"host cell text == 'HOST' (no shape bleed) (got {host.text!r})")
    chk.check(host is not None and host.paragraphs == ["HOST"],
              f"host cell has no phantom empty paragraph (got {host.paragraphs})")
    chk.check(all("SHAPE_IN_CELL" not in (c.text or "") for c in t.cells),
              "no cell text contains the shape's 'SHAPE_IN_CELL'")

    shp = [s for s in doc.shape_texts if "SHAPE_IN_CELL" in (s.text or "")]
    chk.check(len(shp) == 1,
              f"exactly 1 shape block harvested for the in-cell rect (got {len(shp)})")
    chk.check(bool(shp) and shp[0].shape_type == "rect",
              "in-cell shape recorded with shape_type='rect'")
    chk.check(bool(shp) and shp[0].source == "textbox",
              "in-cell shape recorded with source='textbox'")

    # The plain neighbor cell is unaffected.
    plain = t.cell_at(0, 0)
    chk.check(plain is not None and plain.text == "PLAINCELL",
              f"neighbor cell intact == 'PLAINCELL' (got {plain.text!r})")


def test_fix1_incell_shape_with_nested_table(chk):
    print("\n[Fix 1] in-memory: cell with BOTH a nested table and a text box")
    # cell holds: text 'C', a nested 1x1 table 'NESTED', and a rect box 'BOX'.
    nested = (
        '<hp:tbl rowCnt="1" colCnt="1">'
        "<hp:tr><hp:tc><hp:subList>"
        "<hp:p><hp:run><hp:t>NESTED</hp:t></hp:run></hp:p>"
        "</hp:subList>"
        '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
        "</hp:tc></hp:tr></hp:tbl>"
    )
    box = (
        "<hp:p><hp:run>"
        "<hp:rect><hp:drawText><hp:subList>"
        "<hp:p><hp:run><hp:t>BOX</hp:t></hp:run></hp:p>"
        "</hp:subList></hp:drawText></hp:rect>"
        "</hp:run></hp:p>"
    )
    xml = (
        '<hp:tbl xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'rowCnt="1" colCnt="1">'
        "<hp:tr><hp:tc><hp:subList>"
        "<hp:p><hp:run><hp:t>C</hp:t></hp:run></hp:p>"
        + nested
        + box
        + "</hp:subList>"
        '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
        "</hp:tc></hp:tr></hp:tbl>"
    )
    root = _parse_xml_bytes(xml.encode("utf-8"))
    p = HwpxTableParser()
    p._resolver = _StyleResolver(None)
    t = p._parse_table(root, source="<mem>", depth=0)
    p.tables.append(t)
    cell = t.cell_at(0, 0)

    chk.check(cell.text == "C",
              f"cell text == 'C' only (nested/box excluded) (got {cell.text!r})")
    chk.check(len(cell.children) == 1 and cell.children[0].cell_at(0, 0).text == "NESTED",
              f"nested table attached with 'NESTED' (got {len(cell.children)} children)")
    chk.check(len(p.shape_texts) == 1 and p.shape_texts[0].text == "BOX",
              f"box harvested into shape_texts (got {[s.text for s in p.shape_texts]})")
    chk.check("NESTED" not in cell.text and "BOX" not in cell.text,
              "cell text excludes both nested-table and shape text")


# -----------------------------------------------------------------------------
# Fix 2: borderFill background from fillBrush, not border winBrush
# -----------------------------------------------------------------------------
def test_fix2_border_fill(chk, header_path):
    print("\n[Fix 2] borderFill background color resolution")
    with open(header_path, "rb") as fh:
        header_bytes = fh.read()
    res = _StyleResolver(header_bytes)

    chk.check(res.fill_bg_color("3") == "#00FF00",
              f"id=3 bg == fill #00FF00 (NOT border #FF0000) (got {res.fill_bg_color('3')!r})")
    chk.check(res.fill_bg_color("2") == "#00FF00",
              f"id=2 (fill only) bg == #00FF00 (got {res.fill_bg_color('2')!r})")
    chk.check(res.fill_bg_color("1") is None,
              f"id=1 (no fill) bg == None (got {res.fill_bg_color('1')!r})")
    chk.check(res.fill_bg_color("4") is None,
              f"id=4 (fill faceColor='none') bg == None (got {res.fill_bg_color('4')!r})")
    chk.check(res.char_is_bold("7") is True,
              "charPr id=7 resolves bold True")

    # In-memory trap: border brush before fill brush, arbitrary namespace prefix.
    trap = (
        '<X:head xmlns:X="urn:x" xmlns:Y="urn:y">'
        '<X:borderFill id="9">'
        '<X:leftBorder><Y:winBrush faceColor="#111111"/></X:leftBorder>'
        '<X:fillBrush><Y:winBrush faceColor="#ABCDEF"/></X:fillBrush>'
        "</X:borderFill></X:head>"
    )
    res2 = _StyleResolver(trap.encode("utf-8"))
    chk.check(res2.fill_bg_color("9") == "#ABCDEF",
              f"namespace-agnostic trap resolves fill #ABCDEF (got {res2.fill_bg_color('9')!r})")


def test_fix2_endtoend_bg(chk, cell_shape_path):
    print("\n[Fix 2] end-to-end cell background via Contents/header.xml")
    doc = HwpxTableParser.load(cell_shape_path)
    t = doc.tables[0]
    chk.check(t.cell_at(0, 0).style.bg_color == "#00FF00",
              f"cell(0,0) bg from borderFill id=3 == #00FF00 "
              f"(got {t.cell_at(0,0).style.bg_color!r})")
    chk.check(t.cell_at(0, 1).style.bg_color == "#00FF00",
              f"cell(0,1) bg from borderFill id=2 == #00FF00 "
              f"(got {t.cell_at(0,1).style.bg_color!r})")


# -----------------------------------------------------------------------------
# Fix 3: to_matrix out-of-range origin guard
# -----------------------------------------------------------------------------
def test_fix3_out_of_range_origin(chk):
    print("\n[Fix 3] to_matrix(expand_merges=True) with out-of-range origin")
    tb = Table(index=0, n_rows=1, n_cols=1)
    occ = Cell(row=0, col=0, is_occupied=True, origin=(9, 9))  # outside 1x1 grid
    tb.grid = [[occ]]
    crashed = False
    result = None
    try:
        result = tb.to_matrix(expand_merges=True)
    except Exception as exc:  # pragma: no cover - failure path
        crashed = True
        print("   unexpected crash:", type(exc).__name__, exc)
    chk.check(not crashed, "to_matrix does not raise on out-of-range origin")
    chk.check(result == [[""]],
              f"out-of-range occupied slot yields '' (got {result})")

    # Sanity: a well-formed merge still expands correctly (no behavior change).
    tb2 = Table(index=1, n_rows=1, n_cols=2)
    origin = Cell(row=0, col=0, col_span=2, text="WIDE")
    occ2 = Cell(row=0, col=1, is_occupied=True, origin=(0, 0))
    tb2.grid = [[origin, occ2]]
    tb2.cells = [origin]
    chk.check(tb2.to_matrix(expand_merges=True) == [["WIDE", "WIDE"]],
              f"valid merge still expands (got {tb2.to_matrix(expand_merges=True)})")


# -----------------------------------------------------------------------------
# Fix 4 coverage: (0,0) cellAddr fallback + namespace-agnostic parsing
# -----------------------------------------------------------------------------
def test_fix4_addr_fallback(chk):
    print("\n[Fix 4] cellAddr missing -> (0,0) fallback; prefix-agnostic")
    # A single-cell table with NO cellAddr and NO cellSpan, custom prefix.
    xml = (
        '<Q:tbl xmlns:Q="urn:whatever" rowCnt="1" colCnt="1">'
        "<Q:tr><Q:tc><Q:subList>"
        "<Q:p><Q:run><Q:t>NOADDR</Q:t></Q:run></Q:p>"
        "</Q:subList></Q:tc></Q:tr></Q:tbl>"
    )
    root = _parse_xml_bytes(xml.encode("utf-8"))
    p = HwpxTableParser()
    p._resolver = _StyleResolver(None)
    t = p._parse_table(root, source="<mem>", depth=0)
    cell = t.cell_at(0, 0)
    chk.check(cell is not None and cell.text == "NOADDR",
              f"cell defaults to (0,0) when cellAddr absent (got {cell})")
    chk.check(cell is not None and cell.row == 0 and cell.col == 0,
              "missing cellAddr -> row==0, col==0")
    chk.check(cell is not None and cell.row_span == 1 and cell.col_span == 1,
              "missing cellSpan -> spans default to 1")


# -----------------------------------------------------------------------------
# Extra robustness
# -----------------------------------------------------------------------------
def test_collision_warns(chk):
    print("\n[Extra] coordinate collision -> validate() warning")
    # Two origin cells both claim (0,0): overlapping cellAddr.
    xml = (
        '<hp:tbl xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'rowCnt="1" colCnt="2">'
        "<hp:tr>"
        "<hp:tc><hp:p><hp:run><hp:t>A</hp:t></hp:run></hp:p>"
        '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="2" rowSpan="1"/></hp:tc>'
        "<hp:tc><hp:p><hp:run><hp:t>B</hp:t></hp:run></hp:p>"
        '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>'
        "</hp:tr></hp:tbl>"
    )
    root = _parse_xml_bytes(xml.encode("utf-8"))
    p = HwpxTableParser()
    p._resolver = _StyleResolver(None)
    t = p._parse_table(root, source="<mem>", depth=0)
    p.tables.append(t)
    warns = p.validate()
    chk.check(any("collision" in w for w in warns),
              f"validate() flags a coordinate collision (got {warns})")


def test_multi_section_order(chk):
    print("\n[Extra] multiple sections parsed in numeric order")

    def one_cell_table(txt):
        return (
            '<hp:tbl rowCnt="1" colCnt="1">'
            "<hp:tr><hp:tc><hp:subList>"
            f"<hp:p><hp:run><hp:t>{txt}</hp:t></hp:run></hp:p>"
            "</hp:subList>"
            '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
            "</hp:tc></hp:tr></hp:tbl>"
        )

    sec0 = _SEC_OPEN + "<hp:p><hp:run>" + one_cell_table("S0") + "</hp:run></hp:p>" + _SEC_CLOSE
    sec1 = _SEC_OPEN + "<hp:p><hp:run>" + one_cell_table("S1") + "</hp:run></hp:p>" + _SEC_CLOSE
    # Provide them out of order in the zip; loader must sort by numeric suffix.
    doc = _load_bytes(_mem_hwpx([sec0, sec1]))
    texts = [t.cell_at(0, 0).text for t in doc.tables]
    chk.check(texts == ["S0", "S1"],
              f"tables ordered S0 then S1 across sections (got {texts})")
    chk.check(len(doc.sections) == 2,
              f"two sections recorded (got {doc.sections})")


def test_empty_table_no_crash(chk):
    print("\n[Extra] empty table (no <tr>) parses without crashing")
    xml = (
        '<hp:tbl xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'rowCnt="0" colCnt="0"></hp:tbl>'
    )
    root = _parse_xml_bytes(xml.encode("utf-8"))
    p = HwpxTableParser()
    p._resolver = _StyleResolver(None)
    crashed = False
    try:
        t = p._parse_table(root, source="<mem>", depth=0)
        p.tables.append(t)
        _ = t.to_matrix()
        _ = t.to_markdown()
        _ = t.grid_dump()
        warns = p.validate()
    except Exception as exc:  # pragma: no cover - failure path
        crashed = True
        print("   crash:", type(exc).__name__, exc)
        warns = []
    chk.check(not crashed, "empty table survives matrix/markdown/grid/validate")
    chk.check(t.n_rows == 0 and t.n_cols == 0, f"empty table is 0x0 (got {t.n_rows}x{t.n_cols})")
    chk.check(any("empty grid" in w for w in warns),
              f"validate() notes the empty grid (got {warns})")


def main():
    chk = Checker()
    header_path, cell_shape_path = make_style_fixture.build_all()

    test_fix1_incell_shape(chk, cell_shape_path)
    test_fix1_incell_shape_with_nested_table(chk)
    test_fix2_border_fill(chk, header_path)
    test_fix2_endtoend_bg(chk, cell_shape_path)
    test_fix3_out_of_range_origin(chk)
    test_fix4_addr_fallback(chk)
    test_collision_warns(chk)
    test_multi_section_order(chk)
    test_empty_table_no_crash(chk)

    return 0 if chk.summary() else 1


if __name__ == "__main__":
    raise SystemExit(main())
