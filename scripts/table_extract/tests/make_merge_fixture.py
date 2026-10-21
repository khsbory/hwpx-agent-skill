#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fixture builder for merge-grid verification (test B).

The real document has no merged cells, so to exercise the rowSpan/colSpan grid
reconstruction we synthesize two fixtures:

(1) fixtures/merged_min.hwpx
    A *minimal but valid* HWPX zip whose section0.xml contains a single table
    with deliberate merges:

        +---------------------------+---------+
        |          A (colSpan=2)    |   B     |   row 0
        +-------------+-------------+         |
        |   C         |   D         |  (B has rowSpan=2)   row 1
        +-------------+-------------+---------+

    Layout / cellAddr+cellSpan:
        A: addr(0,0) span(col=2,row=1)   -> covers (0,0),(0,1)
        B: addr(0,2) span(col=1,row=2)   -> covers (0,2),(1,2)
        C: addr(1,0) span(1,1)
        D: addr(1,1) span(1,1)
    Expected grid (2 rows x 3 cols):
        (0,0)=A  (0,1)=^A   (0,2)=B
        (1,0)=C  (1,1)=D    (1,2)=^B

(2) fixtures/nested.hwpx
    A 1x1 table whose single cell contains a nested 2x2 table, to verify
    recursive nested-table attachment.

These are written using only the structural elements the parser relies on
(tbl/tr/tc/cellAddr/cellSpan and p/run/t). They are intentionally NOT
full-fidelity HWPX (no header.xml styles needed); the parser tolerates that.

Run:  python tests/make_merge_fixture.py
"""

import os
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
FIX_DIR = os.path.join(_HERE, "fixtures")

# Namespace block mirrors a real HWPX section root so dynamic namespace
# discovery has something realistic to chew on.
SEC_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec '
    'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
)
SEC_CLOSE = "</hs:sec>"


def _cell(col, row, cspan, rspan, text, extra_children=""):
    """Build one <hp:tc> with the given address/span and a single paragraph.

    ``extra_children`` lets us inject a nested table inside the cell.
    """
    paras = ""
    for line in text.split("\n"):
        paras += (
            '<hp:p><hp:run charPrIDRef="0"><hp:t>'
            + _xml_escape(line)
            + "</hp:t></hp:run></hp:p>"
        )
    return (
        '<hp:tc borderFillIDRef="1">'
        "<hp:subList>"
        + paras
        + extra_children
        + "</hp:subList>"
        + f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        + f'<hp:cellSpan colSpan="{cspan}" rowSpan="{rspan}"/>'
        + '<hp:cellSz width="1000" height="1000"/>'
        + "</hp:tc>"
    )


def _xml_escape(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _merged_table_xml():
    """The merge table described in the module docstring."""
    # Row 0: A(colSpan2) + B(rowSpan2)
    row0 = (
        "<hp:tr>"
        + _cell(0, 0, 2, 1, "A")
        + _cell(2, 0, 1, 2, "B")
        + "</hp:tr>"
    )
    # Row 1: C, D (col 2 is covered by B's rowSpan, so no tc emitted there)
    row1 = (
        "<hp:tr>"
        + _cell(0, 1, 1, 1, "C")
        + _cell(1, 1, 1, 1, "D")
        + "</hp:tr>"
    )
    return (
        '<hp:tbl rowCnt="2" colCnt="3" borderFillIDRef="1">'
        + row0
        + row1
        + "</hp:tbl>"
    )


def _nested_table_xml():
    """A 2x2 inner table to be embedded in a 1x1 outer table cell."""
    inner = (
        '<hp:tbl rowCnt="2" colCnt="2" borderFillIDRef="1">'
        "<hp:tr>"
        + _cell(0, 0, 1, 1, "in00")
        + _cell(1, 0, 1, 1, "in01")
        + "</hp:tr>"
        "<hp:tr>"
        + _cell(0, 1, 1, 1, "in10")
        + _cell(1, 1, 1, 1, "in11")
        + "</hp:tr>"
        "</hp:tbl>"
    )
    outer_cell = _cell(0, 0, 1, 1, "OUTER", extra_children=inner)
    return (
        '<hp:tbl rowCnt="1" colCnt="1" borderFillIDRef="1">'
        "<hp:tr>" + outer_cell + "</hp:tr>"
        "</hp:tbl>"
    )


def _wrap_section(table_xml):
    # Wrap the table inside a paragraph/run like real HWPX does.
    return (
        SEC_OPEN
        + '<hp:p><hp:run charPrIDRef="0">'
        + table_xml
        + "</hp:run></hp:p>"
        + SEC_CLOSE
    )


def _write_hwpx(path, section_xml):
    """Write a minimal HWPX zip with just mimetype + the section."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype should be first and stored, per OCF; not strictly required
        # by our parser but keeps the file recognizable.
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", section_xml)


def build_all():
    os.makedirs(FIX_DIR, exist_ok=True)

    merged_path = os.path.join(FIX_DIR, "merged_min.hwpx")
    _write_hwpx(merged_path, _wrap_section(_merged_table_xml()))
    print("wrote", merged_path)

    nested_path = os.path.join(FIX_DIR, "nested.hwpx")
    _write_hwpx(nested_path, _wrap_section(_nested_table_xml()))
    print("wrote", nested_path)

    return merged_path, nested_path


if __name__ == "__main__":
    build_all()
