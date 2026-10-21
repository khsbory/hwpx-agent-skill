#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test C: drawing-object / text-box text extraction + tables inside text boxes.

The real sample document has no shapes, so we build a synthetic fixture
(see make_shape_fixture.py) and assert the parser:

* harvests text-box paragraph text into ``shape_texts`` with source='textbox';
* collects a table living inside a text box into ``tables`` and reconstructs its
  cell grid;
* distinguishes body tables from text-box tables (container / body_tables /
  textbox_tables);
* walks a <hp:container> group recursively, tagging each child with its nested
  container_path;
* preserves document order (body first, then shapes);
* keeps a pure in-memory unit check of a rect text box as an extra guard.

Run:  python tests/test_shapes.py
Exit code 0 = PASS, 1 = FAIL.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from hwpx_table_parser import HwpxTableParser  # noqa: E402
import make_shape_fixture  # noqa: E402

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


def _find_shape(doc, shape_type, needle):
    for s in doc.shape_texts:
        if s.shape_type == shape_type and needle in (s.text or ""):
            return s
    return None


def test_shapes(chk, shapes_path):
    print("\n[Shapes fixture] ", shapes_path)
    doc = HwpxTableParser.load(shapes_path)
    print(doc.shape_text_dump())
    print(doc.grid_dump_all())

    # -- Text-box text extraction -------------------------------------------
    chk.check(len(doc.shape_texts) == 4,
              f"4 shape text blocks harvested (got {len(doc.shape_texts)})")
    chk.check(all(s.source == "textbox" for s in doc.shape_texts),
              "every shape block has source == 'textbox'")

    rect = _find_shape(doc, "rect", "RECT TITLE")
    chk.check(rect is not None, "found rect text box containing 'RECT TITLE'")
    if rect is not None:
        chk.check(rect.paragraphs == ["RECT TITLE", "line two"],
                  f"rect paragraphs == ['RECT TITLE','line two'] (got {rect.paragraphs})")
        chk.check(rect.text == "RECT TITLE\nline two",
                  f"rect joined text preserves line break (got {rect.text!r})")

    ell = _find_shape(doc, "ellipse", "ELLIPSE ONLY")
    chk.check(ell is not None and ell.text == "ELLIPSE ONLY",
              "ellipse text box text == 'ELLIPSE ONLY'")
    chk.check(ell is not None and not ell.tables,
              "ellipse text box has no inner table")

    # -- Table inside a text box --------------------------------------------
    tb_tables = doc.textbox_tables()
    body_tables = doc.body_tables()
    chk.check(len(body_tables) == 1 and body_tables[0].cell_at(0, 0).text == "BODYTAB",
              f"exactly 1 body table 'BODYTAB' (got {[t.cell_at(0,0).text for t in body_tables]})")
    chk.check(len(tb_tables) == 1,
              f"exactly 1 text-box table (got {len(tb_tables)})")

    if tb_tables:
        bt = tb_tables[0]
        chk.check(bt.container == "rect",
                  f"text-box table container == 'rect' (got {bt.container!r})")
        chk.check(bt.container_path == ["rect"],
                  f"text-box table container_path == ['rect'] (got {bt.container_path})")
        chk.check(bt.n_rows == 2 and bt.n_cols == 2,
                  f"text-box table grid is 2x2 (got {bt.n_rows}x{bt.n_cols})")
        chk.check(bt.to_matrix() == [["bx00", "bx01"], ["bx10", "bx11"]],
                  f"text-box table grid restored (got {bt.to_matrix()})")
        # The rect's text block references the same table object.
        chk.check(rect is not None and bt in rect.tables,
                  "rect text block references its inner table")

    # -- Body vs text-box tables are distinct -------------------------------
    body_ids = {id(t) for t in body_tables}
    tb_ids = {id(t) for t in tb_tables}
    chk.check(body_ids.isdisjoint(tb_ids),
              "body tables and text-box tables are disjoint sets")
    chk.check(len(doc.tables) == 2,
              f"2 top-level tables total (1 body + 1 text-box) (got {len(doc.tables)})")

    # -- Document order: body table comes before the text-box table ---------
    chk.check(doc.tables[0].cell_at(0, 0).text == "BODYTAB",
              "document order: body table is table #0")
    chk.check(doc.tables[1].container == "rect",
              "document order: text-box table follows the body table")

    # -- Container (shape group) recursion ----------------------------------
    grp_rect = _find_shape(doc, "rect", "GROUP RECT")
    grp_line = _find_shape(doc, "line", "GROUP LINE")
    chk.check(grp_rect is not None and grp_rect.container_path == ["container", "rect"],
              f"grouped rect container_path == ['container','rect'] "
              f"(got {grp_rect.container_path if grp_rect else None})")
    chk.check(grp_line is not None and grp_line.container_path == ["container", "line"],
              f"grouped line container_path == ['container','line'] "
              f"(got {grp_line.container_path if grp_line else None})")

    # -- JSON export is backward compatible + carries the new data ----------
    js = doc.to_json()
    chk.check('"shapeTexts"' in js, "JSON contains a shapeTexts array")
    chk.check('"container": "rect"' in js, "JSON tags the text-box table container")
    chk.check('"source": "textbox"' in js, "JSON marks shape text source=textbox")

    # -- validate() stays clean for these perfectly tiled tables ------------
    warns = doc.validate()
    chk.check(len(warns) == 0, f"validate() clean for shape fixture (got {warns})")


def test_inmemory_rect(chk):
    """Parse a raw rect-text-box snippet through the same code path (no file)."""
    print("\n[In-memory snippet] rect text box with inner 1x1 table")
    from hwpx_table_parser import _parse_xml_bytes, _StyleResolver  # internal but stable

    xml = (
        '<hp:rect xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        "<hp:drawText><hp:subList>"
        "<hp:p><hp:run><hp:t>MEMO</hp:t></hp:run></hp:p>"
        '<hp:tbl rowCnt="1" colCnt="1">'
        "<hp:tr><hp:tc><hp:subList><hp:p><hp:run><hp:t>C0</hp:t></hp:run></hp:p></hp:subList>"
        '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
        "</hp:tc></hp:tr></hp:tbl>"
        "</hp:subList></hp:drawText></hp:rect>"
    )
    root = _parse_xml_bytes(xml.encode("utf-8"))
    p = HwpxTableParser()
    p._resolver = _StyleResolver(None)
    # Drive the shape parser directly on the rect element.
    p._parse_shape(root, source="<memory>", container_path=[])

    chk.check(len(p.shape_texts) == 1 and p.shape_texts[0].text == "MEMO",
              f"snippet harvested rect text 'MEMO' (got {[s.text for s in p.shape_texts]})")
    chk.check(len(p.tables) == 1 and p.tables[0].container == "rect",
              f"snippet table tagged container='rect' (got "
              f"{[(t.container) for t in p.tables]})")
    chk.check(p.tables and p.tables[0].to_matrix() == [["C0"]],
              f"snippet inner table grid == [['C0']] (got "
              f"{p.tables[0].to_matrix() if p.tables else None})")


def main():
    chk = Checker()
    shapes_path = make_shape_fixture.build_all()
    test_shapes(chk, shapes_path)
    test_inmemory_rect(chk)
    return 0 if chk.summary() else 1


if __name__ == "__main__":
    raise SystemExit(main())
