#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fixture builder for drawing-object / text-box verification (test C).

The real sample document contains no shapes, so to exercise text-box text
extraction and "table inside a text box" handling we synthesize a fixture that
mirrors the OWPML drawing-object model:

    * a drawing object (<hp:rect>, <hp:ellipse>, ...) owns an <hp:drawText>
      whose <hp:subList> holds the text-box paragraphs (and possibly a table);
    * a <hp:container> groups child shapes recursively.

fixtures/shapes_min.hwpx  (single section, document order):

    1. BODY table (1x1, text "BODYTAB")           -> body table

    2. <hp:rect> text box:
         paragraph "RECT TITLE" + "line two"
         + a 2x2 table (bx00..bx11)               -> text-box text + text-box table

    3. <hp:ellipse> text box:
         paragraph "ELLIPSE ONLY"                 -> text-box text, no table

    4. <hp:container> group of:
         <hp:rect>  "GROUP RECT"
         <hp:line>  "GROUP LINE"                  -> nested container_path shapes

These use only the structural elements the parser relies on
(rect/ellipse/line/container/drawText/subList and tbl/tr/tc/cellAddr/cellSpan
and p/run/t). They are intentionally NOT full-fidelity HWPX; the parser
tolerates that (formatting is best-effort and header.xml is optional).

Run:  python tests/make_shape_fixture.py
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


def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _paras(text):
    """One <hp:p> per line of ``text``."""
    out = ""
    for line in text.split("\n"):
        out += (
            '<hp:p><hp:run charPrIDRef="0"><hp:t>'
            + _xml_escape(line)
            + "</hp:t></hp:run></hp:p>"
        )
    return out


def _cell(col, row, cspan, rspan, text):
    """One <hp:tc> with the given address/span and a single paragraph."""
    return (
        '<hp:tc borderFillIDRef="1">'
        "<hp:subList>"
        + _paras(text)
        + "</hp:subList>"
        + f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        + f'<hp:cellSpan colSpan="{cspan}" rowSpan="{rspan}"/>'
        + '<hp:cellSz width="1000" height="1000"/>'
        + "</hp:tc>"
    )


def _table_1x1(text):
    return (
        '<hp:tbl rowCnt="1" colCnt="1" borderFillIDRef="1">'
        "<hp:tr>" + _cell(0, 0, 1, 1, text) + "</hp:tr>"
        "</hp:tbl>"
    )


def _table_2x2():
    """A 2x2 table (bx00..bx11) used inside the rect text box."""
    return (
        '<hp:tbl rowCnt="2" colCnt="2" borderFillIDRef="1">'
        "<hp:tr>"
        + _cell(0, 0, 1, 1, "bx00")
        + _cell(1, 0, 1, 1, "bx01")
        + "</hp:tr>"
        "<hp:tr>"
        + _cell(0, 1, 1, 1, "bx10")
        + _cell(1, 1, 1, 1, "bx11")
        + "</hp:tr>"
        "</hp:tbl>"
    )


def _shape(local, inner):
    """Wrap ``inner`` (paragraphs and/or a table) in <hp:{local}><hp:drawText>."""
    return (
        f"<hp:{local}>"
        '<hp:sz width="2000" height="1000"/>'
        '<hp:pos treatAsChar="0"/>'
        "<hp:drawText>"
        "<hp:subList>" + inner + "</hp:subList>"
        "</hp:drawText>"
        f"</hp:{local}>"
    )


def _container(children):
    """A <hp:container> grouping the given child shape XML fragments."""
    return (
        "<hp:container>"
        '<hp:sz width="4000" height="2000"/>'
        '<hp:pos treatAsChar="0"/>'
        + "".join(children)
        + "</hp:container>"
    )


def _wrap_para(inner):
    """Wrap a fragment in a body paragraph/run like real HWPX does."""
    return '<hp:p><hp:run charPrIDRef="0">' + inner + "</hp:run></hp:p>"


def _shapes_section():
    parts = [SEC_OPEN]
    # 1. body table
    parts.append(_wrap_para(_table_1x1("BODYTAB")))
    # 2. rect text box: two-line paragraph + a 2x2 table
    rect_inner = _paras("RECT TITLE\nline two") + _table_2x2()
    parts.append(_wrap_para(_shape("rect", rect_inner)))
    # 3. ellipse text box: text only
    parts.append(_wrap_para(_shape("ellipse", _paras("ELLIPSE ONLY"))))
    # 4. container group: rect + line, each with text
    grp = _container(
        [
            _shape("rect", _paras("GROUP RECT")),
            _shape("line", _paras("GROUP LINE")),
        ]
    )
    parts.append(_wrap_para(grp))
    parts.append(SEC_CLOSE)
    return "".join(parts)


def _write_hwpx(path, section_xml):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", section_xml)


def build_all():
    os.makedirs(FIX_DIR, exist_ok=True)
    shapes_path = os.path.join(FIX_DIR, "shapes_min.hwpx")
    _write_hwpx(shapes_path, _shapes_section())
    print("wrote", shapes_path)
    return shapes_path


if __name__ == "__main__":
    build_all()
