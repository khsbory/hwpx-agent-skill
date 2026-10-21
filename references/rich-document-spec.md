# Rich Document Creation - JSON Input Specification

## Overview

`create_rich_document.py` generates richly formatted HWPX documents from a JSON specification using an XML-first approach. It copies a base template, injects style definitions into `header.xml`, builds `section0.xml` content, and packages the result via `build_hwpx.pack_hwpx()`.

## JSON Structure

The top-level JSON object has five optional sections:

```
{
  "page": { ... },
  "header_text": "...",
  "footer_text": "...",
  "styles": { ... },
  "content": [ ... ]
}
```

---

### page (Page Settings)

Configures paper size, orientation, and margins.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `paper` | string | `"A4"` | Paper size. One of: `A4`, `B5`, `A3`, `Letter`, `Legal` |
| `orientation` | string | `"portrait"` | Page orientation. One of: `portrait`, `landscape` |
| `margins` | object | see below | Page margins in millimeters |

**margins object:**

| Field | Type | Default | Unit |
|-------|------|---------|------|
| `top` | number | `20` | mm |
| `bottom` | number | `20` | mm |
| `left` | number | `30` | mm |
| `right` | number | `30` | mm |
| `header` | number | `15` | mm |
| `footer` | number | `15` | mm |

**Paper dimensions (internal HWPUNIT):**

| Paper | Width x Height (portrait) |
|-------|---------------------------|
| A4 | 59528 x 84186 |
| B5 | 51592 x 72850 |
| A3 | 84186 x 119054 |
| Letter | 61200 x 79200 |
| Legal | 61200 x 100800 |

---

### header_text / footer_text

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `header_text` | string | `""` | Text displayed in the page header |
| `footer_text` | string | `""` | Text displayed in the page footer |

- Use `{page}` placeholder for automatic page numbering.
- Example: `"- {page} -"` renders as `- 1 -`, `- 2 -`, etc.
- The header/footer is applied to all pages (`applyPageType="BOTH"`).

---

### styles (Style Definitions)

A dictionary mapping style names to style property objects. Style names are user-defined and referenced by content items.

```json
{
  "styles": {
    "title": { ... },
    "body": { ... },
    "heading1": { ... }
  }
}
```

**Style property fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `font` | string | `""` (template default) | Font name (e.g., `"malgun gothic"`, `"Nanum Gothic"`) |
| `size` | number | `10` | Font size in points |
| `bold` | boolean | `false` | Bold text |
| `italic` | boolean | `false` | Italic text |
| `underline` | boolean | `false` | Underline text (BOTTOM, SOLID style) |
| `color` | string | `"#000000"` | Text color in `#RRGGBB` format |
| `align` | string | `"JUSTIFY"` | Horizontal alignment. One of: `LEFT`, `CENTER`, `RIGHT`, `JUSTIFY` |
| `line_spacing` | number | `160` | Line spacing as percentage (e.g., `160` = 160%) |
| `indent` | number | `0` | First-line indent in mm |
| `margin_top` | number | `0` | Spacing before paragraph in mm |
| `margin_bottom` | number | `0` | Spacing after paragraph in mm |

**Notes:**
- If `font` matches a font already registered in the base template, it reuses the existing font ID.
- If `font` is new, it is registered across all language fontface groups (HANGUL, LATIN, HANJA, JAPANESE, OTHER, SYMBOL, USER).
- `line_spacing` uses `PERCENT` type in the HWPX XML.
- `indent`, `margin_top`, `margin_bottom` are converted from mm to HWPUNIT internally (1 mm ~ 283.46 HWPUNIT).

---

### content (Content Array)

An ordered array of content items. Each item has a `type` field.

#### type: "paragraph"

A text paragraph with an optional style reference.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"paragraph"` |
| `style` | string | no | Style name defined in `styles`. Default: `"body"` |
| `text` | string | no | Paragraph text content. Default: `""` |

#### type: "table"

A table with optional headers, data rows, cell merging, and styling.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"table"` |
| `rows` | number | no | Total number of rows (including header). Default: `2` |
| `cols` | number | no | Number of columns. Default: `2` |
| `headers` | array of strings | no | Header row cell values. If provided, first row is treated as header |
| `data` | array of arrays | no | Body row data. Each inner array is one row of cell values |
| `merges` | array of objects | no | Cell merge specifications (see below) |
| `col_widths` | array of numbers | no | Percentage-based column width proportions. Example: `[30, 50, 20]` for 3 columns with 30%, 50%, 20% widths. Values are auto-normalized if they don't sum to 100. If omitted, columns are evenly distributed. |
| `style` | object | no | Table styling options (see below) |

**merges array item:**

| Field | Type | Description |
|-------|------|-------------|
| `row` | number | Starting row index (0-based) |
| `col` | number | Starting column index (0-based) |
| `rowSpan` | number | Number of rows to span. Default: `1` |
| `colSpan` | number | Number of columns to span. Default: `1` |

**style object (table styling):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `header_bg` | string | `"none"` | Header row background color (`#RRGGBB` or `"none"`) |
| `header_font_color` | string | `"#000000"` | Header row text color |
| `header_bold` | boolean | `true` | Bold text in header row |
| `body_font_color` | string | `"#000000"` | Body rows text color |
| `alt_row_bg` | string | `"none"` | Alternating (even) row background color |
| `border` | string | `"solid"` | Border style. One of: `solid`, `thick`, `double`, `none` |
| `cell_align` | string | `"CENTER"` | Cell text alignment. One of: `LEFT`, `CENTER`, `RIGHT`, `JUSTIFY` |

**Border style details:**

| Value | HWPX Type | Width |
|-------|-----------|-------|
| `solid` | `SOLID` | `0.12 mm` |
| `thick` | `SOLID` | `0.4 mm` |
| `double` | `DOUBLE` | `0.4 mm` |
| `none` | `NONE` | `0.1 mm` |

**Table behavior notes:**
- Column widths are evenly distributed by default. Use the optional `col_widths` field (array of numbers representing percentage proportions) to specify custom column widths.
- If `headers` is provided, `repeatHeader` is set to `1` (header repeats on page breaks).
- Alternating row color applies to even-indexed rows (0-based, where row 0 is typically the header).
- Table font size defaults to 10pt. Header and body use separate charPr definitions.
- Row height is fixed at a default value (1200 HWPUNIT).
- Cell text may contain newline characters (`\n`) to create multi-line content within a single cell. Each line becomes a separate paragraph element, and the row height adjusts automatically.

#### type: "page_break"

Inserts a page break.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"page_break"` |

No additional fields.

#### type: "paragraph" — Footnotes and Endnotes

Inline markers in paragraph text trigger automatic footnote/endnote insertion.

**Syntax:**
- `[^N]` — footnote marker (N = numeric key)
- `[^^N]` — endnote marker (N = numeric key)

**Paragraph with notes:**

```json
{
  "type": "paragraph",
  "style": "body",
  "text": "본문 텍스트[^1]이고 계속됩니다[^^1].",
  "footnotes": {"1": "각주 내용"},
  "endnotes": {"1": "미주 내용"}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `footnotes` | object (string→string) | no | Footnote content keyed by marker number |
| `endnotes` | object (string→string) | no | Endnote content keyed by marker number |

**Behavior:**
- Footnotes appear at the bottom of each page
- Endnotes appear at the end of the document
- Numbers are auto-incremented across the document (1, 2, 3, ...)
- Footnote and endnote numbering are independent

---

## Limitations

- **No image insertion** -- images cannot be embedded via this script.
- **No table of contents** -- automatic table of contents generation is not supported.
- **Font size in tables** -- table cells use the default 10pt size; per-cell font size is not configurable.

---

## Examples

### Minimal Document

```json
{
  "page": {
    "paper": "A4",
    "orientation": "portrait"
  },
  "styles": {
    "body": {
      "font": "malgun gothic",
      "size": 11,
      "align": "JUSTIFY",
      "line_spacing": 160
    }
  },
  "content": [
    {"type": "paragraph", "style": "body", "text": "Hello, World!"}
  ]
}
```

### Full-Featured Document

```json
{
  "page": {
    "paper": "A4",
    "orientation": "portrait",
    "margins": {"top": 20, "bottom": 20, "left": 25, "right": 25, "header": 15, "footer": 15}
  },
  "header_text": "Monthly Report",
  "footer_text": "- {page} -",
  "styles": {
    "title": {
      "font": "malgun gothic",
      "size": 22,
      "bold": true,
      "align": "CENTER",
      "color": "#1A3C6E",
      "margin_bottom": 10
    },
    "subtitle": {
      "font": "malgun gothic",
      "size": 14,
      "italic": true,
      "align": "CENTER",
      "color": "#666666",
      "margin_bottom": 15
    },
    "heading": {
      "font": "malgun gothic",
      "size": 14,
      "bold": true,
      "color": "#2E5090",
      "margin_top": 10,
      "margin_bottom": 5
    },
    "body": {
      "font": "malgun gothic",
      "size": 11,
      "align": "JUSTIFY",
      "line_spacing": 170,
      "indent": 5
    }
  },
  "content": [
    {"type": "paragraph", "style": "title", "text": "2026 Q1 Report"},
    {"type": "paragraph", "style": "subtitle", "text": "Finance Division"},
    {"type": "paragraph", "style": "heading", "text": "1. Revenue Summary"},
    {"type": "paragraph", "style": "body", "text": "Revenue increased by 15% compared to the previous quarter."},
    {
      "type": "table",
      "rows": 4,
      "cols": 4,
      "headers": ["Category", "January", "February", "March"],
      "data": [
        ["Product A", "1,200", "1,350", "1,500"],
        ["Product B", "800", "850", "900"],
        ["Total", "2,000", "2,200", "2,400"]
      ],
      "merges": [],
      "style": {
        "header_bg": "#2E5090",
        "header_font_color": "#FFFFFF",
        "header_bold": true,
        "body_font_color": "#333333",
        "alt_row_bg": "#F0F4FA",
        "border": "solid",
        "cell_align": "CENTER"
      }
    },
    {"type": "page_break"},
    {"type": "paragraph", "style": "heading", "text": "2. Next Quarter Outlook"},
    {"type": "paragraph", "style": "body", "text": "We expect continued growth driven by new product launches."}
  ]
}
```

### Table with Cell Merges

```json
{
  "type": "table",
  "rows": 4,
  "cols": 3,
  "headers": ["Region", "H1", "H2"],
  "data": [
    ["North", "100", "120"],
    ["South", "80", "90"],
    ["Total", "", ""]
  ],
  "merges": [
    {"row": 3, "col": 1, "rowSpan": 1, "colSpan": 2}
  ],
  "style": {
    "header_bg": "#4472C4",
    "header_font_color": "#FFFFFF",
    "border": "thick"
  }
}
```

---

## Usage

```bash
# From a JSON file
python create_rich_document.py --input content.json --output result.hwpx

# From stdin
cat content.json | python create_rich_document.py --output result.hwpx
```

**CLI arguments:**

| Argument | Short | Required | Description |
|----------|-------|----------|-------------|
| `--input` | `-i` | no | Path to JSON input file. If omitted, reads from stdin |
| `--output` | `-o` | yes | Output `.hwpx` file path |
