#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test B: merged-cell grid reconstruction + nested-table recursion.

Because the real document has no merges, we build synthetic fixtures
(see make_merge_fixture.py) and assert the parser reconstructs them exactly.

Covered:
* colSpan / rowSpan placement and "merge-occupied" placeholder slots that
  point back to their origin.
* to_matrix(expand_merges=True/False) behavior over merged regions.
* validate() returns no warnings for a perfectly tiled merged grid.
* recursive parsing of a nested table attached to its owning cell.
* a pure in-memory unit check of grid reconstruction from an XML snippet
  (no file needed) as an extra guard.

Run:  python tests/test_merge_and_nested.py
Exit code 0 = PASS, 1 = FAIL.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hwpx_table_parser import HwpxTableParser  # noqa: E402
import make_merge_fixture  # noqa: E402

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


def test_merged(chk, merged_path):
    print("\n[Merged grid] ", merged_path)
    doc = HwpxTableParser.load(merged_path)
    chk.check(len(doc.tables) == 1, f"one table parsed (got {len(doc.tables)})")
    t = doc.tables[0]
    print(t.grid_dump())

    chk.check(t.n_rows == 2 and t.n_cols == 3, f"grid is 2x3 (got {t.n_rows}x{t.n_cols})")

    a = t.cell_at(0, 0)
    b = t.cell_at(0, 2)
    c = t.cell_at(1, 0)
    d = t.cell_at(1, 1)

    # Origin cells carry the right spans and text.
    chk.check(a is not None and a.text == "A" and a.col_span == 2 and a.row_span == 1,
              "A at (0,0) text=A colSpan=2")
    chk.check(b is not None and b.text == "B" and b.row_span == 2 and b.col_span == 1,
              "B at (0,2) text=B rowSpan=2")
    chk.check(c is not None and c.text == "C", "C at (1,0) text=C")
    chk.check(d is not None and d.text == "D", "D at (1,1) text=D")

    # Occupied placeholders point back to their origin.
    occ_01 = t.cell_at(0, 1)
    occ_12 = t.cell_at(1, 2)
    chk.check(occ_01 is not None and occ_01.is_occupied and occ_01.origin == (0, 0),
              "(0,1) is merge-occupied by A origin (0,0)")
    chk.check(occ_12 is not None and occ_12.is_occupied and occ_12.origin == (0, 2),
              "(1,2) is merge-occupied by B origin (0,2)")

    # Matrix with expansion repeats the origin text across covered slots.
    mat_exp = t.to_matrix(expand_merges=True)
    chk.check(mat_exp[0] == ["A", "A", "B"], f"row0 expand == [A,A,B] (got {mat_exp[0]})")
    chk.check(mat_exp[1] == ["C", "D", "B"], f"row1 expand == [C,D,B] (got {mat_exp[1]})")

    # Matrix without expansion leaves covered slots blank.
    mat_raw = t.to_matrix(expand_merges=False)
    chk.check(mat_raw[0] == ["A", "", "B"], f"row0 raw == [A,'',B] (got {mat_raw[0]})")
    chk.check(mat_raw[1] == ["C", "D", ""], f"row1 raw == [C,D,''] (got {mat_raw[1]})")

    # A perfectly tiled merged grid must produce zero warnings.
    warns = doc.validate()
    chk.check(len(warns) == 0, f"validate() clean for tiled merges (got {warns})")


def test_nested(chk, nested_path):
    print("\n[Nested table] ", nested_path)
    doc = HwpxTableParser.load(nested_path)
    # Only the OUTER table is top-level; the inner one is attached to a cell.
    chk.check(len(doc.tables) == 1, f"one top-level table (got {len(doc.tables)})")
    outer = doc.tables[0]
    chk.check(outer.n_rows == 1 and outer.n_cols == 1,
              f"outer is 1x1 (got {outer.n_rows}x{outer.n_cols})")
    cell = outer.cell_at(0, 0)
    chk.check(cell is not None and "OUTER" in (cell.text or ""),
              "outer cell text contains OUTER")
    chk.check(cell is not None and len(cell.children) == 1,
              f"outer cell has 1 nested table (got {len(cell.children) if cell else 0})")
    if cell and cell.children:
        inner = cell.children[0]
        chk.check(inner.depth == 1, f"nested table depth==1 (got {inner.depth})")
        chk.check(inner.n_rows == 2 and inner.n_cols == 2,
                  f"nested is 2x2 (got {inner.n_rows}x{inner.n_cols})")
        inner_mat = inner.to_matrix()
        chk.check(inner_mat == [["in00", "in01"], ["in10", "in11"]],
                  f"nested matrix correct (got {inner_mat})")
        # The OUTER cell's own text must NOT absorb the nested table's text.
        chk.check("in00" not in (cell.text or ""),
                  "outer cell text excludes nested table text")

    # JSON should embed nested under the owning cell.
    js = doc.to_json()
    chk.check('"nested"' in js, "JSON contains a nested table block")


def test_inmemory_snippet(chk):
    """Parse a raw XML snippet through the same code path (no file)."""
    print("\n[In-memory snippet] colSpan=3 banner over a 3-col table")
    from hwpx_table_parser import _parse_xml_bytes  # internal but stable

    xml = (
        '<hp:tbl xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'rowCnt="2" colCnt="3">'
        "<hp:tr><hp:tc>"
        "<hp:p><hp:run><hp:t>BANNER</hp:t></hp:run></hp:p>"
        '<hp:cellAddr colAddr="0" rowAddr="0"/>'
        '<hp:cellSpan colSpan="3" rowSpan="1"/></hp:tc></hp:tr>'
        "<hp:tr>"
        '<hp:tc><hp:p><hp:run><hp:t>x</hp:t></hp:run></hp:p>'
        '<hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>'
        '<hp:tc><hp:p><hp:run><hp:t>y</hp:t></hp:run></hp:p>'
        '<hp:cellAddr colAddr="1" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>'
        '<hp:tc><hp:p><hp:run><hp:t>z</hp:t></hp:run></hp:p>'
        '<hp:cellAddr colAddr="2" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>'
        "</hp:tr></hp:tbl>"
    )
    root = _parse_xml_bytes(xml.encode("utf-8"))
    p = HwpxTableParser()
    from hwpx_table_parser import _StyleResolver
    p._resolver = _StyleResolver(None)
    table = p._parse_table(root, source="<memory>", depth=0)
    chk.check(table.n_rows == 2 and table.n_cols == 3,
              f"snippet grid 2x3 (got {table.n_rows}x{table.n_cols})")
    banner = table.cell_at(0, 0)
    chk.check(banner.text == "BANNER" and banner.col_span == 3,
              "banner spans 3 columns")
    chk.check(table.cell_at(0, 1).is_occupied and table.cell_at(0, 2).is_occupied,
              "(0,1) and (0,2) are merge-occupied")
    chk.check(table.to_matrix()[1] == ["x", "y", "z"],
              f"second row == [x,y,z] (got {table.to_matrix()[1]})")


def main():
    chk = Checker()
    merged_path, nested_path = make_merge_fixture.build_all()
    test_merged(chk, merged_path)
    test_nested(chk, nested_path)
    test_inmemory_snippet(chk)
    return 0 if chk.summary() else 1


if __name__ == "__main__":
    raise SystemExit(main())
