#!/usr/bin/env python3
"""Structure-aware HWPX text extraction (XML only; not a renderer).

All modes use the same traversal regardless of optional python-hwpx installs.
Unknown objects with textual descendants are traversed in document order;
field/page-number evaluation and visual ordering of floating objects are not supported.
"""
import argparse
import html as html_mod
import json
import posixpath
import re
import sys
from pathlib import Path
from zipfile import ZipFile
from document_safety import (validate_archive, read_checked_member, parse_xml,
                             atomic_output_path, require_distinct_paths)

TextExtractor = None  # retained for callers; XML path has deterministic semantics
PARAGRAPH_NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}
HP = '{' + PARAGRAPH_NS['hp'] + '}'


def _section_entries(hwpx_path):
    """Honor a complete OPF spine, otherwise use numeric section suffixes."""
    with ZipFile(hwpx_path) as archive:
        validate_archive(archive)
        names = sorted((n for n in archive.namelist()
                        if re.fullmatch(r'Contents/section[0-9]+\.xml', n)),
                       key=lambda n: int(re.search(r'section([0-9]+)', n).group(1)))
        if not names:
            raise ValueError('No supported Contents/sectionN.xml sections')
        if 'Contents/content.hpf' in archive.namelist():
            root = parse_xml(read_checked_member(archive, 'Contents/content.hpf'))
            uri = root.tag.split('}')[0].lstrip('{')
            if uri not in ('http://www.idpf.org/2007/opf', 'http://www.idpf.org/2007/opf/'):
                raise ValueError('Unsupported content.hpf namespace')
            opf = {'o': uri}
            def member(href):
                # Hancom packages use root-relative Contents/...; other OPF
                # producers use paths relative to the package directory.
                direct = posixpath.normpath(href)
                return direct if direct in archive.namelist() else posixpath.normpath(posixpath.join('Contents', href))
            manifest = {e.get('id'): member(e.get('href', ''))
                        for e in root.findall('o:manifest/o:item', opf)}
            refs = root.findall('o:spine/o:itemref', opf)
            if any(e.get('idref') not in manifest or manifest[e.get('idref')] not in archive.namelist() for e in refs):
                raise ValueError('Unresolved content.hpf spine reference')
            ordered = [manifest[e.get('idref')] for e in refs if manifest[e.get('idref')] in names]
            if refs:
                if len(set(ordered)) != len(ordered) or set(ordered) != set(names):
                    raise ValueError('Incomplete or duplicate section spine')
                return ordered
        return names


def _span(value, default=1):
    if value is None:
        return default
    if not re.fullmatch(r'[0-9]+', value) or not 1 <= int(value) <= 10000:
        raise ValueError('Invalid table span: ' + repr(value))
    return int(value)


def _inline_text(node, inside=False):
    """Own hp:t text and controls; ignore XML indentation outside text nodes."""
    if not isinstance(node.tag, str):
        return ''  # comments/PIs have no visible text; caller keeps their tails
    if node.tag in {HP+'p', HP+'tbl', HP+'ctrl', HP+'secPr'}:
        return ''
    if node.tag == HP+'tab':
        return '\t'
    if node.tag in {HP+'lineBreak', HP+'br'}:
        return '\n'
    inside = inside or node.tag == HP+'t'
    result = (node.text or '') if inside else ''
    for child in node:
        result += _inline_text(child, inside)
        if inside:
            result += child.tail or ''
    return result


def _get_para_text(para):
    return ''.join(_inline_text(c) for c in para)


def _content(node, heading_levels=None):
    """Emit text and objects once by identity, including mixed runs and notes."""
    result = []
    buffer = []
    paragraph_stack = []
    def flush():
        if buffer:
            text = ''.join(buffer)
            if text:
                item = {'type': 'text', 'content': text}
                if paragraph_stack and heading_levels:
                    level = heading_levels.get(paragraph_stack[-1].get('paraPrIDRef'))
                    if level is not None:
                        item['headingLevel'] = level
                result.append(item)
            buffer.clear()
    def walk(el, inside_text=False):
        if not isinstance(el.tag, str):
            return  # retain visible tail in the parent, not comment/PI payload
        if el.tag == HP+'tbl':
            flush()
            result.append(_build_table_json(el, heading_levels))
            return
        if el.tag == HP+'p':
            flush()
            paragraph_stack.append(el)
            for child in el:
                walk(child)
            flush()
            paragraph_stack.pop()
            return
        inside = inside_text or el.tag == HP+'t'
        if inside and el.text:
            buffer.append(el.text)
        if el.tag == HP+'tab':
            buffer.append('\t')
        elif el.tag in {HP+'lineBreak', HP+'br'}:
            buffer.append('\n')
        for child in el:
            walk(child, inside)
            if inside and child.tail:
                buffer.append(child.tail)
    walk(node)
    flush()
    return result


def _build_table_json(tbl, heading_levels=None):
    rows, cells = [], []
    for ri, tr in enumerate(tbl.findall('hp:tr', PARAGRAPH_NS)):
        row, cellrow = [], []
        for ci, tc in enumerate(tr.findall('hp:tc', PARAGRAPH_NS)):
            span = tc.find('hp:cellSpan', PARAGRAPH_NS)
            addr = tc.find('hp:cellAddr', PARAGRAPH_NS)
            content = _content(tc, heading_levels)
            row.append('\n'.join(_plain_items(content, True)))
            cellrow.append({'row': int(addr.get('rowAddr', ri)) if addr is not None else ri,
                            'col': int(addr.get('colAddr', ci)) if addr is not None else ci,
                            'rowSpan': _span(span.get('rowSpan')) if span is not None else 1,
                            'colSpan': _span(span.get('colSpan')) if span is not None else 1,
                            'header': tc.get('header') == '1', 'content': content})
        rows.append(row)
        cells.append(cellrow)
    # Preserve legacy rows for flat consumers; cells is the authoritative tree.
    return {'type': 'table', 'rows': rows, 'cells': cells}


def _sections(path):
    with ZipFile(path) as archive:
        validate_archive(archive)
        headings = {}
        if 'Contents/header.xml' in archive.namelist():
            header = parse_xml(read_checked_member(archive, 'Contents/header.xml'))
            hh = {'hh': 'http://www.hancom.co.kr/hwpml/2011/head'}
            for prop in header.findall('.//hh:paraPr', hh):
                heading = prop.find('hh:heading', hh)
                if heading is not None and heading.get('type') == 'OUTLINE':
                    level = heading.get('level','0')
                    if re.fullmatch(r'[0-5]',level):
                        headings[prop.get('id')] = int(level)+1
        for entry in _section_entries(path):
            yield {'section': Path(entry).stem,
                   'paragraphs': _content(parse_xml(read_checked_member(archive, entry)), headings)}


def _plain_items(items, tables):
    for item in items:
        if item['type'] == 'text':
            yield item['content']
        elif tables:
            for row in item['cells']:
                for cell in row:
                    yield from _plain_items(cell['content'], tables)


def extract_plain(hwpx_path, *, include_tables=False):
    return '\n'.join(text for sec in _sections(hwpx_path)
                     for text in _plain_items(sec['paragraphs'], include_tables) if text)


def _extract_xml_plain(hwpx_path):
    return extract_plain(hwpx_path)


def extract_markdown(hwpx_path):
    return '\n\n---\n\n'.join('\n'.join(_plain_items(sec['paragraphs'], True))
                              for sec in _sections(hwpx_path))


_extract_xml_markdown = extract_markdown


def _items_html(items):
    parts = []
    for item in items:
        if item['type'] == 'text':
            tag = 'h'+str(item['headingLevel']) if 'headingLevel' in item else 'p'
            parts.append(f'<{tag}>' + html_mod.escape(item['content']).replace('\n', '<br>') + f'</{tag}>')
        else:
            rows = []
            for row in item['cells']:
                cells = []
                for cell in row:
                    tag = 'th' if cell['header'] else 'td'
                    attrs = ' scope="col"' if cell['header'] else ''
                    for key in ('colSpan', 'rowSpan'):
                        if cell[key] != 1:
                            attrs += f' {key.lower()}="{cell[key]}"'
                    cells.append(f'<{tag}{attrs}>' + _items_html(cell['content']) + f'</{tag}>')
                rows.append('<tr>' + ''.join(cells) + '</tr>')
            parts.append('<table border="1">' + ''.join(rows) + '</table>')
    return '\n'.join(parts)


def _build_table_html(tbl):
    return _items_html([_build_table_json(tbl)])


def extract_html(hwpx_path):
    body = '\n'.join(_items_html(sec['paragraphs']) for sec in _sections(hwpx_path))
    return '<!DOCTYPE html>\n<html lang="ko"><head><meta charset="utf-8"><title>HWPX Extract</title></head><body>\n' + body + '\n</body></html>'


_extract_xml_html = extract_html


def extract_json(hwpx_path):
    return json.dumps({'sections': list(_sections(hwpx_path))}, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract text from an HWPX document"
    )
    parser.add_argument("input", help="Path to .hwpx file")
    parser.add_argument(
        "--format", "-f",
        choices=["plain", "markdown", "html", "json"],
        default="plain",
        help="Output format (default: plain)",
    )
    parser.add_argument(
        "--include-tables",
        action="store_true",
        help="Include text from tables and nested objects (plain mode)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    if not Path(args.input).is_file():
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        result = extract_json(args.input)
    elif args.format == "markdown":
        result = extract_markdown(args.input)
    elif args.format == "html":
        result = extract_html(args.input)
    else:
        result = extract_plain(args.input, include_tables=args.include_tables)

    if args.output:
        require_distinct_paths(args.input, args.output)
        with atomic_output_path(args.output) as staged:
            staged.write_text(result, encoding="utf-8")
        print(f"Extracted to: {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
