#!/usr/bin/env python3
"""Validate the structural integrity of an HWPX file.

Checks:
  - Valid ZIP archive
  - Bounded ZIP paths, entries, expanded sizes and compression ratios
  - Required package files, expected roots/namespaces and reference targets
  - mimetype content is correct
  - mimetype is the first ZIP entry and stored without compression
  - CRC integrity for every member; XML without DTD/entities and within budgets
  - Basic style references, section count and bounded table geometry

These are intentionally basic checks, not complete XSD or rendered validation.

Usage:
    python validate.py document.hwpx
"""

import argparse
import sys
from pathlib import Path
from zipfile import ZIP_STORED, BadZipFile, ZipFile

import re
from urllib.parse import urlsplit, unquote

from document_safety import DocumentSafetyError, validate_archive, read_checked_member, parse_xml

HH = 'http://www.hancom.co.kr/hwpml/2011/head'
HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'
HS = 'http://www.hancom.co.kr/hwpml/2011/section'
OPF = 'http://www.idpf.org/2007/opf/'
OCF = 'urn:oasis:names:tc:opendocument:xmlns:container'
ODF = 'urn:oasis:names:tc:opendocument:xmlns:manifest:1.0'
MAX_TABLE_SLOTS = 1000000


def _reference(href, names):
    """HWPX package hrefs are archive-root paths, not remote URLs."""
    parts = urlsplit(href)
    decoded = unquote(parts.path)
    if (not decoded or parts.scheme or parts.netloc or parts.query or parts.fragment
            or '\\' in decoded or decoded.startswith('/') or ':' in decoded
            or any(p in ('', '.', '..') for p in decoded.split('/'))):
        raise DocumentSafetyError(f'Unsafe package reference: {href!r}')
    if decoded not in names:
        raise DocumentSafetyError(f'Missing package reference: {href}')
    return decoded


def _positive(element, name, minimum=1):
    value = element.get(name, '')
    if not re.fullmatch(r'[0-9]+', value):
        raise DocumentSafetyError(f'Invalid {name}: {value!r}')
    result = int(value)
    if result < minimum or result > MAX_TABLE_SLOTS:
        raise DocumentSafetyError(f'Out of range {name}: {value}')
    return result


def _check_tables(root):
    budget = 0
    for table in root.iter(f'{{{HP}}}tbl'):
        # Some bundled/legacy XML omits declarations. Bound and check the
        # actual addressed cells, without pretending this is XSD validation.
        cells = table.findall(f'{{{HP}}}tr/{{{HP}}}tc')
        ext_rows = ext_cols = 0
        for cell in cells:
            addr = cell.find(f'{{{HP}}}cellAddr')
            span = cell.find(f'{{{HP}}}cellSpan')
            if addr is None or span is None:
                raise DocumentSafetyError('Table cell lacks address/span')
            ext_rows = max(ext_rows, _positive(addr, 'rowAddr', 0) + _positive(span, 'rowSpan'))
            ext_cols = max(ext_cols, _positive(addr, 'colAddr', 0) + _positive(span, 'colSpan'))
        rows = _positive(table, 'rowCnt') if table.get('rowCnt') is not None else ext_rows
        cols = _positive(table, 'colCnt') if table.get('colCnt') is not None else ext_cols
        if rows < 1 or cols < 1:
            raise DocumentSafetyError('Empty or unbounded table geometry')
        budget += rows * cols
        if budget > MAX_TABLE_SLOTS:
            raise DocumentSafetyError('Table slot budget exceeded')
        occupied = set()
        for row in table.findall(f'{{{HP}}}tr'):
            for cell in row.findall(f'{{{HP}}}tc'):
                addr = cell.find(f'{{{HP}}}cellAddr')
                span = cell.find(f'{{{HP}}}cellSpan')
                if addr is None or span is None:
                    raise DocumentSafetyError('Table cell lacks address/span')
                r, c = _positive(addr, 'rowAddr', 0), _positive(addr, 'colAddr', 0)
                rs, cs = _positive(span, 'rowSpan'), _positive(span, 'colSpan')
                if r + rs > rows or c + cs > cols:
                    raise DocumentSafetyError('Table cell span exceeds declared geometry')
                for rr in range(r, r + rs):
                    for cc in range(c, c + cs):
                        if (rr, cc) in occupied:
                            raise DocumentSafetyError('Overlapping table cells')
                        occupied.add((rr, cc))
                size = cell.find(f'{{{HP}}}cellSz')
                if size is not None:
                    for dimension in ('width', 'height'):
                        if size.get(dimension) is not None:
                            _positive(size, dimension, 0)
        if len(occupied) != rows * cols:
            raise DocumentSafetyError('Table cells do not cover declared geometry')


def _check_structure(roots, names):
    expected = {'Contents/content.hpf': f'{{{OPF}}}package',
                'Contents/header.xml': f'{{{HH}}}head',
                'META-INF/container.xml': f'{{{OCF}}}container',
                'META-INF/manifest.xml': f'{{{ODF}}}manifest'}
    sections = [n for n in names if re.fullmatch(r'Contents/section[0-9]+\.xml', n)]
    expected.update({n: f'{{{HS}}}sec' for n in sections})
    for name, tag in expected.items():
        if name not in roots or roots[name].tag != tag:
            raise DocumentSafetyError(f'Wrong or missing root/namespace: {name}')
    container = roots['META-INF/container.xml']
    paths = [_reference(n.get('full-path', ''), names)
             for n in container.iter(f'{{{OCF}}}rootfile')]
    if 'Contents/content.hpf' not in paths:
        raise DocumentSafetyError('Container does not reference Contents/content.hpf')
    package = roots['Contents/content.hpf']
    manifest = {}
    for item in package.findall(f'{{{OPF}}}manifest/{{{OPF}}}item'):
        ident = item.get('id')
        if not ident or ident in manifest:
            raise DocumentSafetyError('Missing/duplicate manifest id')
        manifest[ident] = _reference(item.get('href', ''), names)
    targets = set(manifest.values())
    if not {'Contents/header.xml', *sections}.issubset(targets):
        raise DocumentSafetyError('Manifest omits header/section')
    spine = []
    for item in package.findall(f'{{{OPF}}}spine/{{{OPF}}}itemref'):
        if item.get('idref') not in manifest:
            raise DocumentSafetyError('Spine references unknown manifest id')
        spine.append(manifest[item.get('idref')])
    if len(spine) != len(set(spine)) or not set(sections).issubset(spine):
        raise DocumentSafetyError('Spine missing/duplicate sections')
    header = roots['Contents/header.xml']
    if _positive(header, 'secCnt') != len(sections):
        raise DocumentSafetyError('Header section count disagrees with package')
    categories = ['charPr', 'paraPr', 'style', 'borderFill', 'tabPr', 'numbering']
    ids = {}
    for category in categories:
        values = [n.get('id') for n in header.iter(f'{{{HH}}}{category}')]
        if None in values or len(values) != len(set(values)):
            raise DocumentSafetyError(f'Missing/duplicate {category} id')
        ids[category] = set(values)
    refs = {'charPrIDRef': 'charPr', 'paraPrIDRef': 'paraPr',
            'styleIDRef': 'style', 'nextStyleIDRef': 'style',
            'borderFillIDRef': 'borderFill', 'tabPrIDRef': 'tabPr'}
    for root in [header, *(roots[n] for n in sections)]:
        for node in root.iter():
            binary_ref = node.get('binaryItemIDRef')
            if binary_ref is not None and binary_ref not in manifest:
                raise DocumentSafetyError(f'Unknown binaryItemIDRef: {binary_ref}')
            for attr, category in refs.items():
                value = node.get(attr)
                # Hancom's paraHead uses UINT32_MAX for inherited character style.
                if (node.tag == f'{{{HH}}}paraHead' and attr == 'charPrIDRef'
                        and value == '4294967295'):
                    continue
                # Zero border fill means none in existing Hancom documents.
                if value is not None and not (category == 'borderFill' and value == '0'):
                    if value not in ids[category]:
                        raise DocumentSafetyError(f'Unknown {attr}: {value}')
        _check_tables(root)

REQUIRED_FILES = [
    "mimetype",
    "META-INF/container.xml",
    "META-INF/manifest.xml",
    "Contents/content.hpf",
    "Contents/header.xml",
    "Contents/section0.xml",
]

EXPECTED_MIMETYPE = "application/hwp+zip"


def validate(hwpx_path: str) -> list[str]:
    """Bounded basic package checks, not XSD or Hancom rendering certification."""

    errors: list[str] = []
    path = Path(hwpx_path)

    if not path.is_file():
        return [f"File not found: {hwpx_path}"]

    try:
        with ZipFile(hwpx_path, 'r') as zf:
            validate_archive(zf)
            names = zf.namelist()
            for required in REQUIRED_FILES:
                if required not in names:
                    errors.append(f'Missing required file: {required}')
            if 'mimetype' in names:
                if read_checked_member(zf, 'mimetype').decode('utf-8').strip() != EXPECTED_MIMETYPE:
                    errors.append('Invalid mimetype content')
                if names[0] != 'mimetype' or zf.getinfo('mimetype').compress_type != ZIP_STORED:
                    errors.append('mimetype must be first and ZIP_STORED')
            roots = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # All entries, including binary parts, are read for CRC integrity.
                data = read_checked_member(zf, info.filename)
                if info.filename.lower().endswith(('.xml', '.hpf')):
                    roots[info.filename] = parse_xml(data)
            if not errors:
                _check_structure(roots, names)
    except (BadZipFile, OSError, ValueError, RuntimeError, EOFError, NotImplementedError) as exc:
        errors.append(f'Invalid or unsafe HWPX: {exc}')
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the structural integrity of an HWPX file"
    )
    parser.add_argument("input", help="Path to .hwpx file")
    args = parser.parse_args()

    errors = validate(args.input)

    if errors:
        print(f"INVALID: {args.input}", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"VALID: {args.input}")
        print('  Basic package/reference/style/table checks passed; not XSD or rendering validation.')


if __name__ == "__main__":
    main()
