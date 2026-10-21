#!/usr/bin/env python3
"""Create an HWPX document from Markdown or JSON input.

DEPRECATED: Use create_rich_document.py instead. This compatibility adapter
routes legacy blocks through the validated XML-first generator, without
python-hwpx. Markdown is limited to simple lines and pipe-delimited tables;
legacy headings use visual styles, not semantic heading navigation.

Supports:
  - Paragraphs (plain text lines)
  - Tables (Markdown pipe tables or JSON array-of-arrays)
  - Headers/footers (via JSON metadata)

Usage:
    python create_document.py --input content.md --output result.hwpx
    python create_document.py --input content.json --output result.hwpx
    echo "Hello World" | python create_document.py --output result.hwpx
"""

import argparse
import json
import re
import sys
from pathlib import Path

from create_rich_document import create_rich_document
from document_safety import require_distinct_paths


def parse_markdown(text: str) -> list[dict]:
    """Parse Markdown text into a list of content blocks.

    Returns a list of dicts, each with:
      - {"type": "paragraph", "text": "..."}
      - {"type": "table", "rows": [["cell", ...], ...]}
      - {"type": "heading", "level": 1-6, "text": "..."}
    """
    blocks: list[dict] = []
    lines = text.split("\n")
    table_buffer: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Heading
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            if table_buffer:
                blocks.append(_parse_md_table(table_buffer))
                table_buffer = []
            level = len(heading_match.group(1))
            blocks.append({
                "type": "heading",
                "level": level,
                "text": heading_match.group(2).strip(),
            })
            i += 1
            continue

        # Table row (pipe-delimited)
        if "|" in line and line.strip().startswith("|"):
            table_buffer.append(line)
            i += 1
            continue

        # End of table
        if table_buffer:
            blocks.append(_parse_md_table(table_buffer))
            table_buffer = []

        # Non-empty line -> paragraph
        stripped = line.strip()
        if stripped:
            blocks.append({"type": "paragraph", "text": stripped})

        i += 1

    if table_buffer:
        blocks.append(_parse_md_table(table_buffer))

    return blocks


def _parse_md_table(lines: list[str]) -> dict:
    """Parse Markdown pipe table lines into a table block."""
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip separator rows (e.g., |---|---|---|)
        if re.match(r"^\|[\s\-:|]+$", line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # Remove empty first/last from leading/trailing pipes
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if cells:
            rows.append(cells)
    return {"type": "table", "rows": rows}


def parse_json_input(text: str) -> list[dict]:
    """Parse JSON input into content blocks.

    Expected format:
    {
      "header": "optional header text",
      "footer": "optional footer text",
      "content": [
        {"type": "paragraph", "text": "..."},
        {"type": "heading", "level": 1, "text": "..."},
        {"type": "table", "rows": [["a", "b"], ["c", "d"]]}
      ]
    }
    """
    data = json.loads(text)
    if not isinstance(data,dict) or set(data)-{"header","footer","content"}:
        raise ValueError("Unsupported legacy JSON object/keys")
    if not isinstance(data.get("content",[]),list):
        raise ValueError("content must be an array")
    blocks: list[dict] = []

    if "header" in data:
        blocks.append({"type": "header", "text": data["header"]})
    if "footer" in data:
        blocks.append({"type": "footer", "text": data["footer"]})

    content = data.get("content", [])
    if isinstance(content, list):
        blocks.extend(content)

    return blocks


def create_document(blocks: list[dict], output_path: str) -> None:
    """Create an HWPX document from parsed content blocks."""

    print('DEPRECATED: use create_rich_document.py with rich JSON. '
          'Legacy headings are visual styles only, not semantic heading navigation; '
          'Markdown support is limited to simple line/pipe syntax.', file=sys.stderr)
    spec = {'styles': {}, 'content': []}
    if not isinstance(blocks, list):
        raise ValueError('Legacy blocks must be an array')
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError('Legacy block must be an object')
        kind = block.get('type', 'paragraph')
        allowed = {'paragraph': {'type','text'}, 'heading': {'type','text','level'},
                   'table': {'type','rows'}, 'header': {'type','text'}, 'footer': {'type','text'}}
        if kind not in allowed or set(block)-allowed[kind]:
            raise ValueError('Unsupported legacy block/keys: ' + repr(kind))
        if kind in ('header','footer'):
            key = kind + '_text'
            if key in spec:
                raise ValueError('Duplicate legacy ' + kind)
            spec[key] = block.get('text','')
        elif kind == 'heading':
            level = block.get('level',1)
            if type(level) is not int or not 1 <= level <= 6:
                raise ValueError('Heading level must be 1..6')
            name = 'legacy_heading_' + str(level)
            spec['styles'][name] = {'bold': True, 'size': max(11,22-level*2)}
            spec['content'].append({'type':'paragraph','text':block.get('text',''),'style':name})
        elif kind == 'table':
            rows = block.get('rows',[])
            if not isinstance(rows,list) or not rows or any(not isinstance(row,list) for row in rows):
                raise ValueError('Legacy table requires non-empty row arrays')
            spec['content'].append({'type':'table','rows':len(rows),
                                    'cols':max(map(len,rows)), 'data':rows})
        else:
            spec['content'].append(block)
    create_rich_document(spec, Path(output_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create HWPX document from Markdown or JSON input"
    )
    parser.add_argument(
        "--input", "-i",
        help="Input file path (.md or .json). Reads from stdin if omitted.",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output .hwpx file path",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["md", "json", "auto"],
        default="auto",
        help="Input format (default: auto-detect from extension)",
    )
    args = parser.parse_args()

    # Read input
    if args.input:
        require_distinct_paths(args.input, args.output)
        input_path = Path(args.input)
        if not input_path.is_file():
            print(f"Error: File not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        text = input_path.read_text(encoding="utf-8")
        fmt = args.format
        if fmt == "auto":
            fmt = "json" if input_path.suffix.lower() == ".json" else "md"
    else:
        text = sys.stdin.read()
        fmt = args.format
        if fmt == "auto":
            # Try JSON first
            fmt = "json" if text.strip().startswith("{") else "md"

    # Parse
    if fmt == "json":
        blocks = parse_json_input(text)
    else:
        blocks = parse_markdown(text)

    if not blocks:
        print("Warning: No content blocks parsed from input.", file=sys.stderr)

    create_document(blocks, args.output)


if __name__ == "__main__":
    main()
