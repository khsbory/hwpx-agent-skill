#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fixture builder for style / regression verification (test D).

Two artifacts are produced.

(1) fixtures/style_header.xml
    A minimal ``Contents/header.xml`` payload that exercises the borderFill
    background-color resolver. The critical case: a borderFill whose *border*
    definitions (leftBorder/rightBorder) each own a ``<hc:winBrush>`` that
    precedes the real background ``<hh:fillBrush><hc:winBrush>``. A resolver that
    grabbed "the first winBrush under borderFill" would wrongly return the border
    color; the correct behavior scopes to ``fillBrush`` first.

        borderFill id="1"  -> no fill        -> bg None (default frame)
        borderFill id="2"  -> fillBrush only -> bg #00FF00
        borderFill id="3"  -> leftBorder #FF0000 + rightBorder #0000FF
                              THEN fillBrush #00FF00
                                             -> bg MUST be #00FF00 (not #FF0000)
        borderFill id="4"  -> fillBrush faceColor="none" -> bg None (transparent)

    Also defines charPr id="7" carrying <hh:bold/> so the bold path stays
    exercised.

(2) fixtures/cell_shape.hwpx
    A single 1x2 body table:
        * cell (0,0): text "PLAINCELL", borderFillIDRef="3" (bg #00FF00)
        * cell (0,1): text "HOST" followed by an in-cell <hp:rect> text box whose
          drawText holds "SHAPE_IN_CELL", borderFillIDRef="2" (bg #00FF00)
    This drives the in-cell-shape fix end to end: the shape text must NOT bleed
    into the cell text, must NOT leave a phantom empty paragraph, and must be
    harvested into shape_texts; the cell backgrounds come from fillBrush.

The header ships inside the same zip as ``Contents/header.xml`` so the parser's
normal style-resolution path (not just an in-memory _StyleResolver) is covered.

Run:  python tests/make_style_fixture.py
"""

import os
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
FIX_DIR = os.path.join(_HERE, "fixtures")

# header.xml root mirrors a real HWPX head so namespace discovery is realistic.
HEAD_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hh:head '
    'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
)
HEAD_CLOSE = "</hh:head>"

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


def _border(local, color):
    """A single border side holding a winBrush (this is the *trap* brush)."""
    return f'<hh:{local}><hc:winBrush faceColor="{color}"/></hh:{local}>'


def _fill(color):
    """The real background fill: fillBrush > winBrush@faceColor."""
    return f'<hh:fillBrush><hc:winBrush faceColor="{color}"/></hh:fillBrush>'


def build_header_xml():
    parts = [HEAD_OPEN, "<hh:refList>"]

    # charProperties with a bold charPr id=7.
    parts.append("<hh:charProperties>")
    parts.append('<hh:charPr id="7"><hh:bold/></hh:charPr>')
    parts.append('<hh:charPr id="0"/>')
    parts.append("</hh:charProperties>")

    # borderFills.
    parts.append("<hh:borderFills>")
    # id=1: no fill at all.
    parts.append('<hh:borderFill id="1"></hh:borderFill>')
    # id=2: fillBrush only.
    parts.append('<hh:borderFill id="2">' + _fill("#00FF00") + "</hh:borderFill>")
    # id=3: border winBrushes FIRST, then the real fillBrush.
    parts.append(
        '<hh:borderFill id="3">'
        + _border("leftBorder", "#FF0000")
        + _border("rightBorder", "#0000FF")
        + _fill("#00FF00")
        + "</hh:borderFill>"
    )
    # id=4: transparent fill (faceColor='none') -> None.
    parts.append('<hh:borderFill id="4">' + _fill("none") + "</hh:borderFill>")
    parts.append("</hh:borderFills>")

    parts.append("</hh:refList>")
    parts.append(HEAD_CLOSE)
    return "".join(parts)


def _paras(text, char_pr="0"):
    out = ""
    for line in text.split("\n"):
        out += (
            f'<hp:p><hp:run charPrIDRef="{char_pr}"><hp:t>'
            + _xml_escape(line)
            + "</hp:t></hp:run></hp:p>"
        )
    return out


def _rect_textbox(text):
    """An <hp:rect> text box wrapped in a paragraph/run, as it sits in a cell."""
    return (
        '<hp:p><hp:run charPrIDRef="0">'
        "<hp:rect>"
        '<hp:sz width="2000" height="1000"/>'
        '<hp:pos treatAsChar="0"/>'
        "<hp:drawText><hp:subList>"
        + _paras(text)
        + "</hp:subList></hp:drawText>"
        "</hp:rect>"
        "</hp:run></hp:p>"
    )


def _cell(col, row, border_fill_id, inner):
    return (
        f'<hp:tc borderFillIDRef="{border_fill_id}">'
        "<hp:subList>"
        + inner
        + "</hp:subList>"
        + f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        + '<hp:cellSpan colSpan="1" rowSpan="1"/>'
        + '<hp:cellSz width="1000" height="1000"/>'
        + "</hp:tc>"
    )


def build_cell_shape_section():
    # cell (0,0): plain text, bg from borderFill id=3 (fillBrush #00FF00).
    c0 = _cell(0, 0, "3", _paras("PLAINCELL"))
    # cell (0,1): "HOST" text then an in-cell rect text box; bg id=2 (#00FF00).
    c1_inner = _paras("HOST") + _rect_textbox("SHAPE_IN_CELL")
    c1 = _cell(1, 0, "2", c1_inner)
    tbl = (
        '<hp:tbl rowCnt="1" colCnt="2" borderFillIDRef="1">'
        "<hp:tr>" + c0 + c1 + "</hp:tr>"
        "</hp:tbl>"
    )
    return (
        SEC_OPEN
        + '<hp:p><hp:run charPrIDRef="0">'
        + tbl
        + "</hp:run></hp:p>"
        + SEC_CLOSE
    )


def _write_hwpx(path, section_xml, header_xml):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/header.xml", header_xml)
        zf.writestr("Contents/section0.xml", section_xml)


def build_all():
    os.makedirs(FIX_DIR, exist_ok=True)
    header_xml = build_header_xml()

    header_path = os.path.join(FIX_DIR, "style_header.xml")
    with open(header_path, "w", encoding="utf-8") as fh:
        fh.write(header_xml)
    print("wrote", header_path)

    cell_shape_path = os.path.join(FIX_DIR, "cell_shape.hwpx")
    _write_hwpx(cell_shape_path, build_cell_shape_section(), header_xml)
    print("wrote", cell_shape_path)

    return header_path, cell_shape_path


if __name__ == "__main__":
    build_all()
