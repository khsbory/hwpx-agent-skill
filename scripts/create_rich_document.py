#!/usr/bin/env python3
"""create_rich_document.py -- Generate richly formatted HWPX documents via XML-first approach.

Instead of relying on the python-hwpx (hwpx package) high-level API, this script:
1. Copies the base template from templates/base/
2. Dynamically injects charPr, paraPr, borderFill definitions into header.xml
3. Builds section0.xml content (paragraphs, tables, headers/footers)
4. Uses build_hwpx.pack_hwpx() for final packaging

Usage:
    python create_rich_document.py --input content.json --output result.hwpx
    cat content.json | python create_rich_document.py --output result.hwpx
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from lxml import etree

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATES_DIR = SKILL_DIR / "templates"
BASE_DIR = TEMPLATES_DIR / "base"

# Import pack_hwpx from build_hwpx
sys.path.insert(0, str(SCRIPT_DIR))
from build_hwpx import pack_hwpx  # noqa: E402
from document_safety import atomic_output_path, require_distinct_paths, parse_xml
from validate import validate

# ---------------------------------------------------------------------------
# XML namespaces
# ---------------------------------------------------------------------------
NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
}

# Full namespace map for root elements (must match base template)
NSMAP_SECTION = {
    "ha": "http://www.hancom.co.kr/hwpml/2011/app",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hp10": "http://www.hancom.co.kr/hwpml/2016/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hhs": "http://www.hancom.co.kr/hwpml/2011/history",
    "hm": "http://www.hancom.co.kr/hwpml/2011/master-page",
    "hpf": "http://www.hancom.co.kr/schema/2011/hpf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf/",
    "ooxmlchart": "http://www.hancom.co.kr/hwpml/2016/ooxmlchart",
    "hwpunitchar": "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar",
    "epub": "http://www.idpf.org/2007/ops",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
}


def _tag(prefix: str, local: str) -> str:
    """Build a Clark-notation tag from namespace prefix and local name."""
    uri = NS.get(prefix)
    if uri is None:
        uri = NSMAP_SECTION.get(prefix, "")
    return f"{{{uri}}}{local}"


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------
MM_TO_HWPUNIT = 283.46  # 1 mm ~ 283.46 HWPUNIT


def mm_to_hwpunit(mm: float) -> int:
    """Convert millimeters to HWPUNIT (internal coordinate unit)."""
    return int(round(mm * MM_TO_HWPUNIT))


def pt_to_height(pt: float) -> int:
    """Font size in pt to charPr height value (1pt = 100)."""
    return int(round(pt * 100))


# Paper sizes in HWPUNIT (width, height)
PAPER_SIZES = {
    "A4": (59528, 84186),
    "B5": (51592, 72850),
    "A3": (84186, 119054),
    "Letter": (61200, 79200),
    "Legal": (61200, 100800),
}

# ---------------------------------------------------------------------------
# ID counters -- track next available IDs from existing base template
# ---------------------------------------------------------------------------
# Base template already has: borderFill 1-2, charPr 0-6, paraPr 0-19, style 0-22, font id 0-1
BASE_BORDERFILL_MAX = 2
BASE_CHARPR_MAX = 6
BASE_PARAPR_MAX = 19
BASE_STYLE_MAX = 22
BASE_FONT_MAX = 1  # per language


class IDAllocator:
    """Allocates sequential IDs starting after existing base template IDs."""

    def __init__(self):
        """Initialize counters starting after existing base template IDs."""
        self.next_borderfill = BASE_BORDERFILL_MAX + 1
        self.next_charpr = BASE_CHARPR_MAX + 1
        self.next_parapr = BASE_PARAPR_MAX + 1
        self.next_style = BASE_STYLE_MAX + 1
        self.next_font = BASE_FONT_MAX + 1  # per-language font id

    def alloc_borderfill(self) -> int:
        """Allocate and return the next available borderFill ID."""
        bid = self.next_borderfill
        self.next_borderfill += 1
        return bid

    def alloc_charpr(self) -> int:
        """Allocate and return the next available charPr ID."""
        cid = self.next_charpr
        self.next_charpr += 1
        return cid

    def alloc_parapr(self) -> int:
        """Allocate and return the next available paraPr ID."""
        pid = self.next_parapr
        self.next_parapr += 1
        return pid

    def alloc_style(self) -> int:
        """Allocate and return the next available style ID."""
        sid = self.next_style
        self.next_style += 1
        return sid

    def alloc_font(self) -> int:
        """Allocate and return the next available per-language font ID."""
        fid = self.next_font
        self.next_font += 1
        return fid


# ---------------------------------------------------------------------------
# Header.xml manipulation
# ---------------------------------------------------------------------------

def _add_font_to_header(header_root, font_name: str, alloc: IDAllocator) -> int:
    """Register a new font in all language fontface groups. Returns font id."""
    fid = alloc.alloc_font()
    fontfaces = header_root.find(f".//{_tag('hh', 'fontfaces')}")
    if fontfaces is None:
        return 0  # Fallback to default

    for fontface in fontfaces.findall(_tag("hh", "fontface")):
        # Update fontCnt
        old_cnt = int(fontface.get("fontCnt", "0"))
        fontface.set("fontCnt", str(old_cnt + 1))

        font_el = etree.SubElement(fontface, _tag("hh", "font"))
        font_el.set("id", str(fid))
        font_el.set("face", font_name)
        font_el.set("type", "TTF")
        font_el.set("isEmbedded", "0")

        type_info = etree.SubElement(font_el, _tag("hh", "typeInfo"))
        type_info.set("familyType", "FCAT_GOTHIC")
        type_info.set("weight", "6")
        type_info.set("proportion", "4")
        type_info.set("contrast", "0")
        type_info.set("strokeVariation", "1")
        type_info.set("armStyle", "1")
        type_info.set("letterform", "1")
        type_info.set("midline", "1")
        type_info.set("xHeight", "1")

    # Update itemCnt
    fontfaces.set("itemCnt", str(len(fontfaces.findall(_tag("hh", "fontface")))))
    return fid


def _make_charpr(
    alloc: IDAllocator,
    font_id: int = 0,
    height: int = 1000,
    text_color: str = "#000000",
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    border_fill_ref: int = 2,
    latin_font_id: int | None = None,
) -> etree._Element:
    """Create a <hh:charPr> element."""
    cid = alloc.alloc_charpr()
    cp = etree.Element(_tag("hh", "charPr"))
    cp.set("id", str(cid))
    cp.set("height", str(height))
    cp.set("textColor", text_color)
    cp.set("shadeColor", "none")
    cp.set("useFontSpace", "0")
    cp.set("useKerning", "0")
    cp.set("symMark", "NONE")
    cp.set("borderFillIDRef", str(border_fill_ref))

    fid_str = str(font_id)
    # Use a separate font ID for the "latin" attribute when specified
    latin_fid_str = str(latin_font_id) if latin_font_id is not None else fid_str
    fontref = etree.SubElement(cp, _tag("hh", "fontRef"))
    for attr in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user"):
        fontref.set(attr, latin_fid_str if attr == "latin" else fid_str)

    for sub_tag, default_val in [
        ("ratio", "100"), ("spacing", "0"), ("relSz", "100"), ("offset", "0")
    ]:
        el = etree.SubElement(cp, _tag("hh", sub_tag))
        for attr in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user"):
            el.set(attr, default_val)

    if bold:
        etree.SubElement(cp, _tag("hh", "bold"))
    if italic:
        etree.SubElement(cp, _tag("hh", "italic"))

    ul = etree.SubElement(cp, _tag("hh", "underline"))
    if underline:
        ul.set("type", "BOTTOM")
        ul.set("shape", "SOLID")
        ul.set("color", text_color)
    else:
        ul.set("type", "NONE")
        ul.set("shape", "SOLID")
        ul.set("color", "#000000")

    so = etree.SubElement(cp, _tag("hh", "strikeout"))
    so.set("shape", "NONE")
    so.set("color", "#000000")

    ol = etree.SubElement(cp, _tag("hh", "outline"))
    ol.set("type", "NONE")

    sh = etree.SubElement(cp, _tag("hh", "shadow"))
    sh.set("type", "NONE")
    sh.set("color", "#C0C0C0")
    sh.set("offsetX", "10")
    sh.set("offsetY", "10")

    return cp


def _make_parapr(
    alloc: IDAllocator,
    align: str = "JUSTIFY",
    line_spacing: int = 160,
    indent: int = 0,
    margin_left: int = 0,
    margin_right: int = 0,
    margin_prev: int = 0,
    margin_next: int = 0,
    border_fill_ref: int = 2,
) -> etree._Element:
    """Create a <hh:paraPr> element."""
    pid = alloc.alloc_parapr()
    pp = etree.Element(_tag("hh", "paraPr"))
    pp.set("id", str(pid))
    pp.set("tabPrIDRef", "0")
    pp.set("condense", "0")
    pp.set("fontLineHeight", "0")
    pp.set("snapToGrid", "1")
    pp.set("suppressLineNumbers", "0")
    pp.set("checked", "0")
    pp.set("textDir", "LTR")

    al = etree.SubElement(pp, _tag("hh", "align"))
    al.set("horizontal", align)
    al.set("vertical", "BASELINE")

    hd = etree.SubElement(pp, _tag("hh", "heading"))
    hd.set("type", "NONE")
    hd.set("idRef", "0")
    hd.set("level", "0")

    bs = etree.SubElement(pp, _tag("hh", "breakSetting"))
    bs.set("breakLatinWord", "KEEP_WORD")
    bs.set("breakNonLatinWord", "BREAK_WORD")
    bs.set("widowOrphan", "0")
    bs.set("keepWithNext", "0")
    bs.set("keepLines", "0")
    bs.set("pageBreakBefore", "0")
    bs.set("lineWrap", "BREAK")

    asp = etree.SubElement(pp, _tag("hh", "autoSpacing"))
    asp.set("eAsianEng", "0")
    asp.set("eAsianNum", "0")

    # Use hp:switch pattern for margin/lineSpacing (matching base template)
    switch = etree.SubElement(pp, _tag("hp", "switch"))

    case_el = etree.SubElement(switch, _tag("hp", "case"))
    case_el.set(_tag("hp", "required-namespace"),
                "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar")
    _add_margin_linespacing(case_el, indent, margin_left, margin_right,
                            margin_prev, margin_next, line_spacing)

    default_el = etree.SubElement(switch, _tag("hp", "default"))
    _add_margin_linespacing(default_el, indent, margin_left, margin_right,
                            margin_prev, margin_next, line_spacing)

    bd = etree.SubElement(pp, _tag("hh", "border"))
    bd.set("borderFillIDRef", str(border_fill_ref))
    bd.set("offsetLeft", "0")
    bd.set("offsetRight", "0")
    bd.set("offsetTop", "0")
    bd.set("offsetBottom", "0")
    bd.set("connect", "0")
    bd.set("ignoreMargin", "0")

    return pp


def _add_margin_linespacing(parent, indent, left, right, prev, nxt, line_spacing):
    """Add <hh:margin> and <hh:lineSpacing> children."""
    margin = etree.SubElement(parent, _tag("hh", "margin"))

    # NOTE: OWPML spec uses "intent" (not "indent") as the XML element name
    for name, val in [
        ("intent", indent), ("left", left), ("right", right),
        ("prev", prev), ("next", nxt)
    ]:
        el = etree.SubElement(margin, _tag("hc", name))
        el.set("value", str(val))
        el.set("unit", "HWPUNIT")

    ls = etree.SubElement(parent, _tag("hh", "lineSpacing"))
    ls.set("type", "PERCENT")
    ls.set("value", str(line_spacing))
    ls.set("unit", "HWPUNIT")


def _make_borderfill(
    alloc: IDAllocator,
    border_type: str = "NONE",
    border_width: str = "0.1 mm",
    border_color: str = "#000000",
    face_color: str = "none",
) -> etree._Element:
    """Create a <hh:borderFill> element."""
    bid = alloc.alloc_borderfill()
    bf = etree.Element(_tag("hh", "borderFill"))
    bf.set("id", str(bid))
    bf.set("threeD", "0")
    bf.set("shadow", "0")
    bf.set("centerLine", "NONE")
    bf.set("breakCellSeparateLine", "0")

    slash = etree.SubElement(bf, _tag("hh", "slash"))
    slash.set("type", "NONE")
    slash.set("Crooked", "0")
    slash.set("isCounter", "0")

    backslash = etree.SubElement(bf, _tag("hh", "backSlash"))
    backslash.set("type", "NONE")
    backslash.set("Crooked", "0")
    backslash.set("isCounter", "0")

    for side in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
        b = etree.SubElement(bf, _tag("hh", side))
        b.set("type", border_type)
        b.set("width", border_width)
        b.set("color", border_color)

    diag = etree.SubElement(bf, _tag("hh", "diagonal"))
    diag.set("type", "SOLID")
    diag.set("width", "0.1 mm")
    diag.set("color", "#000000")

    if face_color != "none":
        fb = etree.SubElement(bf, _tag("hc", "fillBrush"))
        wb = etree.SubElement(fb, _tag("hc", "winBrush"))
        wb.set("faceColor", face_color)
        wb.set("hatchColor", "#999999")
        wb.set("alpha", "0")
    else:
        fb = etree.SubElement(bf, _tag("hc", "fillBrush"))
        wb = etree.SubElement(fb, _tag("hc", "winBrush"))
        wb.set("faceColor", "none")
        wb.set("hatchColor", "#999999")
        wb.set("alpha", "0")

    return bf


def _make_borderfill_custom_sides(
    alloc: IDAllocator,
    left_type="SOLID", left_width="0.12 mm",
    right_type="SOLID", right_width="0.12 mm",
    top_type="SOLID", top_width="0.12 mm",
    bottom_type="SOLID", bottom_width="0.12 mm",
    border_color="#000000",
    face_color="none",
) -> etree._Element:
    """Create a borderFill with individual side control."""
    bid = alloc.alloc_borderfill()
    bf = etree.Element(_tag("hh", "borderFill"))
    bf.set("id", str(bid))
    bf.set("threeD", "0")
    bf.set("shadow", "0")
    bf.set("centerLine", "NONE")
    bf.set("breakCellSeparateLine", "0")

    slash = etree.SubElement(bf, _tag("hh", "slash"))
    slash.set("type", "NONE")
    slash.set("Crooked", "0")
    slash.set("isCounter", "0")

    backslash = etree.SubElement(bf, _tag("hh", "backSlash"))
    backslash.set("type", "NONE")
    backslash.set("Crooked", "0")
    backslash.set("isCounter", "0")

    sides = [
        ("leftBorder", left_type, left_width),
        ("rightBorder", right_type, right_width),
        ("topBorder", top_type, top_width),
        ("bottomBorder", bottom_type, bottom_width),
    ]
    for side_name, side_type, side_width in sides:
        b = etree.SubElement(bf, _tag("hh", side_name))
        b.set("type", side_type)
        b.set("width", side_width)
        b.set("color", border_color)

    diag = etree.SubElement(bf, _tag("hh", "diagonal"))
    diag.set("type", "SOLID")
    diag.set("width", "0.1 mm")
    diag.set("color", "#000000")

    if face_color != "none":
        fb = etree.SubElement(bf, _tag("hc", "fillBrush"))
        wb = etree.SubElement(fb, _tag("hc", "winBrush"))
        wb.set("faceColor", face_color)
        wb.set("hatchColor", "#999999")
        wb.set("alpha", "0")
    else:
        fb = etree.SubElement(bf, _tag("hc", "fillBrush"))
        wb = etree.SubElement(fb, _tag("hc", "winBrush"))
        wb.set("faceColor", "none")
        wb.set("hatchColor", "#999999")
        wb.set("alpha", "0")

    return bf


def inject_styles_into_header(header_path: Path, doc_spec: dict, alloc: IDAllocator):
    """Parse header.xml, add new font/charPr/paraPr/borderFill, return style maps."""
    tree = etree.parse(str(header_path))
    root = tree.getroot()

    # Locate containers
    ref_list = root.find(_tag("hh", "refList"))
    borderfills = ref_list.find(_tag("hh", "borderFills"))
    charprops = ref_list.find(_tag("hh", "charProperties"))
    paraprops = ref_list.find(_tag("hh", "paraProperties"))

    # Track which font names are already registered
    existing_fonts = set()
    for fontface in root.findall(f".//{_tag('hh', 'fontface')}"):
        for font_el in fontface.findall(_tag("hh", "font")):
            existing_fonts.add(font_el.get("face", ""))

    # Maps: style_name -> { "charPrID": int, "paraPrID": int }
    style_map = {}
    # Font name -> font id
    font_id_map = {}

    # Register fonts first (including optional font_latin)
    styles_def = doc_spec.get("styles", {})
    for style_name, sdef in styles_def.items():
        for font_key in ("font", "font_latin"):
            fname = sdef.get(font_key, "")
            if fname and fname not in font_id_map:
                if fname in existing_fonts:
                    # Find existing id
                    for fontface in root.findall(f".//{_tag('hh', 'fontface')}"):
                        if fontface.get("lang") == "HANGUL":
                            for font_el in fontface.findall(_tag("hh", "font")):
                                if font_el.get("face") == fname:
                                    font_id_map[fname] = int(font_el.get("id", "0"))
                                    break
                    if fname not in font_id_map:
                        font_id_map[fname] = 0
                else:
                    fid = _add_font_to_header(root, fname, alloc)
                    font_id_map[fname] = fid
                    existing_fonts.add(fname)

    # Create charPr and paraPr for each style
    for style_name, sdef in styles_def.items():
        fname = sdef.get("font", "")
        fid = font_id_map.get(fname, 0)
        size_pt = sdef.get("size", 10)
        bold = sdef.get("bold", False)
        italic = sdef.get("italic", False)
        underline = sdef.get("underline", False)
        color = sdef.get("color", "#000000")
        align = sdef.get("align", "JUSTIFY")
        line_spacing = sdef.get("line_spacing", 160)
        indent = mm_to_hwpunit(sdef.get("indent", 0))
        margin_prev = mm_to_hwpunit(sdef.get("margin_top", 0))
        margin_next = mm_to_hwpunit(sdef.get("margin_bottom", 0))

        # Resolve optional separate Latin font ID
        latin_fname = sdef.get("font_latin", "")
        latin_fid = font_id_map.get(latin_fname) if latin_fname else None

        # Create charPr
        cp = _make_charpr(
            alloc, font_id=fid, height=pt_to_height(size_pt),
            text_color=color, bold=bold, italic=italic,
            underline=underline, latin_font_id=latin_fid,
        )
        charprops.append(cp)
        cpr_id = int(cp.get("id"))

        # Create paraPr
        pp = _make_parapr(
            alloc, align=align, line_spacing=line_spacing,
            indent=indent, margin_prev=margin_prev, margin_next=margin_next,
        )
        paraprops.append(pp)
        ppr_id = int(pp.get("id"))

        style_map[style_name] = {"charPrID": cpr_id, "paraPrID": ppr_id}

    # Update itemCnt attributes
    charprops.set("itemCnt", str(len(charprops.findall(_tag("hh", "charPr")))))
    paraprops.set("itemCnt", str(len(paraprops.findall(_tag("hh", "paraPr")))))
    borderfills.set("itemCnt", str(len(borderfills.findall(_tag("hh", "borderFill")))))

    # Build table borderFills if needed
    table_bf_map = {}
    for item in doc_spec.get("content", []):
        if item.get("type") == "table":
            tbl_style = item.get("style", {})
            table_bf_map[id(item)] = _create_table_borderfills(
                alloc, borderfills, tbl_style
            )

    # Update borderFills itemCnt again
    borderfills.set("itemCnt", str(len(borderfills.findall(_tag("hh", "borderFill")))))

    # Write modified header back
    tree.write(str(header_path), xml_declaration=True, encoding="UTF-8", pretty_print=True)

    return style_map, table_bf_map, font_id_map


def _create_table_borderfills(alloc: IDAllocator, borderfills_container, tbl_style: dict):
    """Create borderFills for table cells and return a map of bf ids."""
    border = tbl_style.get("border", "solid")
    header_bg = tbl_style.get("header_bg", "none")
    alt_row_bg = tbl_style.get("alt_row_bg", "none")

    # Determine border type
    if border == "solid":
        btype = "SOLID"
        bwidth = "0.12 mm"
    elif border == "thick":
        btype = "SOLID"
        bwidth = "0.4 mm"
    elif border == "double":
        btype = "DOUBLE"
        bwidth = "0.4 mm"
    elif border == "none":
        btype = "NONE"
        bwidth = "0.1 mm"
    else:
        btype = "SOLID"
        bwidth = "0.12 mm"

    # Table outer border fill (for the <hp:tbl> element itself)
    tbl_bf = _make_borderfill(alloc, border_type=btype, border_width=bwidth)
    borderfills_container.append(tbl_bf)
    tbl_bf_id = int(tbl_bf.get("id"))

    # Header cell fill
    if header_bg != "none":
        header_cell_bf = _make_borderfill(
            alloc, border_type=btype, border_width=bwidth, face_color=header_bg
        )
    else:
        header_cell_bf = _make_borderfill(alloc, border_type=btype, border_width=bwidth)
    borderfills_container.append(header_cell_bf)
    header_cell_bf_id = int(header_cell_bf.get("id"))

    # Normal cell fill (no background)
    normal_cell_bf = _make_borderfill(alloc, border_type=btype, border_width=bwidth)
    borderfills_container.append(normal_cell_bf)
    normal_cell_bf_id = int(normal_cell_bf.get("id"))

    # Alternating row fill
    if alt_row_bg != "none":
        alt_cell_bf = _make_borderfill(
            alloc, border_type=btype, border_width=bwidth, face_color=alt_row_bg
        )
        borderfills_container.append(alt_cell_bf)
        alt_cell_bf_id = int(alt_cell_bf.get("id"))
    else:
        alt_cell_bf_id = normal_cell_bf_id

    return {
        "tbl": tbl_bf_id,
        "header_cell": header_cell_bf_id,
        "normal_cell": normal_cell_bf_id,
        "alt_cell": alt_cell_bf_id,
    }


# ---------------------------------------------------------------------------
# Section0.xml construction
# ---------------------------------------------------------------------------

_PARA_ID_COUNTER = 1000000000


def _random_para_id() -> str:
    """Generate a sequential paragraph id for deterministic output."""
    global _PARA_ID_COUNTER
    pid = _PARA_ID_COUNTER
    _PARA_ID_COUNTER += 1
    return str(pid)


# ---------------------------------------------------------------------------
# Footnote / Endnote support
# ---------------------------------------------------------------------------

# Regex pattern to match [^N] (footnote) and [^^N] (endnote) markers in text.
# Group 1: optional '^' for endnote distinction, Group 2: note number digits.
_NOTE_MARKER_RE = re.compile(r'\[\^(\^?)(\d+)\]')

# Document-level note counters (reset per document build)
_FOOTNOTE_NUMBER_COUNTER = 0
_ENDNOTE_NUMBER_COUNTER = 0


def _reset_note_counters():
    """Reset the document-level footnote/endnote numbering counters.

    Must be called once at the beginning of each document build to ensure
    note numbers start from 1.
    """
    global _FOOTNOTE_NUMBER_COUNTER, _ENDNOTE_NUMBER_COUNTER
    _FOOTNOTE_NUMBER_COUNTER = 0
    _ENDNOTE_NUMBER_COUNTER = 0


def _next_footnote_number() -> int:
    """Allocate and return the next sequential footnote number (document-wide)."""
    global _FOOTNOTE_NUMBER_COUNTER
    _FOOTNOTE_NUMBER_COUNTER += 1
    return _FOOTNOTE_NUMBER_COUNTER


def _next_endnote_number() -> int:
    """Allocate and return the next sequential endnote number (document-wide)."""
    global _ENDNOTE_NUMBER_COUNTER
    _ENDNOTE_NUMBER_COUNTER += 1
    return _ENDNOTE_NUMBER_COUNTER


def _parse_note_markers(text: str):
    """Parse inline footnote/endnote markers from text.

    Markers use the format ``[^N]`` for footnotes and ``[^^N]`` for endnotes,
    where *N* is a reference key (digits).

    Args:
        text: The paragraph text potentially containing note markers.

    Returns:
        A list of tuples.  Each tuple is either:
        - ``(plain_text, None)`` for a plain-text segment, or
        - ``("", ("footnote", key_str))`` / ``("", ("endnote", key_str))``
          for a note marker position.

    Example::

        >>> _parse_note_markers("Hello[^1] world[^^2].")
        [("Hello", None), ("", ("footnote", "1")), (" world", None),
         ("", ("endnote", "2")), (".", None)]
    """
    segments = []
    last_end = 0
    for m in _NOTE_MARKER_RE.finditer(text):
        # Text before this marker
        before = text[last_end:m.start()]
        if before:
            segments.append((before, None))
        # Determine note type
        is_endnote = m.group(1) == "^"
        key = m.group(2)
        note_type = "endnote" if is_endnote else "footnote"
        segments.append(("", (note_type, key)))
        last_end = m.end()

    # Remaining text after last marker
    remaining = text[last_end:]
    if remaining:
        segments.append((remaining, None))

    return segments


def _build_note_element(note_type: str, note_number: int, content_text: str,
                        charpr_id: int) -> etree._Element:
    """Build an ``<hp:ctrl>`` wrapper containing ``<hp:footNote>`` or ``<hp:endNote>``.

    The note element is wrapped in ``<hp:ctrl>`` as required by the OWPML
    standard.  Inside the note paragraph an ``<hp:autoNum>`` control is
    emitted before the text so that the Hangul renderer can display the
    automatic note number marker.

    Args:
        note_type: Either ``"footnote"`` or ``"endnote"``.
        note_number: The sequential note number (document-wide).
        content_text: The text content of the note.
        charpr_id: The charPr ID reference for the note content run.

    Returns:
        An lxml ``<hp:ctrl>`` Element containing the footnote or endnote,
        ready to be appended inside an ``<hp:run>``.
    """
    # --- outer wrapper: <hp:ctrl> ---
    ctrl_el = etree.Element(_tag("hp", "ctrl"))

    tag_name = "footNote" if note_type == "footnote" else "endNote"
    note_el = etree.SubElement(ctrl_el, _tag("hp", tag_name))
    note_el.set("number", str(note_number))
    # suffixChar="41" is ASCII code for ')' -- required by Hangul renderer
    note_el.set("suffixChar", "41")
    note_el.set("instId", _random_para_id())

    # --- subList with full attribute set ---
    sublist = etree.SubElement(note_el, _tag("hp", "subList"))
    sublist.set("id", "")
    sublist.set("textDirection", "HORIZONTAL")
    sublist.set("lineWrap", "BREAK")
    sublist.set("vertAlign", "TOP")
    sublist.set("linkListIDRef", "0")
    sublist.set("linkListNextIDRef", "0")
    sublist.set("textWidth", "0")
    sublist.set("textHeight", "0")
    sublist.set("hasTextRef", "0")
    sublist.set("hasNumRef", "0")

    # --- note paragraph ---
    # Use style 15 (footnote) or 16 (endnote), paraPrIDRef 10, charPrIDRef 3
    # as defined in the base template header.xml
    note_style_id = "15" if note_type == "footnote" else "16"
    note_p = etree.SubElement(sublist, _tag("hp", "p"))
    note_p.set("id", "0")
    note_p.set("paraPrIDRef", "10")
    note_p.set("styleIDRef", note_style_id)
    note_p.set("pageBreak", "0")
    note_p.set("columnBreak", "0")
    note_p.set("merged", "0")

    note_run = etree.SubElement(note_p, _tag("hp", "run"))
    note_run.set("charPrIDRef", "3")

    # --- autoNum control (required for Hangul to render the note number) ---
    auto_ctrl = etree.SubElement(note_run, _tag("hp", "ctrl"))
    auto_num = etree.SubElement(auto_ctrl, _tag("hp", "autoNum"))
    auto_num.set("num", str(note_number))
    auto_num.set("numType", "FOOTNOTE" if note_type == "footnote" else "ENDNOTE")
    auto_num_fmt = etree.SubElement(auto_num, _tag("hp", "autoNumFormat"))
    auto_num_fmt.set("type", "DIGIT")
    auto_num_fmt.set("userChar", "")
    auto_num_fmt.set("prefixChar", "")
    auto_num_fmt.set("suffixChar", ")")
    auto_num_fmt.set("supscript", "0")

    # --- note text (with leading space as per Hangul convention) ---
    note_t = etree.SubElement(note_run, _tag("hp", "t"))
    note_t.text = " " + content_text

    # --- linesegarray for the note paragraph ---
    lsa = etree.SubElement(note_p, _tag("hp", "linesegarray"))
    ls = etree.SubElement(lsa, _tag("hp", "lineseg"))
    ls.set("textpos", "0")
    ls.set("vertpos", "0")
    ls.set("vertsize", "900")
    ls.set("textheight", "900")
    ls.set("baseline", "765")
    ls.set("spacing", "272")
    ls.set("horzpos", "0")
    ls.set("horzsize", "42520")
    ls.set("flags", "393216")

    return ctrl_el


def _build_paragraph_with_notes(
    text: str,
    charpr_id: int,
    parapr_id: int,
    footnotes: dict | None,
    endnotes: dict | None,
    style_id: int = 0,
) -> etree._Element:
    """Build an ``<hp:p>`` element with inline footnote/endnote elements.

    This function splits the paragraph text at ``[^N]`` / ``[^^N]`` markers.
    Text segments and note controls are merged into as few ``<hp:run>``
    elements as possible, matching the structure produced by the Hangul
    application.  In Hangul's XML output, a single ``<hp:run>`` can contain
    ``<hp:t>`` text followed by ``<hp:ctrl>`` (with the note), followed by
    more ``<hp:t>`` text.

    Args:
        text: Paragraph text containing inline note markers.
        charpr_id: Character property ID for the paragraph runs.
        parapr_id: Paragraph property ID.
        footnotes: Dict mapping key strings to footnote content text.
                   E.g. ``{"1": "First footnote", "2": "Second footnote"}``.
        endnotes: Dict mapping key strings to endnote content text.
                  E.g. ``{"1": "First endnote"}``.
        style_id: Style ID reference for the paragraph.

    Returns:
        An lxml ``<hp:p>`` Element with embedded note elements.
    """
    if footnotes is None:
        footnotes = {}
    if endnotes is None:
        endnotes = {}

    p = etree.Element(_tag("hp", "p"))
    p.set("id", _random_para_id())
    p.set("paraPrIDRef", str(parapr_id))
    p.set("styleIDRef", str(style_id))
    p.set("pageBreak", "0")
    p.set("columnBreak", "0")
    p.set("merged", "0")

    segments = _parse_note_markers(text)

    # Build a single run that interleaves <hp:t> and <hp:ctrl> children,
    # matching the native Hangul structure.
    run = etree.SubElement(p, _tag("hp", "run"))
    run.set("charPrIDRef", str(charpr_id))

    for seg_text, note_info in segments:
        if note_info is None:
            # Plain text segment -- emit <hp:t>
            t = etree.SubElement(run, _tag("hp", "t"))
            t.text = seg_text if seg_text else ""
        else:
            # Note marker -- emit <hp:ctrl> containing the note
            note_type, key = note_info
            if note_type == "footnote":
                content = footnotes.get(key, "")
                note_number = _next_footnote_number()
            else:
                content = endnotes.get(key, "")
                note_number = _next_endnote_number()

            ctrl_el = _build_note_element(note_type, note_number, content,
                                          charpr_id)
            run.append(ctrl_el)

    return p


def _text_has_note_markers(text: str) -> bool:
    """Check whether text contains any ``[^N]`` or ``[^^N]`` note markers.

    Args:
        text: The text to check.

    Returns:
        True if at least one note marker is found.
    """
    return bool(_NOTE_MARKER_RE.search(text))


def _build_paragraph(text: str, charpr_id: int, parapr_id: int, style_id: int = 0) -> etree._Element:
    """Build a <hp:p> element with a single text run."""
    p = etree.Element(_tag("hp", "p"))
    p.set("id", _random_para_id())
    p.set("paraPrIDRef", str(parapr_id))
    p.set("styleIDRef", str(style_id))
    p.set("pageBreak", "0")
    p.set("columnBreak", "0")
    p.set("merged", "0")

    run = etree.SubElement(p, _tag("hp", "run"))
    run.set("charPrIDRef", str(charpr_id))

    t = etree.SubElement(run, _tag("hp", "t"))
    t.text = text

    return p


def _build_table_paragraph(
    table_spec: dict,
    tbl_bf_ids: dict,
    alloc: IDAllocator,
    header_root,
    font_id_map: dict,
    style_map: dict,
    body_width: int,
    col_widths_pct: list[float] | None = None,
) -> etree._Element:
    """Build a <hp:p> containing a <hp:tbl>."""
    rows_count = table_spec.get("rows", 2)
    cols_count = table_spec.get("cols", 2)
    headers = table_spec.get("headers", [])
    data = table_spec.get("data", [])
    tbl_style = table_spec.get("style", {})
    merge_specs = table_spec.get("merges", [])

    # Determine table char style for header/body
    header_bold = tbl_style.get("header_bold", True)
    header_font_color = tbl_style.get("header_font_color", "#000000")
    body_font_color = tbl_style.get("body_font_color", "#000000")
    cell_align = tbl_style.get("cell_align", "CENTER")

    # Get or create charPr for header cells
    charprops = header_root.find(f".//{_tag('hh', 'charProperties')}")
    header_cp = _make_charpr(
        alloc, font_id=0, height=1000, text_color=header_font_color,
        bold=header_bold,
    )
    charprops.append(header_cp)
    header_charpr_id = int(header_cp.get("id"))

    # charPr for body cells
    body_cp = _make_charpr(
        alloc, font_id=0, height=1000, text_color=body_font_color,
    )
    charprops.append(body_cp)
    body_charpr_id = int(body_cp.get("id"))

    # paraPr for table cells (centered)
    paraprops = header_root.find(f".//{_tag('hh', 'paraProperties')}")
    cell_pp = _make_parapr(alloc, align=cell_align, line_spacing=160)
    paraprops.append(cell_pp)
    cell_parapr_id = int(cell_pp.get("id"))

    # Update counts
    charprops.set("itemCnt", str(len(charprops.findall(_tag("hh", "charPr")))))
    paraprops.set("itemCnt", str(len(paraprops.findall(_tag("hh", "paraPr")))))

    # Calculate dimensions using the actual page body width
    table_width = body_width

    # Calculate per-column widths
    if col_widths_pct and len(col_widths_pct) == cols_count:
        # Normalize percentages so they sum to 100
        pct_sum = sum(col_widths_pct)
        if pct_sum != 100 and pct_sum > 0:
            col_widths_pct = [p * 100.0 / pct_sum for p in col_widths_pct]
        col_widths_list = [int(round(table_width * p / 100.0)) for p in col_widths_pct]
        # Adjust rounding remainder onto the last column
        remainder = table_width - sum(col_widths_list)
        col_widths_list[-1] += remainder
    else:
        col_width = table_width // cols_count
        col_widths_list = [col_width] * cols_count
        # Adjust remainder onto the last column
        col_widths_list[-1] += table_width - col_width * cols_count

    # Build merge map: (row, col) -> (rowSpan, colSpan) and covered cells
    merge_map = {}     # (r, c) -> (rowSpan, colSpan)
    covered = set()    # set of (r, c) that are covered by merges

    for ms in merge_specs:
        r = ms.get("row", 0)
        c = ms.get("col", 0)
        rs = ms.get("rowSpan", 1)
        cs = ms.get("colSpan", 1)
        merge_map[(r, c)] = (rs, cs)
        for ri in range(r, r + rs):
            for ci in range(c, c + cs):
                if (ri, ci) != (r, c):
                    covered.add((ri, ci))

    # Build all rows data
    all_rows_data = []
    if headers:
        all_rows_data.append(headers)
    for row_data in data:
        all_rows_data.append(row_data)

    # Pad rows
    total_rows = max(rows_count, len(all_rows_data))
    while len(all_rows_data) < total_rows:
        all_rows_data.append([""] * cols_count)

    # Calculate per-row heights based on max line count in each row
    base_row_height = 1200  # Default height for a single-line row
    row_heights = []
    for ri in range(total_rows):
        max_lines = 1
        if ri < len(all_rows_data):
            for ci in range(cols_count):
                if ci < len(all_rows_data[ri]):
                    cell_val = str(all_rows_data[ri][ci])
                    line_count = len(cell_val.split("\n")) if cell_val else 1
                    if line_count > max_lines:
                        max_lines = line_count
        row_heights.append(base_row_height * max_lines)

    # Build the paragraph containing the table
    p = etree.Element(_tag("hp", "p"))
    p.set("id", _random_para_id())
    p.set("paraPrIDRef", "0")
    p.set("styleIDRef", "0")
    p.set("pageBreak", "0")
    p.set("columnBreak", "0")
    p.set("merged", "0")

    run = etree.SubElement(p, _tag("hp", "run"))
    run.set("charPrIDRef", "0")

    # Total table height (sum of all row heights)
    total_height = sum(row_heights)

    tbl = etree.SubElement(run, _tag("hp", "tbl"))
    tbl.set("id", _random_para_id())
    tbl.set("rowCnt", str(total_rows))
    tbl.set("colCnt", str(cols_count))
    tbl.set("cellSpacing", "0")
    tbl.set("borderFillIDRef", str(tbl_bf_ids["tbl"]))
    tbl.set("repeatHeader", "1" if headers else "0")
    tbl.set("noAdjust", "0")
    tbl.set("pageBreak", "CELL")

    sz = etree.SubElement(tbl, _tag("hp", "sz"))
    sz.set("width", str(table_width))
    sz.set("height", str(total_height))

    pos = etree.SubElement(tbl, _tag("hp", "pos"))
    pos.set("treatAsChar", "1")
    pos.set("affectLSpacing", "0")
    pos.set("flowWithText", "1")
    pos.set("allowOverlap", "0")
    pos.set("holdAnchorAndSO", "0")
    pos.set("rgroupOverride", "0")
    pos.set("allowInHeader", "0")
    pos.set("allowInFooter", "0")
    pos.set("vertRelTo", "PARA")
    pos.set("horzRelTo", "PARA")
    pos.set("vertAlign", "TOP")
    pos.set("horzAlign", "LEFT")
    pos.set("vertOffset", "0")
    pos.set("horzOffset", "0")

    outsidemargin = etree.SubElement(tbl, _tag("hp", "outMargin"))
    outsidemargin.set("left", "0")
    outsidemargin.set("right", "0")
    outsidemargin.set("top", "0")
    outsidemargin.set("bottom", "0")

    # Build rows
    for ri in range(total_rows):
        tr = etree.SubElement(tbl, _tag("hp", "tr"))

        is_header_row = (headers and ri == 0)

        for ci in range(cols_count):
            if (ri, ci) in covered:
                continue

            row_span, col_span = merge_map.get((ri, ci), (1, 1))

            # Determine borderFill for this cell
            if is_header_row:
                cell_bf_id = tbl_bf_ids["header_cell"]
            elif ri % 2 == 0:
                cell_bf_id = tbl_bf_ids["alt_cell"]
            else:
                cell_bf_id = tbl_bf_ids["normal_cell"]

            tc = etree.SubElement(tr, _tag("hp", "tc"))
            tc.set("header", "1" if is_header_row else "0")
            tc.set("borderFillIDRef", str(cell_bf_id))

            cell_addr = etree.SubElement(tc, _tag("hp", "cellAddr"))
            cell_addr.set("colAddr", str(ci))
            cell_addr.set("rowAddr", str(ri))

            cell_span = etree.SubElement(tc, _tag("hp", "cellSpan"))
            cell_span.set("colSpan", str(col_span))
            cell_span.set("rowSpan", str(row_span))

            cell_sz = etree.SubElement(tc, _tag("hp", "cellSz"))
            spanned_width = sum(col_widths_list[ci:ci + col_span])
            cell_sz.set("width", str(spanned_width))
            cell_sz.set("height", str(sum(row_heights[ri:ri + row_span])))

            cell_margin = etree.SubElement(tc, _tag("hp", "cellMargin"))
            cell_margin.set("left", "510")
            cell_margin.set("right", "510")
            cell_margin.set("top", "142")
            cell_margin.set("bottom", "142")

            # Cell content
            cell_text = ""
            if ri < len(all_rows_data) and ci < len(all_rows_data[ri]):
                cell_text = str(all_rows_data[ri][ci])

            charpr = header_charpr_id if is_header_row else body_charpr_id

            sublist = etree.SubElement(tc, _tag("hp", "subList"))
            sublist.set("id", _random_para_id())
            sublist.set("textDirection", "HORIZONTAL")
            sublist.set("vertAlign", "CENTER")

            # Split cell text by newlines to support multiline cells
            lines = cell_text.split("\n") if cell_text else [""]

            for line in lines:
                cell_p = etree.SubElement(sublist, _tag("hp", "p"))
                cell_p.set("id", _random_para_id())
                cell_p.set("paraPrIDRef", str(cell_parapr_id))
                cell_p.set("styleIDRef", "0")
                cell_p.set("pageBreak", "0")
                cell_p.set("columnBreak", "0")
                cell_p.set("merged", "0")

                cell_run = etree.SubElement(cell_p, _tag("hp", "run"))
                cell_run.set("charPrIDRef", str(charpr))

                cell_t = etree.SubElement(cell_run, _tag("hp", "t"))
                cell_t.text = line

    # Add an empty run after the table (HWPX convention)
    run2 = etree.SubElement(p, _tag("hp", "run"))
    run2.set("charPrIDRef", "0")
    t2 = etree.SubElement(run2, _tag("hp", "t"))

    return p


def build_section_xml(
    doc_spec: dict,
    style_map: dict,
    table_bf_map: dict,
    alloc: IDAllocator,
    header_root,
    font_id_map: dict,
) -> etree._Element:
    """Build the complete section0.xml element tree."""
    sec = etree.Element(_tag("hs", "sec"), nsmap=NSMAP_SECTION)

    # First paragraph with secPr (section properties)
    page_cfg = doc_spec.get("page", {})
    paper = page_cfg.get("paper", "A4")
    orientation = page_cfg.get("orientation", "portrait")
    margins = page_cfg.get("margins", {})

    if paper not in PAPER_SIZES:
        print(
            f"WARNING: Unknown paper size '{paper}'. Falling back to A4.",
            file=sys.stderr,
        )
    paper_w, paper_h = PAPER_SIZES.get(paper, PAPER_SIZES["A4"])
    if orientation == "landscape":
        paper_w, paper_h = paper_h, paper_w
        landscape_val = "NARROWLY"
    else:
        landscape_val = "WIDELY"

    m_top = mm_to_hwpunit(margins.get("top", 20))
    m_bottom = mm_to_hwpunit(margins.get("bottom", 20))
    m_left = mm_to_hwpunit(margins.get("left", 30))
    m_right = mm_to_hwpunit(margins.get("right", 30))
    m_header = mm_to_hwpunit(margins.get("header", 15))
    m_footer = mm_to_hwpunit(margins.get("footer", 15))

    # Calculate body width for table sizing
    body_width = paper_w - m_left - m_right

    # --- secPr paragraph ---
    sec_p = etree.SubElement(sec, _tag("hp", "p"))
    sec_p.set("id", _random_para_id())
    sec_p.set("paraPrIDRef", "0")
    sec_p.set("styleIDRef", "0")
    sec_p.set("pageBreak", "0")
    sec_p.set("columnBreak", "0")
    sec_p.set("merged", "0")

    sec_run = etree.SubElement(sec_p, _tag("hp", "run"))
    sec_run.set("charPrIDRef", "0")

    secpr = etree.SubElement(sec_run, _tag("hp", "secPr"))
    secpr.set("id", "")
    secpr.set("textDirection", "HORIZONTAL")
    secpr.set("spaceColumns", "1134")
    secpr.set("tabStop", "8000")
    secpr.set("tabStopVal", "4000")
    secpr.set("tabStopUnit", "HWPUNIT")
    secpr.set("outlineShapeIDRef", "1")
    secpr.set("memoShapeIDRef", "0")
    secpr.set("textVerticalWidthHead", "0")
    secpr.set("masterPageCnt", "0")

    grid = etree.SubElement(secpr, _tag("hp", "grid"))
    grid.set("lineGrid", "0")
    grid.set("charGrid", "0")
    grid.set("wonggojiFormat", "0")

    startnum = etree.SubElement(secpr, _tag("hp", "startNum"))
    startnum.set("pageStartsOn", "BOTH")
    startnum.set("page", "0")
    startnum.set("pic", "0")
    startnum.set("tbl", "0")
    startnum.set("equation", "0")

    vis = etree.SubElement(secpr, _tag("hp", "visibility"))
    vis.set("hideFirstHeader", "0")
    vis.set("hideFirstFooter", "0")
    vis.set("hideFirstMasterPage", "0")
    vis.set("border", "SHOW_ALL")
    vis.set("fill", "SHOW_ALL")
    vis.set("hideFirstPageNum", "0")
    vis.set("hideFirstEmptyLine", "0")
    vis.set("showLineNumber", "0")

    linenum = etree.SubElement(secpr, _tag("hp", "lineNumberShape"))
    linenum.set("restartType", "0")
    linenum.set("countBy", "0")
    linenum.set("distance", "0")
    linenum.set("startNumber", "0")

    pagepr = etree.SubElement(secpr, _tag("hp", "pagePr"))
    pagepr.set("landscape", landscape_val)
    pagepr.set("width", str(paper_w))
    pagepr.set("height", str(paper_h))
    pagepr.set("gutterType", "LEFT_ONLY")

    margin_el = etree.SubElement(pagepr, _tag("hp", "margin"))
    margin_el.set("header", str(m_header))
    margin_el.set("footer", str(m_footer))
    margin_el.set("gutter", "0")
    margin_el.set("left", str(m_left))
    margin_el.set("right", str(m_right))
    margin_el.set("top", str(m_top))
    margin_el.set("bottom", str(m_bottom))

    # Footnote/endnote properties -- values must match the base template
    # (derived from a known-good Hangul-generated document).
    for note_tag in ("footNotePr", "endNotePr"):
        is_fn = note_tag == "footNotePr"
        note = etree.SubElement(secpr, _tag("hp", note_tag))

        anf = etree.SubElement(note, _tag("hp", "autoNumFormat"))
        anf.set("type", "DIGIT")
        anf.set("userChar", "")
        anf.set("prefixChar", "")
        anf.set("suffixChar", ")")
        anf.set("supscript", "0")

        nl = etree.SubElement(note, _tag("hp", "noteLine"))
        nl.set("length", "-1" if is_fn else "14692344")
        nl.set("type", "SOLID")
        nl.set("width", "0.12 mm")
        nl.set("color", "#000000")

        ns_el = etree.SubElement(note, _tag("hp", "noteSpacing"))
        ns_el.set("betweenNotes", "283" if is_fn else "0")
        ns_el.set("belowLine", "567")
        ns_el.set("aboveLine", "850")

        nb = etree.SubElement(note, _tag("hp", "numbering"))
        nb.set("type", "CONTINUOUS")
        nb.set("newNum", "1")

        pl = etree.SubElement(note, _tag("hp", "placement"))
        pl.set("place", "EACH_COLUMN" if is_fn else "END_OF_DOCUMENT")
        pl.set("beneathText", "0")

    # Page border fills
    for pbf_type in ("BOTH", "EVEN", "ODD"):
        pbf = etree.SubElement(secpr, _tag("hp", "pageBorderFill"))
        pbf.set("type", pbf_type)
        pbf.set("borderFillIDRef", "1")
        pbf.set("textBorder", "PAPER")
        pbf.set("headerInside", "0")
        pbf.set("footerInside", "0")
        pbf.set("fillArea", "PAPER")
        off = etree.SubElement(pbf, _tag("hp", "offset"))
        off.set("left", "1417")
        off.set("right", "1417")
        off.set("top", "1417")
        off.set("bottom", "1417")

    # Column control
    ctrl_run = etree.SubElement(sec_p, _tag("hp", "run"))
    ctrl_run.set("charPrIDRef", "0")
    ctrl = etree.SubElement(ctrl_run, _tag("hp", "ctrl"))
    colpr = etree.SubElement(ctrl, _tag("hp", "colPr"))
    colpr.set("id", "")
    colpr.set("type", "NEWSPAPER")
    colpr.set("layout", "LEFT")
    colpr.set("colCount", "1")
    colpr.set("sameSz", "1")
    colpr.set("sameGap", "0")

    # Header/Footer -- OWPML standard places these as <hp:ctrl> children
    # inside separate <hp:run> elements in the secPr paragraph, NOT inside secPr itself.
    header_text = doc_spec.get("header_text", "")
    footer_text = doc_spec.get("footer_text", "")

    if header_text:
        hdr_run = _build_header_footer_ctrl_run("header", header_text, alloc)
        sec_p.append(hdr_run)
    if footer_text:
        ftr_run = _build_header_footer_ctrl_run("footer", footer_text, alloc)
        sec_p.append(ftr_run)

    # Empty run at end of secPr paragraph
    empty_run = etree.SubElement(sec_p, _tag("hp", "run"))
    empty_run.set("charPrIDRef", "0")
    etree.SubElement(empty_run, _tag("hp", "t"))

    # Lineseg for secPr paragraph
    lsa = etree.SubElement(sec_p, _tag("hp", "linesegarray"))
    ls = etree.SubElement(lsa, _tag("hp", "lineseg"))
    ls.set("textpos", "0")
    ls.set("vertpos", "0")
    ls.set("vertsize", "1000")
    ls.set("textheight", "1000")
    ls.set("baseline", "850")
    ls.set("spacing", "600")
    ls.set("horzpos", "0")
    ls.set("horzsize", str(body_width))
    ls.set("flags", "393216")

    # --- Content paragraphs ---
    content_items = doc_spec.get("content", [])
    for item in content_items:
        item_type = item.get("type", "paragraph")

        if item_type == "paragraph":
            style_name = item.get("style", "body")
            text = item.get("text", "")

            if style_name not in style_map:
                print(
                    f"WARNING: Style '{style_name}' not defined in 'styles'. "
                    f"Using default.",
                    file=sys.stderr,
                )
            sinfo = style_map.get(style_name, {"charPrID": 0, "paraPrID": 0})

            if _text_has_note_markers(text):
                # Build paragraph with inline footnotes/endnotes
                fn_dict = item.get("footnotes") or {}
                en_dict = item.get("endnotes") or {}
                para = _build_paragraph_with_notes(
                    text,
                    sinfo["charPrID"],
                    sinfo["paraPrID"],
                    footnotes=fn_dict,
                    endnotes=en_dict,
                )
            else:
                para = _build_paragraph(text, sinfo["charPrID"], sinfo["paraPrID"])
            sec.append(para)

        elif item_type == "table":
            bf_ids = table_bf_map.get(id(item), {
                "tbl": 1, "header_cell": 1, "normal_cell": 1, "alt_cell": 1
            })
            tbl_para = _build_table_paragraph(
                item, bf_ids, alloc, header_root, font_id_map, style_map,
                body_width=body_width,
                col_widths_pct=item.get("col_widths"),
            )
            sec.append(tbl_para)

        elif item_type == "page_break":
            # Insert a paragraph with pageBreak="1"
            pb_p = etree.SubElement(sec, _tag("hp", "p"))
            pb_p.set("id", _random_para_id())
            pb_p.set("paraPrIDRef", "0")
            pb_p.set("styleIDRef", "0")
            pb_p.set("pageBreak", "1")
            pb_p.set("columnBreak", "0")
            pb_p.set("merged", "0")
            pb_run = etree.SubElement(pb_p, _tag("hp", "run"))
            pb_run.set("charPrIDRef", "0")
            etree.SubElement(pb_run, _tag("hp", "t"))

    # If no content, add an empty paragraph
    if not content_items:
        empty_p = _build_paragraph("", 0, 0)
        sec.append(empty_p)

    return sec


def _build_header_footer_ctrl_run(hf_type, text, alloc):
    """Build a <hp:run> containing <hp:ctrl> with <hp:header> or <hp:footer>.

    In OWPML standard (KS X 6101), headers/footers are child elements of
    <hp:ctrl>, NOT of <hp:secPr>.  The correct structure is:
        <hp:run charPrIDRef="0">
          <hp:ctrl>
            <hp:header applyPageType="BOTH">
              <hp:subList ...>
                <hp:p ...><hp:run ...><hp:t>text</hp:t></hp:run></hp:p>
              </hp:subList>
            </hp:header>
          </hp:ctrl>
        </hp:run>

    hf_type: "header" or "footer"
    Returns: an lxml <hp:run> Element ready to be appended to the secPr paragraph.
    """
    run_el = etree.Element(_tag("hp", "run"))
    run_el.set("charPrIDRef", "0")

    ctrl = etree.SubElement(run_el, _tag("hp", "ctrl"))

    # Tag name is "header" or "footer" (NOT "headerPr"/"footerPr")
    hf = etree.SubElement(ctrl, _tag("hp", hf_type))
    hf.set("id", "0")
    hf.set("applyPageType", "BOTH")

    # The header/footer contains a subList with paragraphs
    sublist = etree.SubElement(hf, _tag("hp", "subList"))
    sublist.set("id", _random_para_id())
    sublist.set("textDirection", "HORIZONTAL")
    sublist.set("vertAlign", "CENTER")

    hf_p = etree.SubElement(sublist, _tag("hp", "p"))
    hf_p.set("id", _random_para_id())
    hf_p.set("paraPrIDRef", "0")
    hf_p.set("styleIDRef", "0")
    hf_p.set("pageBreak", "0")
    hf_p.set("columnBreak", "0")
    hf_p.set("merged", "0")

    # Emit every literal fragment and every page field, without truncation.
    for index, fragment in enumerate(text.split("{page}")):
        if index:
            page_run = etree.SubElement(hf_p, _tag("hp", "run"), charPrIDRef="0")
            page_ctrl = etree.SubElement(page_run, _tag("hp", "ctrl"))
            etree.SubElement(page_ctrl, _tag("hp", "autoNum"), numType="PAGE", suffixChar="NONE")
        if fragment:
            text_run = etree.SubElement(hf_p, _tag("hp", "run"), charPrIDRef="0")
            etree.SubElement(text_run, _tag("hp", "t")).text = fragment

    return run_el


# ---------------------------------------------------------------------------
# Main build flow
# ---------------------------------------------------------------------------

def _validate_spec(spec):
    """Reject unsupported or lossy input before touching the destination."""
    import math
    def obj(value, allowed, label):
        if not isinstance(value, dict) or set(value) - set(allowed.split()):
            raise ValueError(label + ': expected object with supported keys')
    def number(value, label, low=0, high=100000, integer=False):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high or (integer and type(value) is not int):
            raise ValueError(label + ': invalid number')
    def string(value, label):
        if not isinstance(value, str):
            raise ValueError(label + ': expected string')
        if any(ord(c) < 32 and c not in '\t\n\r' for c in value):
            raise ValueError(label + ': XML control character')
    def style(value, table=False):
        allowed = 'header_bg header_font_color header_bold body_font_color alt_row_bg border cell_align' if table else 'font font_latin size bold italic underline color align line_spacing indent margin_top margin_bottom'
        obj(value, allowed, 'style')
        for key, val in value.items():
            if key in ('bold','italic','underline','header_bold'):
                if type(val) is not bool:
                    raise ValueError(key + ': expected boolean')
            elif key in ('size','line_spacing','indent','margin_top','margin_bottom'):
                number(val, key, -1000 if key=='indent' else (0.01 if key in ('size','line_spacing') else 0), 1000)
            else:
                string(val, key)
                if key in ('align','cell_align') and val not in ('LEFT','CENTER','RIGHT','JUSTIFY'):
                    raise ValueError('Unsupported alignment')
                if key == 'border' and val not in ('solid','thick','double','none'):
                    raise ValueError('Unsupported border')
                if key in ('color','header_bg','header_font_color','body_font_color','alt_row_bg') and not (re.fullmatch(r'#[0-9a-fA-F]{6}', val) or (key in ('header_bg','alt_row_bg') and val=='none')):
                    raise ValueError('Invalid color')
    obj(spec, 'page header_text footer_text styles content', 'document')
    for key in ('header_text','footer_text'):
        string(spec.get(key,''), key)
    page = spec.get('page', {})
    obj(page, 'paper orientation margins', 'page')
    paper = page.get('paper','A4')
    orientation = page.get('orientation','portrait')
    string(paper, 'paper')
    string(orientation, 'orientation')
    if paper not in PAPER_SIZES or orientation not in ('portrait','landscape'):
        raise ValueError('Unsupported paper/orientation')
    margins = page.get('margins',{})
    obj(margins, 'top bottom left right header footer', 'margins')
    for key, value in margins.items():
        number(value, 'margin '+key, 0, 1000)
    width, height = PAPER_SIZES[paper]
    if orientation == 'landscape':
        width, height = height, width
    if mm_to_hwpunit(margins.get('left',30)+margins.get('right',30)) >= width or mm_to_hwpunit(margins.get('top',20)+margins.get('bottom',20)) >= height:
        raise ValueError('Margins leave no page body')
    styles = spec.get('styles',{})
    if not isinstance(styles, dict):
        raise ValueError('styles must be an object')
    for name, value in styles.items():
        string(name, 'style name')
        style(value)
    content = spec.get('content',[])
    if not isinstance(content, list) or len(content) > 10000:
        raise ValueError('content must be a bounded array')
    slots = 0
    for item in content:
        if not isinstance(item, dict):
            raise ValueError('content item must be an object')
        kind = item.get('type','paragraph')
        if kind == 'page_break':
            obj(item, 'type', kind)
        elif kind == 'paragraph':
            obj(item, 'type text style footnotes endnotes', kind)
            string(item.get('text',''), 'text')
            string(item.get('style','body'), 'style reference')
            for key in ('footnotes','endnotes'):
                notes = item.get(key,{})
                if not isinstance(notes,dict):
                    raise ValueError(key + ': expected object')
                for k,v in notes.items():
                    string(k,'note key')
                    string(v,'note text')
            for marker in re.finditer(r'\[(\^\^?)([0-9]+)\]',item.get('text','')):
                if marker[2] not in item.get('endnotes' if marker[1]=='^^' else 'footnotes',{}):
                    raise ValueError('Missing note definition: ' + marker[0])
        elif kind == 'table':
            obj(item, 'type rows cols headers data merges col_widths style', kind)
            rows, cols = item.get('rows',2), item.get('cols',2)
            number(rows, 'rows', 1, 10000, True)
            number(cols, 'cols', 1, 1000, True)
            headers, data = item.get('headers',[]), item.get('data',[])
            if not isinstance(headers,list) or not isinstance(data,list):
                raise ValueError('headers/data must be arrays')
            total = max(rows, len(data) + bool(headers))
            slots += total * cols
            if slots > 100000:
                raise ValueError('Document table cell budget exceeded')
            for row in ([headers] if headers else []) + data:
                if not isinstance(row,list) or len(row) > cols:
                    raise ValueError('Row exceeds declared columns')
                for val in row:
                    if type(val) not in (str,int,float,bool) or (type(val) is float and not math.isfinite(val)):
                        raise ValueError('Unsupported cell value')
                    if isinstance(val,str):
                        string(val,'cell')
            widths = item.get('col_widths')
            if widths is not None:
                if not isinstance(widths,list) or len(widths) != cols:
                    raise ValueError('col_widths must match cols')
                for value in widths:
                    number(value,'column width',0.000001,100000)
                body = width-mm_to_hwpunit(margins.get('left',30)+margins.get('right',30))
                if any(round(body*v/sum(widths)) < 1 for v in widths):
                    raise ValueError('Column width rounds to zero')
            merges = item.get('merges',[])
            if not isinstance(merges,list):
                raise ValueError('merges must be an array')
            occupied = set()
            for merge in merges:
                obj(merge,'row col rowSpan colSpan','merge')
                r,c,rs,cs = (merge.get('row',0),merge.get('col',0),merge.get('rowSpan',1),merge.get('colSpan',1))
                for value,lower in ((r,0),(c,0),(rs,1),(cs,1)):
                    number(value,'merge coordinate/span',lower,10000,True)
                if r+rs > total or c+cs > cols:
                    raise ValueError('Merge outside table')
                region = {(i,j) for i in range(r,r+rs) for j in range(c,c+cs)}
                if occupied & region:
                    raise ValueError('Overlapping merges')
                occupied.update(region)
            style(item.get('style',{}), True)
        else:
            raise ValueError('Unsupported content type: ' + repr(kind))


def create_rich_document(doc_spec: dict, output_path: Path) -> None:
    """Main entry: build a formatted HWPX document from JSON spec."""
    _validate_spec(doc_spec)
    if not BASE_DIR.is_dir():
        raise SystemExit(f"ERROR: Base template not found at {BASE_DIR}")

    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir) / "build"
        shutil.copytree(str(BASE_DIR), str(work))

        # Reset note counters for each new document
        _reset_note_counters()

        alloc = IDAllocator()

        header_path = work / "Contents" / "header.xml"
        section_path = work / "Contents" / "section0.xml"

        # Step 1: Inject styles into header.xml
        style_map, table_bf_map, font_id_map = inject_styles_into_header(
            header_path, doc_spec, alloc
        )

        # Re-parse the updated header for table charPr generation
        header_tree = etree.parse(str(header_path))
        header_root = header_tree.getroot()

        # Step 2: Build section0.xml
        sec_root = build_section_xml(
            doc_spec, style_map, table_bf_map, alloc,
            header_root, font_id_map,
        )

        # Write section0.xml
        sec_tree = etree.ElementTree(sec_root)
        sec_tree.write(
            str(section_path),
            xml_declaration=True,
            encoding="UTF-8",
            pretty_print=True,
        )

        # Write updated header (table charPr might have been added)
        header_tree.write(
            str(header_path),
            xml_declaration=True,
            encoding="UTF-8",
            pretty_print=True,
        )

        # Step 3: Pack into HWPX
        with atomic_output_path(output_path) as staged:
            pack_hwpx(work, staged)
            errors = validate(str(staged))
            if errors:
                raise ValueError("HWPX validation failed: " + "; ".join(errors))

    print(f"SUCCESS: Created {output_path}")
    print(f"  Styles defined: {', '.join(style_map.keys())}")
    print(f"  Content items: {len(doc_spec.get('content', []))}")


def main():
    """CLI entry point: parse arguments and create an HWPX document."""
    parser = argparse.ArgumentParser(
        description="Create richly formatted HWPX documents via XML-first approach"
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        help="JSON input file with document specification. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Output .hwpx file path",
    )
    args = parser.parse_args()

    if args.input:
        require_distinct_paths(args.input, args.output)
        if not args.input.is_file():
            raise SystemExit(f"ERROR: Input file not found: {args.input}")
        with open(args.input, "r", encoding="utf-8") as f:
            doc_spec = json.load(f)
    else:
        doc_spec = json.load(sys.stdin)

    content_count = len(doc_spec.get("content", []))
    if content_count > 500:
        print(
            f"WARNING: {content_count} content items detected. Processing may take a while.",
            file=sys.stderr,
        )

    create_rich_document(doc_spec, args.output)


if __name__ == "__main__":
    main()
