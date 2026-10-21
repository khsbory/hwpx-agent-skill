#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hwpx_table_parser
=================

A dependency-light, reusable parser that extracts *table structure* (not just
flattened text) from HWPX (Hancom OWPML / Office Open XML for HWP) documents.

Design goals
------------
* Uses the shared bounded ZIP/XML safety layer (requires lxml). The table
  reader also supports a separately guarded stdlib ElementTree backend.
* Namespace-prefix agnostic: namespace URIs are neither hard-coded nor
  validated. Matching is done purely on the *local* name of elements and
  attributes (tbl, tr, tc, cellAddr, faceColor, ...), so any prefix (hp:, a
  different prefix, or even a differing URI) works identically. This is *not*
  "dynamic namespace extraction" -- the namespace is simply never consulted.
* Reconstructs a real 2-D grid (rows x cols) using each cell's
  ``cellAddr`` (origin) + ``cellSpan`` (merge extent). Spanned-over slots are
  recorded as "occupied" cells that point back to their origin coordinate.
  When ``cellAddr`` is missing or unparsable the origin falls back to
  ``(0, 0)``; a missing ``cellSpan`` falls back to span ``1x1``. Documents that
  omit coordinates therefore pile cells onto (0,0) and ``validate()`` flags the
  resulting collisions.
* Nested tables (a ``tbl`` living inside a ``tc``) are parsed recursively and
  attached to the owning cell as child tables.
* Drawing objects / text boxes (``rect``, ``ellipse``, ``polygon``, ``line``,
  ``arc``, ``curve``, ``connectLine``, ``container``, ``pic``) are inspected:
  their ``drawText`` paragraph text is harvested into ``shape_texts`` (each a
  ``ShapeTextBlock`` with ``source='textbox'``), and any table living inside a
  shape is collected into ``tables`` tagged with its enclosing shape via the
  ``container`` / ``container_path`` fields so body tables and text-box tables
  are distinguishable. Grouped shapes (``container``) are walked recursively.
* Best-effort formatting: per-run bold flag and per-cell background color are
  resolved against ``Contents/header.xml`` (charPr / borderFill). The
  background color is taken specifically from ``borderFill > fillBrush >
  winBrush@faceColor`` -- border-side brushes (leftBorder/rightBorder/...) are
  ignored so a border color is never mistaken for the fill. When the reference
  cannot be resolved we keep the raw style id so callers can decide.

The module is also a CLI entry point. Run with ``--help`` for usage.

All inline comments and emitted log/console messages are intentionally in
English for portability; user-facing guidance lives in the skill references.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from document_safety import (DocumentSafetyError, validate_archive,
    read_checked_member, parse_xml, atomic_output_path, require_distinct_paths)
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --- Optional lxml acceleration -------------------------------------------------
# We prefer the stdlib ElementTree for guaranteed availability. lxml, when
# installed, is only used to *read/parse* bytes faster; the rest of the code
# operates on the resulting Element objects through the common API subset
# (tag, attrib/get, iter, findall is avoided in favor of manual traversal so
# the two backends behave identically).
try:  # pragma: no cover - exercised only when lxml is installed
    from lxml import etree as _LXML_ET  # type: ignore

    _HAVE_LXML = True
except Exception:  # pragma: no cover
    _LXML_ET = None
    _HAVE_LXML = False

import xml.etree.ElementTree as _STD_ET

# Conservative application budgets; schema validity is a separate concern.
MAX_DOCUMENT_SLOTS = 1_000_000
MAX_DOCUMENT_ROWS = 100_000
MAX_DOCUMENT_COLS = 100_000
MAX_CELLS = 100_000
MAX_TABLES = 2000
MAX_SPAN_WORK = 1_000_000
MAX_NESTING = 32
MAX_XML_NODES = 250_000
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_RENDERED_UTF8_BYTES = 64 * 1024 * 1024


def _preflight_render(tables, fmt, expand_merges=True):
    """Bound aggregate UTF-8 output/text work before matrices or string joins.

    Count references, not repeated strings. The fixed allowances conservatively
    cover labels, separators, numeric coordinates and grid padding. Markdown
    expansion is charged exactly per source string (including escaped pipes,
    backslashes and <br>). Cache by identity so merged text is encoded once.
    """
    if fmt not in ('matrix', 'markdown', 'grid'):
        return
    total = 256  # flat-projection notice and final separators
    for table in tables:
        total += 512 + 4 * (len(table.source) + len(table.container)
                           + sum(len(p) for p in table.container_path))
        costs = {}
        for row in table.grid:
            total += 16
            for cell in row:
                origin = cell
                if cell is not None and cell.is_occupied:
                    origin = (table.cell_at(*cell.origin)
                              if expand_merges and cell.origin is not None else None)
                if fmt == 'grid':
                    # The grid only renders bounded previews and merge labels.
                    total += 256
                else:
                    cost = 0
                    if origin is not None:
                        key = id(origin)
                        if key not in costs:
                            text = origin.text
                            size = len(text.encode('utf-8'))
                            if fmt == 'markdown':
                                size += text.count('\\') + text.count('|') + 3 * text.count('\n')
                            costs[key] = size
                        cost = costs[key]
                    total += cost + 16
                if total > MAX_RENDERED_UTF8_BYTES:
                    raise ValueError('rendered UTF-8/text-work budget exceeded')
        if total > MAX_RENDERED_UTF8_BYTES:
            raise ValueError('rendered UTF-8/text-work budget exceeded')


# ------------------------------------------------------------------------------
# Low-level XML helpers (backend-agnostic, namespace-agnostic)
# ------------------------------------------------------------------------------
def _parse_xml_bytes(data: bytes):
    """Parse XML bytes into an Element, using lxml if available else stdlib.

    Returns the root Element. Both backends expose ``.tag`` and iteration over
    children, which is all the traversal helpers below need.
    """
    if len(data) > MAX_XML_BYTES:
        raise DocumentSafetyError('XML byte limit exceeded')
    if _HAVE_LXML:
        return parse_xml(data)
    # The parser callback rejects declarations before entity expansion, including
    # UTF-16 input (a raw ASCII byte scan would miss it).
    class NoDTD(_STD_ET.TreeBuilder):
        def __init__(self):
            super().__init__()
            self.count = self.depth = 0

        def start(self, tag, attrs):
            self.count += 1
            self.depth += 1
            if self.count > MAX_XML_NODES or self.depth > 128:
                raise DocumentSafetyError('XML node/depth limit exceeded')
            return super().start(tag, attrs)

        def end(self, tag):
            self.depth -= 1
            return super().end(tag)

        def doctype(self, name, pubid, system):
            raise DocumentSafetyError('DTD/DOCTYPE/entity declarations forbidden')
    try:
        return _STD_ET.fromstring(data, parser=_STD_ET.XMLParser(target=NoDTD()))
    except _STD_ET.ParseError as exc:
        raise DocumentSafetyError(f'Malformed XML: {exc}') from exc


def _local_name(tag) -> str:
    """Return the namespace-stripped local name of an element tag.

    ElementTree expands tags to ``{uri}local``. lxml may also yield comments or
    processing instructions whose ``tag`` is a callable; we coerce those to ''.
    """
    if not isinstance(tag, str):
        # lxml comments / PIs have non-string tags.
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _iter_children(elem):
    """Yield direct child elements (skipping comments/PIs)."""
    for child in list(elem):
        if isinstance(child.tag, str):
            yield child


def _find_child(elem, local: str):
    """First *direct* child with the given local name, or None."""
    for child in _iter_children(elem):
        if _local_name(child.tag) == local:
            return child
    return None


def _find_children_simple(elem, local: str) -> List:
    """All *direct* children with the given local name (document order)."""
    out = []
    for c in _iter_children(elem):
        if _local_name(c.tag) == local:
            out.append(c)
    return out


def _attr(elem, name: str, default=None):
    """Attribute lookup that tolerates namespaced attribute keys.

    HWPX table attributes (colAddr, colSpan, faceColor, ...) are unprefixed in
    practice, so a plain lookup works. We still fall back to a local-name scan
    to be safe across exporters.
    """
    val = elem.get(name)
    if val is not None:
        return val
    for k, v in elem.attrib.items():
        if _local_name(k) == name:
            return v
    return default


def _iter_descendants_local(elem, local: str):
    """Depth-first iterate over *all* descendants (and self excluded) whose
    local name matches ``local``. Implemented manually so it works identically
    on lxml and ElementTree and so we can prune when needed (see table walk)."""
    stack = list(_iter_children(elem))
    # Use an explicit stack to avoid recursion limits on deep documents.
    # We want document order, so reverse-extend.
    stack.reverse()
    while stack:
        node = stack.pop()
        if _local_name(node.tag) == local:
            yield node
        kids = list(_iter_children(node))
        kids.reverse()
        stack.extend(kids)


# ------------------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------------------
@dataclass
class CellStyle:
    """Best-effort style snapshot for a cell.

    ``bold`` is True when *any* run in the cell carries a bold charPr (a coarse
    but useful signal for header detection). ``bg_color`` is the cell's
    background fill color (``#RRGGBB``) or None when transparent/unresolved.
    ``border_fill_id`` / ``char_pr_ids`` retain the raw style ids so callers can
    do their own resolution if needed.
    """

    bold: bool = False
    bg_color: Optional[str] = None
    border_fill_id: Optional[str] = None
    char_pr_ids: List[str] = field(default_factory=list)


@dataclass
class Cell:
    """A single table cell.

    ``is_occupied`` marks a grid slot that is covered by a merged neighbor; its
    ``origin`` points to the (row, col) of the cell that actually owns the span.
    Real cells have ``is_occupied=False`` and ``origin == (row, col)``.
    """

    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    text: str = ""
    paragraphs: List[str] = field(default_factory=list)
    style: CellStyle = field(default_factory=CellStyle)
    children: List["Table"] = field(default_factory=list)  # nested tables
    is_occupied: bool = False
    origin: Optional[Tuple[int, int]] = None

    def to_dict(self) -> dict:
        d = {
            "row": self.row,
            "col": self.col,
            "rowSpan": self.row_span,
            "colSpan": self.col_span,
            "text": self.text,
            "paragraphs": self.paragraphs,
            "style": {
                "bold": self.style.bold,
                "bgColor": self.style.bg_color,
                "borderFillId": self.style.border_fill_id,
                "charPrIds": self.style.char_pr_ids,
            },
            "isOccupied": self.is_occupied,
            "origin": list(self.origin) if self.origin else None,
        }
        if self.children:
            d["nested"] = [t.to_dict() for t in self.children]
        return d


@dataclass
class Table:
    """A parsed table: declared dimensions, origin cells, and a dense grid.

    ``cells`` holds only the *origin* cells in document order. ``grid`` is the
    dense rows x cols matrix where every slot is a ``Cell`` (either an origin or
    an occupied placeholder), enabling true 2-D access.
    """

    index: int
    declared_rows: Optional[int] = None
    declared_cols: Optional[int] = None
    n_rows: int = 0
    n_cols: int = 0
    cells: List[Cell] = field(default_factory=list)
    grid: List[List[Optional[Cell]]] = field(default_factory=list)
    source: str = ""  # which section*.xml this table came from
    depth: int = 0    # 0 = top-level, >0 = nested
    # Provenance of the table within the document flow. Empty string means the
    # table sits directly in the body text (the historical default). A non-empty
    # value is the local name of the enclosing drawing object / text box (e.g.
    # "rect", "ellipse", "container"), i.e. the table was found *inside a shape*.
    container: str = ""
    # For a table inside a shape, the ordered chain of shape local names from the
    # outermost shape down to the immediate parent (e.g. ["container", "rect"]).
    # Empty for body tables.
    container_path: List[str] = field(default_factory=list)
    host_table_index: Optional[int] = None
    host_cell: Optional[Tuple[int, int]] = None

    # -- conversions -----------------------------------------------------------
    def cell_at(self, row: int, col: int) -> Optional[Cell]:
        if 0 <= row < self.n_rows and 0 <= col < self.n_cols:
            return self.grid[row][col]
        return None

    def to_matrix(self, expand_merges: bool = True) -> List[List[str]]:
        """Return a 2-D list of cell texts.

        With ``expand_merges`` (default), every covered slot repeats the
        origin's text so the matrix is rectangular and each column lines up.
        Otherwise covered slots are empty strings.
        """
        _preflight_render([self], 'matrix', expand_merges)
        out: List[List[str]] = []
        for r in range(self.n_rows):
            row_vals: List[str] = []
            for c in range(self.n_cols):
                cell = self.grid[r][c]
                if cell is None:
                    row_vals.append("")
                    continue
                if cell.is_occupied:
                    if expand_merges and cell.origin is not None:
                        # Use cell_at so an out-of-range origin (defensive: a
                        # malformed grid could point outside n_rows/n_cols)
                        # yields "" instead of raising IndexError.
                        oc = self.cell_at(cell.origin[0], cell.origin[1])
                        row_vals.append(oc.text if oc else "")
                    else:
                        row_vals.append("")
                else:
                    row_vals.append(cell.text)
            out.append(row_vals)
        return out

    def to_markdown(self) -> str:
        """Render a Markdown table. Merges are expanded (repeated) so the table
        stays rectangular; newlines inside cells become ``<br>`` and pipes are
        escaped. Markdown has no rowspan/colspan, so this is a faithful *flat*
        projection, not a merge-preserving one."""
        _preflight_render([self], 'markdown')
        matrix = self.to_matrix(expand_merges=True)
        if not matrix:
            return ""

        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")

        lines = []
        header = matrix[0]
        lines.append("| " + " | ".join(esc(x) for x in header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for row in matrix[1:]:
            # pad/truncate to header width defensively
            cells = (row + [""] * len(header))[: len(header)]
            lines.append("| " + " | ".join(esc(x) for x in cells) + " |")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "depth": self.depth,
            "source": self.source,
            # "" for body tables; enclosing shape local name for tables that
            # live inside a drawing object / text box.
            "container": self.container,
            "containerPath": list(self.container_path),
            "hostTableIndex": self.host_table_index,
            "hostCell": list(self.host_cell) if self.host_cell is not None else None,
            "declaredRows": self.declared_rows,
            "declaredCols": self.declared_cols,
            "rows": self.n_rows,
            "cols": self.n_cols,
            "cells": [c.to_dict() for c in self.cells],
        }

    def grid_dump(self) -> str:
        """Human-readable ASCII dump of the grid for eyeball verification.

        Each slot shows ``r,c`` plus a compact text preview. Occupied slots show
        ``^origin`` so merges are obvious.
        """
        _preflight_render([self], 'grid')
        origin = ("textbox:" + ">".join(self.container_path)) if self.container else "body"
        lines = [
            f"[Table #{self.index}] depth={self.depth} "
            f"size={self.n_rows}x{self.n_cols} "
            f"(declared {self.declared_rows}x{self.declared_cols}) "
            f"src={self.source} origin={origin}"
        ]
        for r in range(self.n_rows):
            row_strs = []
            for c in range(self.n_cols):
                cell = self.grid[r][c]
                if cell is None:
                    row_strs.append("(empty)".ljust(22))
                    continue
                if cell.is_occupied and cell.origin is not None:
                    row_strs.append(("^merge->%d,%d" % cell.origin).ljust(22))
                else:
                    # Replacing newlines preserves character count; slice first
                    # to avoid processing the full text for a 14-char preview.
                    preview = (cell.text or "")[:15].replace("\n", "/")
                    if len(preview) > 14:
                        preview = preview[:13] + "…"
                    tag = ""
                    if cell.row_span > 1 or cell.col_span > 1:
                        tag = "[%dx%d]" % (cell.row_span, cell.col_span)
                    if cell.children:
                        tag += "{+%d tbl}" % len(cell.children)
                    row_strs.append((f"{r},{c}{tag}:{preview}").ljust(22))
            lines.append(" | ".join(row_strs))
        return "\n".join(lines)


# ------------------------------------------------------------------------------
# Drawing-object / text-box model
# ------------------------------------------------------------------------------
# OWPML (HWPX) local element names for drawing objects that can carry text.
# Confirmed against the Hancom-compatible OWPML object model:
#   * rect / ellipse / polygon / line / arc / curve / connectLine are
#     "DrawingObject"s; each may own an <hp:drawText> whose <hp:subList> holds
#     the text-box paragraphs (and possibly a <hp:tbl>).
#   * container groups child shapes recursively (a "shape group").
#   * pic (picture) is image-only in the model, but some exporters still attach a
#     drawText to it, so we include it defensively.
# We match on *local* names only (namespace-agnostic), consistent with the rest
# of the parser.
_SHAPE_LOCALS = frozenset(
    {
        "rect",
        "ellipse",
        "polygon",
        "line",
        "arc",
        "curve",
        "connectLine",
        "container",
        "pic",
    }
)
# The text holder inside a drawing object.
_DRAWTEXT_LOCAL = "drawText"
# The paragraph-list holder (also used by table cells).
_SUBLIST_LOCAL = "subList"
# Shape group container local name.
_CONTAINER_LOCAL = "container"


@dataclass
class ShapeTextBlock:
    """Text captured from a drawing object / text box (not from body flow).

    A single ``ShapeTextBlock`` corresponds to one text-bearing shape (its
    ``<hp:drawText>`` content). ``paragraphs`` are the shape's paragraph strings
    in document order; ``text`` is them joined by newlines. ``tables`` holds any
    tables that live *inside this shape's text* (already present in the parser's
    global ``tables`` list too, tagged with the same container); they are also
    referenced here so a caller can navigate shape -> its tables directly.

    ``source`` is always ``"textbox"`` so callers can cheaply distinguish shape
    text from body text. ``shape_type`` is the shape's local element name
    (``"rect"``, ``"ellipse"``, ...). ``container_path`` is the chain of enclosing
    shape local names from outermost to this shape (inclusive), so grouped shapes
    keep their nesting context.
    """

    shape_type: str
    paragraphs: List[str] = field(default_factory=list)
    text: str = ""
    source: str = "textbox"
    doc_source: str = ""  # which section*.xml this shape came from
    container_path: List[str] = field(default_factory=list)
    host_table_index: Optional[int] = None
    host_cell: Optional[Tuple[int, int]] = None
    tables: List["Table"] = field(default_factory=list)
    xml_order: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "shapeType": self.shape_type,
            "docSource": self.doc_source,
            "containerPath": list(self.container_path),
            "hostTableIndex": self.host_table_index,
            "hostCell": list(self.host_cell) if self.host_cell is not None else None,
            "paragraphs": self.paragraphs,
            "text": self.text,
            "tableIndices": [t.index for t in self.tables],
        }


# ------------------------------------------------------------------------------
# Style resolver (header.xml -> bold / bg color)
# ------------------------------------------------------------------------------
class _StyleResolver:
    """Resolves charPr ids -> bold, and borderFill ids -> background color.

    The background color for a borderFill is read only from its
    ``fillBrush > winBrush@faceColor``; border-side brushes are ignored so a
    border color is never mistaken for the fill (``faceColor="none"`` -> None).

    Built lazily from ``Contents/header.xml``. If header.xml is missing or the
    structure differs, lookups simply return safe defaults so the rest of the
    parser keeps working (formatting is best-effort by contract).
    """

    def __init__(self, header_bytes: Optional[bytes]):
        self._char_bold: Dict[str, bool] = {}
        self._fill_bg: Dict[str, Optional[str]] = {}
        if not header_bytes:
            return
        try:
            root = _parse_xml_bytes(header_bytes)
        except DocumentSafetyError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            sys.stderr.write(f"[hwpx] WARN: failed to parse header.xml: {exc}\n")
            return
        self._index_char_props(root)
        self._index_border_fills(root)

    def _index_char_props(self, root) -> None:
        # charPr elements may be nested under charProperties; scan all.
        for cp in _iter_descendants_local(root, "charPr"):
            cid = _attr(cp, "id")
            if cid is None:
                continue
            has_bold = _find_child(cp, "bold") is not None
            self._char_bold[str(cid)] = has_bold

    def _index_border_fills(self, root) -> None:
        for bf in _iter_descendants_local(root, "borderFill"):
            bid = _attr(bf, "id")
            if bid is None:
                continue
            color = None
            # The background fill lives specifically under <fillBrush>. Scope to
            # the fillBrush subtree FIRST, then read its winBrush@faceColor.
            # Border definitions (leftBorder/rightBorder/...) also carry their
            # own winBrush and usually precede fillBrush in document order, so a
            # naive "first winBrush anywhere under borderFill" would pick up a
            # border color and mislabel it as the background. Restricting to
            # fillBrush avoids that.
            for fill_brush in _iter_descendants_local(bf, "fillBrush"):
                brush = None
                for wb in _iter_descendants_local(fill_brush, "winBrush"):
                    brush = wb
                    break
                if brush is not None:
                    face = _attr(brush, "faceColor")
                    # 'none' means transparent; only keep real colors.
                    if face and face.lower() != "none":
                        color = face
                # Use the first fillBrush only; deeper ones (if any) are ignored.
                break
            self._fill_bg[str(bid)] = color

    def char_is_bold(self, char_pr_id: Optional[str]) -> bool:
        if char_pr_id is None:
            return False
        return self._char_bold.get(str(char_pr_id), False)

    def fill_bg_color(self, border_fill_id: Optional[str]) -> Optional[str]:
        if border_fill_id is None:
            return None
        return self._fill_bg.get(str(border_fill_id))


# ------------------------------------------------------------------------------
# Core parser
# ------------------------------------------------------------------------------
class HwpxTableParser:
    """Parse all tables from an HWPX file, preserving document order.

    Typical use::

        doc = HwpxTableParser.load("plan.hwpx")
        for table in doc.tables:
            print(table.grid_dump())
    """

    def __init__(self) -> None:
        self.tables: List[Table] = []
        # Text blocks harvested from drawing objects / text boxes, in document
        # order. Empty for documents without shapes (so existing callers that
        # never look at this attribute are unaffected).
        self.shape_texts: List[ShapeTextBlock] = []
        self.sections: List[str] = []  # names of section*.xml processed
        self._resolver: Optional[_StyleResolver] = None
        self._table_counter: int = 0
        self._budgets = {}
        self._indices = {}
        self._element_order = {}
        self._host = (None, None)

    def _charge(self, key, amount, limit):
        total = self._budgets.get(key, 0) + amount
        if total > limit:
            raise DocumentSafetyError(f'Document {key} budget limit exceeded')
        self._budgets[key] = total

    # -- loading ---------------------------------------------------------------
    @classmethod
    def load(cls, hwpx_path: str) -> "HwpxTableParser":
        """Open ``hwpx_path`` (a .hwpx zip), parse every section's tables and
        return a populated parser. Korean paths/filenames are fine: zipfile is
        given the native path string and entry text is decoded as UTF-8."""
        inst = cls()
        inst._load_impl(hwpx_path)
        return inst

    def _load_impl(self, hwpx_path: str) -> None:
        with zipfile.ZipFile(hwpx_path, "r") as zf:
            validate_archive(zf)
            names = zf.namelist()
            xml_total = sum(info.file_size for info in zf.infolist()
                            if info.filename.lower().endswith('.xml'))
            if xml_total > MAX_XML_BYTES:
                raise DocumentSafetyError('XML byte budget limit exceeded')
            if ('mimetype' not in names or
                    read_checked_member(zf, 'mimetype').strip() != b'application/hwp+zip'):
                raise DocumentSafetyError('Not an HWPX container: missing/invalid mimetype')

            # Resolve style references from header.xml when present.
            header_name = _pick_first(
                names,
                ["Contents/header.xml", "header.xml"],
            )
            header_bytes = read_checked_member(zf, header_name) if header_name else None
            self._resolver = _StyleResolver(header_bytes)

            # Collect Contents/section*.xml in numeric order (section0, 1, ...).
            section_names = _sorted_sections(names)
            if not section_names:
                raise DocumentSafetyError('Not an HWPX container: no numbered section XML')
            for sname in section_names:
                self.sections.append(sname)
                data = read_checked_member(zf, sname)
                root = _parse_xml_bytes(data)
                if _local_name(root.tag) != 'sec':
                    raise DocumentSafetyError(f'Invalid section root in {sname}: expected sec')
                self._parse_section(root, sname)

    # -- section / table / shape walking --------------------------------------
    def _parse_section(self, root, source: str) -> None:
        """Walk a section root in document order and collect both body tables
        and drawing-object / text-box content.

        Dispatch rules (document-order DFS):
        * A ``tbl`` reached in the *body* flow (not inside a shape) is a
          top-level body table (``container=""``). We parse it and prune, since
          nested tables are handled during cell parsing.
        * A shape element (rect/ellipse/.../container/pic) is handed to
          ``_parse_shape``, which owns that entire subtree: it harvests the
          shape's text-box paragraphs and any tables inside the shape (tagged
          with the shape as their container). We then prune the main walk into
          that shape so its inner tables are not also mistaken for body tables.
        * Anything else keeps descending.
        """
        stack = [(root, 0)]
        while stack:
            node, depth = stack.pop()
            self._charge('xml nodes', 1, MAX_XML_NODES)
            self._element_order[node] = self._budgets['xml nodes']
            if _local_name(node.tag) == 'tbl':
                self._indices[node] = self._table_counter
                self._table_counter += 1
            if depth > 128:
                raise DocumentSafetyError('XML depth limit exceeded')
            stack.extend((child, depth + 1) for child in reversed(list(_iter_children(node))))
        self._walk_flow(root, source=source, container_path=[])
        self.tables.sort(key=lambda t: t.index)
        self.shape_texts.sort(key=lambda s: s.xml_order)

    def _walk_flow(self, root, source: str, container_path: List[str]) -> None:
        """Document-order DFS over a body/paragraph subtree, dispatching tables
        and shapes. ``container_path`` is the chain of enclosing shape local
        names (empty at body level)."""
        stack = list(_iter_children(root))
        stack.reverse()
        while stack:
            node = stack.pop()
            ln = _local_name(node.tag)
            if ln == "tbl":
                # A table in the current flow. When container_path is empty this
                # is a body table; otherwise it belongs to the enclosing shape.
                container = container_path[-1] if container_path else ""
                table = self._parse_table(
                    node,
                    source=source,
                    depth=0,
                    container=container,
                    container_path=list(container_path),
                )
                self.tables.append(table)
                # Do NOT descend; nested tables handled by _parse_table.
                continue
            if ln in _SHAPE_LOCALS:
                # Hand the whole shape subtree to the shape parser and prune.
                self._parse_shape(node, source=source, container_path=container_path)
                continue
            kids = list(_iter_children(node))
            kids.reverse()
            stack.extend(kids)

    def _parse_shape(self, shape_elem, source: str, container_path: List[str]) -> None:
        """Parse a single drawing object / text box.

        Collects, in document order:
        * the shape's own text-box paragraphs (from ``<hp:drawText>``), emitted
          as a ``ShapeTextBlock`` with ``source='textbox'``;
        * any tables that live in the shape's text, tagged with this shape as
          their container and appended to the global ``tables`` list;
        * child shapes (for a ``container`` group, or a shape whose drawText body
          itself embeds another shape), recursively.

        ``container_path`` is the chain of ancestor shape local names; this shape
        is appended for its own descendants.
        """
        shape_type = _local_name(shape_elem.tag)
        this_path = container_path + [shape_type]
        if len(this_path) > MAX_NESTING:
            raise DocumentSafetyError('Shape nesting depth limit exceeded')

        # The text box body lives under <hp:drawText>. There is normally at most
        # one, but we tolerate several and merge their paragraphs in order.
        draw_texts = _find_children_simple(shape_elem, _DRAWTEXT_LOCAL)

        paragraphs: List[str] = []
        block_tables: List[Table] = []

        for dt in draw_texts:
            # Gather this shape's own paragraph text, excluding text that belongs
            # to a nested table or a nested shape (their text is captured
            # separately), mirroring the cell-vs-nested-table rule.
            for p in self._iter_shape_paragraphs(dt):
                text = self._paragraph_text(p)
                if text or not _paragraph_wraps_block(p):
                    paragraphs.append(text)
            # Tables directly in this shape's drawText become container tables.
            for ntbl in self._iter_flow_tables(dt):
                t = self._parse_table(
                    ntbl,
                    source=source,
                    depth=0,
                    container=shape_type,
                    container_path=list(this_path),
                )
                self.tables.append(t)
                block_tables.append(t)

        # Emit a text block for this shape when it carried any text. Shapes with
        # no drawText (e.g. a bare picture or a pure grouping container) produce
        # no text block, but we still recurse for their children below.
        if paragraphs or block_tables:
            text = "\n".join(paragraphs)
            self.shape_texts.append(
                ShapeTextBlock(
                    shape_type=shape_type,
                    paragraphs=paragraphs,
                    text=text,
                    doc_source=source,
                    container_path=list(this_path),
                    tables=block_tables,
                    xml_order=self._element_order.get(shape_elem, len(self.shape_texts)),
                    host_table_index=self._host[0],
                    host_cell=self._host[1],
                )
            )

        # Recurse into child shapes. For a <hp:container> these are its grouped
        # members; for any shape they may also appear inside its drawText body.
        for child_shape in self._iter_child_shapes(shape_elem):
            self._parse_shape(child_shape, source=source, container_path=this_path)

    def _iter_shape_paragraphs(self, drawtext_elem):
        """Yield paragraphs (p) that belong to this shape's own text box, not
        descending into nested tables *or* nested shapes (whose text is captured
        by their own handlers)."""
        stack = list(_iter_children(drawtext_elem))
        stack.reverse()
        while stack:
            node = stack.pop()
            ln = _local_name(node.tag)
            if ln == "tbl" or ln in _SHAPE_LOCALS:
                # Prune: this subtree's text belongs to a table / child shape.
                continue
            if ln == "p":
                yield node
            kids = list(_iter_children(node))
            kids.reverse()
            stack.extend(kids)

    def _iter_flow_tables(self, drawtext_elem):
        """Yield the outermost tables inside a shape's drawText body (document
        order), without descending into nested tables or nested shapes."""
        stack = list(_iter_children(drawtext_elem))
        stack.reverse()
        while stack:
            node = stack.pop()
            ln = _local_name(node.tag)
            if ln == "tbl":
                yield node
                continue  # nested tables handled recursively by _parse_table
            if ln in _SHAPE_LOCALS:
                # A table inside a *child* shape belongs to that shape, not here.
                continue
            kids = list(_iter_children(node))
            kids.reverse()
            stack.extend(kids)

    def _iter_child_shapes(self, shape_elem):
        """Yield the outermost child shapes of a shape (document order).

        We must not re-enter the shape's *own* ``<hp:drawText>`` paragraph text
        as a child (it was already harvested), but a drawText body may legitimately
        contain a nested shape, so we descend through drawText while pruning at
        the first shape we meet (deeper nesting is handled recursively)."""
        out = []
        stack = list(_iter_children(shape_elem))
        stack.reverse()
        while stack:
            node = stack.pop()
            ln = _local_name(node.tag)
            if ln in _SHAPE_LOCALS:
                out.append(node)
                continue  # don't descend; recursion handles deeper levels
            if ln == "tbl":
                # A shape embedded inside a table cell belongs to that table's
                # cell parsing, not to this shape's child list.
                continue
            kids = list(_iter_children(node))
            kids.reverse()
            stack.extend(kids)
        return out

    def _paragraph_text(self, p) -> str:
        """Flatten one paragraph element to a string (runs -> t tokens)."""
        texts: List[str] = []
        for run in _find_children_simple(p, "run"):
            for t in _find_children_simple(run, "t"):
                texts.append(_collect_text(t))
        return "".join(texts)

    def _parse_table(
        self,
        tbl_elem,
        source: str,
        depth: int,
        container: str = "",
        container_path: Optional[List[str]] = None,
    ) -> Table:
        if depth > MAX_NESTING:
            raise DocumentSafetyError('Table nesting depth limit exceeded')
        self._charge('tables', 1, MAX_TABLES)
        idx = self._indices.get(tbl_elem)
        if idx is None:
            idx = self._table_counter
            self._table_counter += 1

        declared_rows = _to_int(_attr(tbl_elem, "rowCnt"))
        declared_cols = _to_int(_attr(tbl_elem, "colCnt"))
        for value in (declared_rows, declared_cols):
            if value is not None and not 0 <= value <= 4096:
                raise DocumentSafetyError('Table dimension limit exceeded')
        if (declared_rows or 0) * (declared_cols or 0) > 100_000:
            raise DocumentSafetyError('Table slot limit exceeded')

        table = Table(
            index=idx,
            declared_rows=declared_rows,
            declared_cols=declared_cols,
            source=source,
            depth=depth,
            container=container,
            container_path=list(container_path) if container_path else [],
            host_table_index=self._host[0],
            host_cell=self._host[1],
        )

        origin_cells: List[Cell] = []
        max_row_extent = 0
        max_col_extent = 0

        # Iterate rows (tr) then cells (tc) in document order.
        for tr in _find_children_simple(tbl_elem, "tr"):
            for tc in _find_children_simple(tr, "tc"):
                addr = _find_child(tc, 'cellAddr')
                coords = ((_to_int(_attr(addr, 'rowAddr')) or 0,
                           _to_int(_attr(addr, 'colAddr')) or 0) if addr is not None else (0, 0))
                previous_host = self._host
                self._host = (idx, coords)
                try:
                    cell = self._parse_cell(tc, source=source, depth=depth,
                        container=container, container_path=container_path)
                finally:
                    self._host = previous_host
                origin_cells.append(cell)
                max_row_extent = max(max_row_extent, cell.row + cell.row_span)
                max_col_extent = max(max_col_extent, cell.col + cell.col_span)

        # Grid size = max(addr + span). Fall back to declared dims when the
        # document has no cells (degenerate) or declares a larger frame.
        n_rows = max(max_row_extent, declared_rows or 0)
        n_cols = max(max_col_extent, declared_cols or 0)
        if n_rows > 4096 or n_cols > 4096 or n_rows * n_cols > 100_000:
            raise DocumentSafetyError('Table grid dimension/slot limit exceeded')
        self._charge('slots', n_rows * n_cols, MAX_DOCUMENT_SLOTS)
        self._charge('rows', n_rows, MAX_DOCUMENT_ROWS)
        self._charge('columns', n_cols, MAX_DOCUMENT_COLS)
        table.n_rows = n_rows
        table.n_cols = n_cols

        # Build the dense grid and place every cell (plus merge placeholders).
        grid: List[List[Optional[Cell]]] = [
            [None for _ in range(n_cols)] for _ in range(n_rows)
        ]
        for cell in origin_cells:
            r0, c0 = cell.row, cell.col
            for dr in range(cell.row_span):
                for dc in range(cell.col_span):
                    rr, cc = r0 + dr, c0 + dc
                    if not (0 <= rr < n_rows and 0 <= cc < n_cols):
                        # Out-of-range span; skip defensively (validate() flags).
                        continue
                    if dr == 0 and dc == 0:
                        grid[rr][cc] = cell
                    else:
                        grid[rr][cc] = Cell(
                            row=rr,
                            col=cc,
                            row_span=1,
                            col_span=1,
                            text="",
                            is_occupied=True,
                            origin=(r0, c0),
                        )
        table.grid = grid
        table.cells = origin_cells
        return table

    def _parse_cell(
        self,
        tc_elem,
        source: str,
        depth: int,
        container: str = "",
        container_path: Optional[List[str]] = None,
    ) -> Cell:
        self._charge('cells', 1, MAX_CELLS)
        addr = _find_child(tc_elem, "cellAddr")
        span = _find_child(tc_elem, "cellSpan")

        col = _to_int(_attr(addr, "colAddr")) if addr is not None else 0
        row = _to_int(_attr(addr, "rowAddr")) if addr is not None else 0
        col_span = _to_int(_attr(span, "colSpan")) if span is not None else 1
        row_span = _to_int(_attr(span, "rowSpan")) if span is not None else 1
        col = col or 0
        row = row or 0
        col_span = 1 if col_span is None else col_span
        row_span = 1 if row_span is None else row_span
        if row < 0 or col < 0 or row >= 4096 or col >= 4096:
            raise DocumentSafetyError('Cell address limit exceeded')
        if row_span <= 0 or col_span <= 0:
            raise DocumentSafetyError('Cell span must be positive')
        if (row + row_span > 4096 or col + col_span > 4096
                or row_span * col_span > 100_000):
            raise DocumentSafetyError('Cell span limit exceeded')
        self._charge('span work', row_span * col_span, MAX_SPAN_WORK)

        border_fill_id = _attr(tc_elem, "borderFillIDRef")

        # Collect paragraph texts and nested tables. We walk the *cell's* direct
        # paragraph subtree; a paragraph that itself contains a nested table is
        # recursed into and the table attached to this cell.
        paragraphs: List[str] = []
        char_pr_ids: List[str] = []
        any_bold = False
        nested_tables: List[Table] = []

        # A pruning walk separates cell paragraphs from nested tables/shapes.
        # Build a quick ancestor check: a paragraph "belongs" to this cell only
        # if none of its ancestors (within the cell) is a tbl. Since ElementTree
        # has no parent pointers, we walk paragraphs via a pruning DFS that does
        # not descend into nested tables.
        for p in self._iter_cell_paragraphs(tc_elem):
            texts: List[str] = []
            # Collect this paragraph's charPr ids / bold flag into locals first,
            # then commit them only if we keep the paragraph (see wrapper skip
            # below), so a discarded wrapper paragraph does not pollute the
            # cell's style with its (text-less) run's charPr.
            p_char_pr_ids: List[str] = []
            p_bold = False
            for run in _find_children_simple(p, "run"):
                cpid = _attr(run, "charPrIDRef")
                if cpid is not None:
                    p_char_pr_ids.append(str(cpid))
                    if self._resolver and self._resolver.char_is_bold(cpid):
                        p_bold = True
                # text tokens (t), and line breaks within a run.
                for t in _find_children_simple(run, "t"):
                    texts.append(_collect_text(t))
            joined = "".join(texts)
            # Drop a paragraph that is merely a *wrapper* around a nested table
            # or an in-cell shape: it carries no text of its own (no <t>) yet
            # holds a tbl/shape in its subtree. Emitting it would inject a
            # spurious empty paragraph (and an extra "\n" in the cell text) that
            # does not correspond to any real line in the cell. A genuinely empty
            # line (an empty <p> with no tbl/shape inside) is still preserved, so
            # blank rows in a cell survive.
            if not joined and _paragraph_wraps_block(p):
                continue
            char_pr_ids.extend(p_char_pr_ids)
            if p_bold:
                any_bold = True
            paragraphs.append(joined)

        # Parse nested tables (top-level within this cell) recursively. A nested
        # table inherits the enclosing table's container provenance (a table in
        # a text box that itself nests a table keeps container='rect', etc.).
        for ntbl in self._iter_cell_nested_tables(tc_elem):
            nested_tables.append(
                self._parse_table(
                    ntbl,
                    source=source,
                    depth=depth + 1,
                    container=container,
                    container_path=container_path,
                )
            )

        # Parse drawing objects / text boxes placed directly inside this cell.
        # Their text is captured as ShapeTextBlock(s) and any table they hold is
        # collected into the global ``tables`` list, exactly like a body/flow
        # shape. Without this, a text box in a cell would have its paragraphs
        # excluded from the cell text (correct, see _iter_cell_paragraphs) but
        # then dropped entirely; delegating to _parse_shape recaptures them.
        # The shape inherits the enclosing table's container provenance so a
        # text box inside a text-box table keeps that outer chain.
        base_path = list(container_path) if container_path else []
        for shape_elem in self._iter_cell_shapes(tc_elem):
            self._parse_shape(
                shape_elem,
                source=source,
                container_path=base_path,
            )

        bg = self._resolver.fill_bg_color(border_fill_id) if self._resolver else None
        style = CellStyle(
            bold=any_bold,
            bg_color=bg,
            border_fill_id=border_fill_id,
            char_pr_ids=char_pr_ids,
        )

        text = "\n".join(paragraphs)
        return Cell(
            row=row,
            col=col,
            row_span=row_span,
            col_span=col_span,
            text=text,
            paragraphs=paragraphs,
            style=style,
            children=nested_tables,
            is_occupied=False,
            origin=(row, col),
        )

    def _iter_cell_paragraphs(self, tc_elem):
        """Yield paragraphs (p) that belong to this cell, *not* descending into
        nested tables or nested shapes (whose paragraphs belong to the child
        table / child shape and are captured by their own handlers).

        This mirrors ``_iter_shape_paragraphs`` so the cell-parsing and
        shape-parsing walks are symmetric: a text box (rect/ellipse/...) placed
        inside a cell must not leak its drawText paragraphs into the cell's own
        text, and must not inject empty placeholder paragraphs either."""
        stack = list(_iter_children(tc_elem))
        stack.reverse()
        while stack:
            node = stack.pop()
            ln = _local_name(node.tag)
            if ln == "tbl" or ln in _SHAPE_LOCALS:
                # Prune: this subtree belongs to a nested table or child shape.
                continue
            if ln == "p":
                yield node
                # A paragraph may itself contain a nested tbl / shape; we still
                # must not gather that subtree's inner paragraphs as this cell's
                # text, so we descend but rely on the prune above when we hit it.
            kids = list(_iter_children(node))
            kids.reverse()
            stack.extend(kids)

    def _iter_cell_nested_tables(self, tc_elem):
        """Yield the *outermost* nested tables within a cell (document order).
        Tables nested deeper are handled by recursive _parse_table calls.

        A table that lives *inside a shape* placed in this cell is NOT yielded
        here: it belongs to that shape and is collected when the shape itself is
        handed to ``_parse_shape``. We therefore prune at shapes so shape-owned
        tables are not double-counted as the cell's own nested tables."""
        stack = list(_iter_children(tc_elem))
        stack.reverse()
        while stack:
            node = stack.pop()
            ln = _local_name(node.tag)
            if ln == "tbl":
                yield node
                continue  # don't descend; recursion handles deeper levels
            if ln in _SHAPE_LOCALS:
                # A table inside this shape belongs to the shape, not the cell.
                continue
            kids = list(_iter_children(node))
            kids.reverse()
            stack.extend(kids)

    def _iter_cell_shapes(self, tc_elem):
        """Yield the *outermost* drawing objects / text boxes placed directly in
        a cell (document order), pruning at the first shape (deeper nesting is
        handled recursively by ``_parse_shape``) and skipping shapes that live
        inside a nested table (those belong to that table's cells)."""
        out = []
        stack = list(_iter_children(tc_elem))
        stack.reverse()
        while stack:
            node = stack.pop()
            ln = _local_name(node.tag)
            if ln in _SHAPE_LOCALS:
                out.append(node)
                continue  # don't descend; recursion handles deeper levels
            if ln == "tbl":
                # A shape inside a nested table belongs to that table's cell
                # parsing, not to this cell's shape list.
                continue
            kids = list(_iter_children(node))
            kids.reverse()
            stack.extend(kids)
        return out

    # -- validation ------------------------------------------------------------
    def validate(self, table_idx=None) -> List[str]:
        """Return a list of human-readable warnings about structural issues:

        * fill ratio (how many grid slots are blank) per table
        * coordinate collisions (two origin cells claiming the same slot)
        * span totals that do not tile the declared grid exactly

        An empty list means no anomalies were detected.
        """
        warnings: List[str] = []
        for t in self.selected_tables(table_idx):
            warnings.extend(self._validate_table(t, recurse=False))
        return warnings

    def _validate_table(self, t: Table, recurse=True) -> List[str]:
        w: List[str] = []
        prefix = f"[Table #{t.index} d{t.depth}]"

        if t.n_rows == 0 or t.n_cols == 0:
            w.append(f"{prefix} empty grid ({t.n_rows}x{t.n_cols})")
            return w

        total = t.n_rows * t.n_cols

        # Coordinate collisions: detect origin cells whose spans overlap.
        claimed: Dict[Tuple[int, int], Tuple[int, int]] = {}
        collisions = 0
        for cell in t.cells:
            for dr in range(cell.row_span):
                for dc in range(cell.col_span):
                    rr, cc = cell.row + dr, cell.col + dc
                    key = (rr, cc)
                    if key in claimed:
                        collisions += 1
                    else:
                        claimed[key] = (cell.row, cell.col)
        if collisions:
            w.append(
                f"{prefix} {collisions} coordinate collision(s): "
                f"overlapping cellAddr/cellSpan regions"
            )

        # Fill ratio: count grid slots with no Cell object at all.
        empty_slots = sum(
            1
            for r in range(t.n_rows)
            for c in range(t.n_cols)
            if t.grid[r][c] is None
        )
        if empty_slots:
            ratio = empty_slots / total * 100.0
            w.append(
                f"{prefix} {empty_slots}/{total} grid slot(s) unfilled "
                f"({ratio:.1f}% holes) - cells may be missing or spans misaligned"
            )

        # Span coverage: sum of (rowSpan*colSpan) across origin cells should
        # equal the grid area when the table tiles perfectly.
        covered = sum(c.row_span * c.col_span for c in t.cells)
        if covered != total:
            w.append(
                f"{prefix} span coverage {covered} != grid area {total} "
                f"(declared {t.declared_rows}x{t.declared_cols})"
            )

        # Declared vs computed dimensions mismatch.
        if t.declared_rows is not None and t.declared_rows != t.n_rows:
            w.append(
                f"{prefix} declared rowCnt={t.declared_rows} "
                f"!= computed rows={t.n_rows}"
            )
        if t.declared_cols is not None and t.declared_cols != t.n_cols:
            w.append(
                f"{prefix} declared colCnt={t.declared_cols} "
                f"!= computed cols={t.n_cols}"
            )

        # Recurse into nested tables.
        if recurse:
            for cell in t.cells:
                for child in cell.children:
                    w.extend(self._validate_table(child))
        return w

    # -- table provenance accessors -------------------------------------------
    def body_tables(self) -> List[Table]:
        """Top-level tables that live directly in the body flow (not in any
        drawing object / text box)."""
        return [t for t in self.tables if t.depth == 0 and not t.container]

    def textbox_tables(self) -> List[Table]:
        """Top-level tables that live inside a drawing object / text box."""
        return [t for t in self.tables if t.depth == 0 and t.container]

    # -- aggregate exporters ---------------------------------------------------
    def iter_tables(self):
        """All tables, including nested children, in XML preorder."""
        found = {}
        stack = list(self.tables)
        while stack:
            table = stack.pop()
            if table.index in found:
                continue
            found[table.index] = table
            stack.extend(child for cell in table.cells for child in cell.children)
        return iter(sorted(found.values(), key=lambda t: t.index))

    def selected_tables(self, table_idx=None):
        tables = list(self.iter_tables())
        if table_idx is None:
            return tables
        if not any(t.index == table_idx for t in tables):
            raise ValueError(f'No table with index {table_idx}; found {len(tables)}')
        selected = {table_idx}
        for t in tables:
            if t.host_table_index in selected:
                selected.add(t.index)
        return [t for t in tables if t.index in selected]

    def to_json(self, indent: int = 2) -> str:
        """Serialize every table (with nested cells) to JSON.

        The ``shapeTexts`` array is additive: documents without shapes emit an
        empty list, so existing consumers of ``tables`` are unaffected.
        """
        payload = {
            "sections": self.sections,
            "lxml": _HAVE_LXML,
            "tableCount": len(list(self.iter_tables())),
            "topLevelTableCount": len(self.tables),
            "tables": [t.to_dict() for t in self.tables],
            "shapeTextCount": len(self.shape_texts),
            "shapeTexts": [s.to_dict() for s in self.shape_texts],
        }
        return json.dumps(payload, ensure_ascii=False, indent=indent)

    def grid_dump_all(self) -> str:
        _preflight_render(self.iter_tables(), 'grid')
        return "\n\n".join(t.grid_dump() for t in self.iter_tables())

    def shape_text_dump(self) -> str:
        """Human-readable dump of every harvested text box block."""
        if not self.shape_texts:
            return "(no drawing-object / text-box text found)"
        lines: List[str] = []
        for i, s in enumerate(self.shape_texts):
            path = ">".join(s.container_path) if s.container_path else s.shape_type
            tbls = (
                " tables=" + ",".join("#%d" % t.index for t in s.tables)
                if s.tables
                else ""
            )
            preview = (s.text or "").replace("\n", " / ")
            if len(preview) > 60:
                preview = preview[:59] + "…"
            lines.append(
                f"[Shape #{i}] type={s.shape_type} path={path} "
                f"src={s.doc_source}{tbls}\n    text: {preview!r}"
            )
        return "\n".join(lines)


# ------------------------------------------------------------------------------
# Small utilities
# ------------------------------------------------------------------------------
def _collect_text(t_elem) -> str:
    """Collect text from an <hp:t> element.

    HWPX stores soft line breaks as child elements (e.g. <hp:lineBreak/>) inside
    a <t>; ElementTree exposes character data as .text/.tail. We concatenate the
    element's text and the tail of each child so inline breaks/markup don't drop
    surrounding characters. Plain runs return their direct text.
    """
    parts: List[str] = []
    if t_elem.text:
        parts.append(t_elem.text)
    for child in t_elem:
        # Comments/PIs have hidden payloads but may have visible tail text.
        if isinstance(child.tag, str):
            ln = _local_name(child.tag)
            # Represent explicit line/tab breaks as their whitespace equivalents.
            if ln in ("lineBreak", "lineBreaker"):
                parts.append("\n")
            elif ln == "tab":
                parts.append("\t")
            if child.text:
                parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _paragraph_wraps_block(p) -> bool:
    """True if paragraph ``p`` has a table or a drawing-object/shape somewhere in
    its subtree.

    Used to recognize a paragraph that only *hosts* a nested table or an in-cell
    text box (its text is captured separately by the child table / shape
    handlers) so we can skip the otherwise-empty wrapper paragraph instead of
    emitting a phantom blank line. Matches on local names only, consistent with
    the rest of the parser.
    """
    for tbl in _iter_descendants_local(p, "tbl"):
        return True
    for local in _SHAPE_LOCALS:
        for _ in _iter_descendants_local(p, local):
            return True
    return False


def _to_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return None


def _pick_first(names: List[str], candidates: List[str]) -> Optional[str]:
    nameset = set(names)
    for c in candidates:
        if c in nameset:
            return c
    # case-insensitive / suffix fallback
    lowered = {n.lower(): n for n in names}
    for c in candidates:
        if c.lower() in lowered:
            return lowered[c.lower()]
    return None


def _sorted_sections(names: List[str]) -> List[str]:
    """Return Contents/section*.xml entries sorted by their numeric suffix.

    Accepts both ``Contents/section0.xml`` and a bare ``section0.xml`` layout.
    """
    sections = []
    for n in names:
        if re.fullmatch(r'(?:Contents/)?section[0-9]+\.xml', n, re.IGNORECASE):
            # Only accept Contents/ or root-level placement.
            sections.append(n)

    def keyfn(name: str):
        base = name.rsplit("/", 1)[-1].lower()
        digits = base[len("section"):-len(".xml")]
        try:
            return (0, int(digits))
        except ValueError:
            return (1, base)  # non-numeric suffixes sort after, stable by name

    return sorted(sections, key=keyfn)


# ------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hwpx_table_parser",
        description=(
            "Extract table structure from an HWPX file. "
            "Outputs grid dumps, Markdown, JSON, or a validation report."
        ),
    )
    p.add_argument("hwpx", help="path to the .hwpx file (Korean paths OK)")
    p.add_argument(
        "--format",
        "-f",
        choices=["grid", "markdown", "json", "matrix", "validate", "shapes"],
        default="grid",
        help=(
            "output format (default: grid). 'shapes' dumps drawing-object / "
            "text-box text blocks."
        ),
    )
    p.add_argument(
        "--table",
        "-t",
        type=int,
        default=None,
        help="emit this table index and descendants (0-based); not supported with shapes",
    )
    p.add_argument(
        "--out",
        "-o",
        default=None,
        help="write output to this file (UTF-8) instead of stdout",
    )
    p.add_argument('--strict', action='store_true', help='fail with exit 1 on selected structural warnings')
    return p


def _render(parser: HwpxTableParser, fmt: str, table_idx: Optional[int]) -> str:
    tables = parser.selected_tables(table_idx)
    _preflight_render(tables, fmt)
    if fmt == 'shapes' and table_idx is not None:
        raise ValueError('Table filter -t is not supported for shapes output')


    if fmt == "shapes":
        return parser.shape_text_dump()

    if fmt == "validate":
        warns = parser.validate(table_idx)
        if not warns:
            return "OK: no structural warnings."
        return "\n".join(warns)

    if fmt == "json":
        if table_idx is not None:
            return json.dumps(
                {"tableCount": len(tables), "tables": [t.to_dict() for t in tables],
                 "shapeTexts": [s.to_dict() for s in parser.shape_texts
                                if s.host_table_index in {t.index for t in tables}]},
                ensure_ascii=False,
                indent=2,
            )
        return parser.to_json()

    chunks: List[str] = ['NOTE: flat projection; merged layout/styles may be lost, grid text previews may truncate. '
                        'Use JSON for full structure, collisions and separate shapeTexts.']

    for t in tables:
        if fmt == "grid":
            chunks.append(t.grid_dump())
        elif fmt == "markdown":
            chunks.append(f"## Table #{t.index}\n\n{t.to_markdown()}")
        elif fmt == "matrix":
            mat = t.to_matrix()
            chunks.append(
                f"# Table #{t.index} ({t.n_rows}x{t.n_cols})\n"
                + "\n".join("\t".join(row) for row in mat)
            )
    return "\n\n".join(chunks)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        if args.out:
            require_distinct_paths(args.hwpx, args.out)
        parser = HwpxTableParser.load(args.hwpx)
    except DocumentSafetyError as exc:
        sys.stderr.write(f"[hwpx] ERROR: {exc}\n")
        return 2
    except FileNotFoundError:
        sys.stderr.write(f"[hwpx] ERROR: file not found: {args.hwpx}\n")
        return 2
    except zipfile.BadZipFile:
        sys.stderr.write(
            f"[hwpx] ERROR: not a valid HWPX (zip) file: {args.hwpx}\n"
            "        Legacy binary .hwp is not supported.\n"
        )
        return 2
    except (OSError, ValueError) as exc:
        sys.stderr.write(f'[hwpx] ERROR: cannot read input: {exc}\n')
        return 2

    try:
        text = _render(parser, args.format, args.table)
        if args.strict:
            warnings = parser.validate(args.table)
            if warnings:
                sys.stderr.write('[hwpx] ERROR: strict validation failed:\n' + '\n'.join(warnings) + '\n')
                return 1
    except ValueError as exc:
        sys.stderr.write(f'[hwpx] ERROR: {exc}\n')
        return 2

    if args.out:
        try:
            with atomic_output_path(args.out) as staged:
                staged.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
        except (OSError, ValueError) as exc:
            sys.stderr.write(f'[hwpx] ERROR: cannot write output: {exc}\n')
            return 1
        sys.stderr.write(f"[hwpx] wrote {args.format} output to {args.out}\n")
    else:
        # Force UTF-8 on stdout so Korean text isn't mangled by the console's
        # default code page (e.g. cp949 on Korean Windows).
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # py3.7+
        except Exception:
            pass
        try:
            print(text)
            sys.stdout.flush()
        except (OSError, ValueError) as exc:
            sys.stderr.write(f'[hwpx] ERROR: cannot write output: {exc}\n')
            # Prevent a second buffered flush at interpreter shutdown (exit 120).
            try:
                descriptor = sys.stdout.fileno()
                if isinstance(descriptor, int):
                    with open(os.devnull, 'w') as sink:
                        os.dup2(sink.fileno(), descriptor)
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
