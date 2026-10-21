#!/usr/bin/env python3
"""
analyze_template.py — HWPX 문서 구조 심층 분석

HWPX 파일을 분석하여 문서의 전체 구조, 스타일 정의, 테이블 레이아웃,
셀 병합, 내용 등을 상세하게 출력한다.
레퍼런스 기반 문서 생성의 청사진으로 사용.

Usage:
    python3 analyze_template.py <input.hwpx>
    python3 analyze_template.py <input.hwpx> --extract-header /tmp/ref_header.xml
"""

import sys
import os
import json
import tempfile
import shutil
import zipfile
import argparse
from lxml import etree
from pathlib import Path
from document_safety import (validate_archive, read_checked_member, parse_xml,
                             atomic_output_path, require_distinct_paths)
from text_extract import _section_entries, _get_para_text

NS = {
    'hp': 'http://www.hancom.co.kr/hwpml/2011/paragraph',
    'hs': 'http://www.hancom.co.kr/hwpml/2011/section',
    'hc': 'http://www.hancom.co.kr/hwpml/2011/core',
    'hh': 'http://www.hancom.co.kr/hwpml/2011/head',
}

FONT_MAP = {}


def get_text(el):
    return _get_para_text(el)


def analyze_fonts(root):
    lines = ["▶ Font definitions"]
    for fontface in root.findall('.//hh:fontface', NS):
        lang = fontface.get('lang', '?')
        for font in fontface.findall('hh:font', NS):
            fid = font.get('id')
            face = font.get('face')
            FONT_MAP[(lang, fid)] = face
            if lang == 'HANGUL':
                lines.append(f"  hangul/{fid}: {face}")
    lines.append("")
    return lines


def analyze_borderfills(root):
    lines = ["▶ borderFill (borders/backgrounds)"]
    for bf in root.findall('.//hh:borderFill', NS):
        bid = bf.get('id')
        parts = []
        for side in ['left', 'right', 'top', 'bottom']:
            b = bf.find(f'hh:{side}Border', NS)
            if b is not None:
                btype = b.get('type', 'NONE')
                bwidth = b.get('width', '')
                if btype != 'NONE':
                    parts.append(f"{side}={btype} {bwidth}".strip())
                else:
                    parts.append(f"{side}=NONE")

        bg = "none"
        fill = bf.find('.//hc:winBrush', NS)
        if fill is not None:
            fc = fill.get('faceColor', 'none')
            if fc != 'none':
                bg = fc

        border_desc = ', '.join(parts)
        lines.append(f"  [{bid}] {border_desc}")
        if bg != "none":
            lines.append(f"       background={bg}")
    lines.append("")
    return lines


def analyze_charprops(root):
    lines = ["▶ charPr (character styles)"]
    for cp in root.findall('.//hh:charPr', NS):
        cid = cp.get('id')
        height = int(cp.get('height', '0'))
        pt = height / 100
        color = cp.get('textColor', '#000000')
        bfref = cp.get('borderFillIDRef', '?')

        fontref = cp.find('hh:fontRef', NS)
        font_id = fontref.get('hangul', '0') if fontref is not None else '0'
        font_name = FONT_MAP.get(('HANGUL', font_id), f'font{font_id}')

        spacing_el = cp.find('hh:spacing', NS)
        spacing = int(spacing_el.get('hangul', '0')) if spacing_el is not None else 0

        flags = []
        if cp.find('hh:bold', NS) is not None:
            flags.append('bold')
        if cp.find('hh:italic', NS) is not None:
            flags.append('italic')
        ul = cp.find('hh:underline', NS)
        if ul is not None and ul.get('type', 'NONE') != 'NONE':
            ul_shape = ul.get('shape', 'SOLID')
            flags.append(f'underline({ul_shape})')
        so = cp.find('hh:strikeout', NS)
        if so is not None and so.get('shape', 'NONE') != 'NONE':
            flags.append('strikethrough')

        flag_str = ' '.join(flags) if flags else ''
        spacing_str = f' spacing={spacing}' if spacing != 0 else ''
        line = f"  [{cid}] {pt}pt {font_name} {color}{spacing_str} {flag_str}".rstrip()
        lines.append(line)
        lines.append(f"       fontRef=hangul:{font_id} borderFillIDRef={bfref}")
    lines.append("")
    return lines


def analyze_paraprops(root):
    lines = ["▶ paraPr (paragraph styles)"]
    for pp in root.findall('.//hh:paraPr', NS):
        pid = pp.get('id')
        tabref = pp.get('tabPrIDRef', '0')

        align = pp.find('hh:align', NS)
        h_align = align.get('horizontal', '?') if align is not None else '?'
        v_align = align.get('vertical', '?') if align is not None else '?'

        heading = pp.find('hh:heading', NS)
        h_type = heading.get('type', 'NONE') if heading is not None else 'NONE'
        h_level = heading.get('level', '0') if heading is not None else '0'

        ls_val = '?'
        ls_type = '?'
        ls = pp.find('.//hh:lineSpacing', NS)
        if ls is not None:
            ls_val = ls.get('value', '?')
            ls_type = ls.get('type', 'PERCENT')

        margins = {}
        for m_name in ['intent', 'left', 'right', 'prev', 'next']:
            m = pp.find(f'.//hc:{m_name}', NS)
            if m is not None:
                val = m.get('value', '0')
                margins[m_name] = val

        border = pp.find('hh:border', NS)
        bf_ref = border.get('borderFillIDRef', '2') if border is not None else '2'
        b_offsets = {}
        if border is not None:
            for attr in ['offsetLeft', 'offsetRight', 'offsetTop', 'offsetBottom']:
                v = border.get(attr, '0')
                if v != '0':
                    b_offsets[attr] = v

        margin_parts = []
        for k, v in margins.items():
            if v != '0':
                margin_parts.append(f"{k}={v}")
        margin_str = ', '.join(margin_parts) if margin_parts else 'none'

        heading_str = ""
        if h_type != 'NONE':
            heading_str = f" heading={h_type} level={h_level}"

        lines.append(f"  [{pid}] {h_align} lineSpacing={ls_val}{ls_type}{heading_str}")
        lines.append(f"       margins({margin_str}) borderFillIDRef={bf_ref}")
        if b_offsets:
            lines.append(f"       borderOffset({', '.join(f'{k}={v}' for k, v in b_offsets.items())})")
    lines.append("")
    return lines


def analyze_cell(tc, indent=""):
    lines = []

    bf = tc.get('borderFillIDRef', '?')
    addr = tc.find('hp:cellAddr', NS)
    col = addr.get('colAddr', '?') if addr is not None else '?'
    row = addr.get('rowAddr', '?') if addr is not None else '?'

    span = tc.find('hp:cellSpan', NS)
    cs = span.get('colSpan', '1') if span is not None else '1'
    rs = span.get('rowSpan', '1') if span is not None else '1'

    sz = tc.find('hp:cellSz', NS)
    w = sz.get('width', '?') if sz is not None else '?'
    h = sz.get('height', '?') if sz is not None else '?'

    margin = tc.find('hp:cellMargin', NS)
    cm_str = ""
    if margin is not None:
        ml = margin.get('left', '0')
        mr = margin.get('right', '0')
        mt = margin.get('top', '0')
        mb = margin.get('bottom', '0')
        cm_str = f" cellMargin=[{ml},{mr},{mt},{mb}]"

    span_str = ""
    if cs != '1':
        span_str += f" colSpan={cs}"
    if rs != '1':
        span_str += f" rowSpan={rs}"

    lines.append(f"{indent}Cell({col},{row}) w={w} h={h}{span_str} borderFill={bf}{cm_str}")

    sublist = tc.find('hp:subList', NS)
    if sublist is not None:
        valign = sublist.get('vertAlign', '?')
        if valign != 'CENTER':
            lines.append(f"{indent}  vertAlign={valign}")
        for p in sublist.findall('hp:p', NS):
            ppr = p.get('paraPrIDRef', '0')
            run_parts = []
            for run in p.findall('hp:run', NS):
                cpr = run.get('charPrIDRef', '0')
                txt = get_text(run)
                nested_tbl = run.find('hp:tbl', NS)
                if nested_tbl is not None:
                    run_parts.append('[nested table]')
                elif txt:
                    # Truncate long text
                    display = txt[:40] + '...' if len(txt) > 40 else txt
                    run_parts.append(f'charPr={cpr}:"{display}"')
                else:
                    run_parts.append(f'charPr={cpr}:(empty)')
            content = ' + '.join(run_parts) if run_parts else '(empty)'
            lines.append(f"{indent}  P paraPr={ppr} {content}")

    return '\n'.join(lines)


def analyze_table(tbl, indent=""):
    lines = []

    rows = int(tbl.get('rowCnt', '0'))
    cols = int(tbl.get('colCnt', '0'))
    # XML byte/node limits do not bound numeric geometry amplification.
    # Refuse before allocating the column view, even for tables without cells.
    if not (1 <= rows <= 10000 and 1 <= cols <= 1000) or rows * cols > 100000:
        raise ValueError('Analysis table geometry budget exceeded')
    tbl_id = tbl.get('id', '?')
    bf = tbl.get('borderFillIDRef', '?')
    repeat_header = tbl.get('repeatHeader', '0')
    page_break = tbl.get('pageBreak', '?')

    sz = tbl.find('hp:sz', NS)
    w = sz.get('width', '?') if sz is not None else '?'
    h = sz.get('height', '?') if sz is not None else '?'

    pos = tbl.find('hp:pos', NS)
    treat_as_char = pos.get('treatAsChar', '?') if pos is not None else '?'
    h_align = pos.get('horzAlign', '?') if pos is not None else '?'

    lines.append(f"{indent}┌─ TABLE id={tbl_id} {rows} rows × {cols} columns w={w} h={h}")
    lines.append(f"{indent}│  borderFill={bf} treatAsChar={treat_as_char} horzAlign={h_align}")

    # Collect column widths from first data row
    col_widths = {}
    for tr in tbl.findall('hp:tr', NS):
        for tc in tr.findall('hp:tc', NS):
            addr = tc.find('hp:cellAddr', NS)
            if addr is not None:
                col_idx = int(addr.get('colAddr', '0'))
                span_el = tc.find('hp:cellSpan', NS)
                cs = int(span_el.get('colSpan', '1')) if span_el is not None else 1
                if cs == 1 and col_idx not in col_widths:
                    csz = tc.find('hp:cellSz', NS)
                    if csz is not None:
                        col_widths[col_idx] = csz.get('width', '?')

    # Bound the rendered width line before range/list/join allocation.
    width_chars = cols * 3 + sum(len(value) for value in col_widths.values())
    if width_chars + len(indent) > 1000000:
        raise ValueError('Analysis column output budget exceeded')
    sorted_widths = [col_widths.get(i, '?') for i in range(cols)]
    lines.append(f"{indent}│  Column widths: [{', '.join(sorted_widths)}]")
    total = sum(int(v) for v in sorted_widths if v != '?')
    lines.append(f"{indent}│  Total: {total}")
    lines.append(f"{indent}│")

    for ri, tr in enumerate(tbl.findall('hp:tr', NS)):
        lines.append(f"{indent}│  ── Row {ri}")
        for tc in tr.findall('hp:tc', NS):
            cell_lines = analyze_cell(tc, indent + "│     ")
            lines.append(cell_lines)
    lines.append(f"{indent}└─────")
    lines.append("")

    return '\n'.join(lines)


def analyze_paragraph(p, indent=""):
    lines = []

    pid = p.get('id', '?')
    ppr = p.get('paraPrIDRef', '0')

    run_parts = []
    has_table = False
    has_secpr = False

    for run in p.findall('hp:run', NS):
        cpr = run.get('charPrIDRef', '0')

        if run.find('hp:secPr', NS) is not None:
            has_secpr = True

        tbl = run.find('hp:tbl', NS)
        if tbl is not None:
            has_table = True
            if run_parts:
                content = ' + '.join(run_parts)
                lines.append(f"{indent}P id={pid} paraPr={ppr} {content}")
                run_parts = []
            lines.append(analyze_table(tbl, indent))
            continue

        txt = get_text(run)
        if txt:
            display = txt[:50] + '...' if len(txt) > 50 else txt
            run_parts.append(f'charPr={cpr}:"{display}"')
        else:
            run_parts.append(f'charPr={cpr}:(empty)')

    if not has_table:
        content = ' + '.join(run_parts) if run_parts else '(empty)'
        prefix = '[secPr] ' if has_secpr else ''
        lines.append(f"{indent}P id={pid} paraPr={ppr} {prefix}{content}")
    elif run_parts:
        content = ' + '.join(run_parts)
        lines.append(f"{indent}P id={pid} paraPr={ppr} {content}")

    return '\n'.join(lines)


def analyze_section(section_root):
    lines = ["▶ Document structure"]

    secpr = section_root.find('.//hp:secPr', NS)
    if secpr is not None:
        pagepr = secpr.find('hp:pagePr', NS)
        if pagepr is not None:
            w = pagepr.get('width', '?')
            h = pagepr.get('height', '?')
            landscape = pagepr.get('landscape', '?')
            lines.append(f"  Page: {w} × {h} ({landscape})")
            margin = pagepr.find('hp:margin', NS)
            if margin is not None:
                lines.append(f"  Margins: left={margin.get('left')} right={margin.get('right')} top={margin.get('top')} bottom={margin.get('bottom')}")
                lines.append(f"  Header={margin.get('header')} Footer={margin.get('footer')}")
                left = int(margin.get('left', '0'))
                right = int(margin.get('right', '0'))
                lines.append(f"  Body width: {int(w) - left - right} ({w}-{left}-{right})")

        for pbf in secpr.findall('hp:pageBorderFill', NS):
            ptype = pbf.get('type', '?')
            if ptype == 'BOTH':
                bfref = pbf.get('borderFillIDRef', '?')
                tb = pbf.get('textBorder', '?')
                off = pbf.find('hp:offset', NS)
                if off is not None:
                    lines.append(f"  Page border: borderFill={bfref} textBorder={tb} offset=[{off.get('left')},{off.get('right')},{off.get('top')},{off.get('bottom')}]")

    lines.append("")
    lines.append("  ════════ Body ════════")
    lines.append("")

    sec = section_root.find('.//hs:sec', NS)
    if sec is None:
        sec = section_root
    for p in sec.findall('hp:p', NS):
        para_lines = analyze_paragraph(p, "  ")
        lines.append(para_lines)

    return '\n'.join(lines)


def _xml_structure(node):
    """Untruncated, namespace-qualified XML structure, including inline controls."""
    return {"tag": str(node.tag), "attributes": dict(node.attrib),
            "text": node.text, "tail": node.tail,
            "children": [_xml_structure(child) for child in node if isinstance(child.tag, str)]}


def _build_analysis_json(header_root, section_root, filename):
    """Build a JSON-serializable dict with the full analysis result."""
    result = {"file": filename, "body": _xml_structure(section_root),
              "headerStructure": _xml_structure(header_root)}

    # Fonts
    fonts = []
    for fontface in header_root.findall('.//hh:fontface', NS):
        lang = fontface.get('lang', '?')
        for font in fontface.findall('hh:font', NS):
            fid = font.get('id')
            face = font.get('face')
            fonts.append({"lang": lang, "id": fid, "face": face})
    result["fonts"] = fonts

    # BorderFills
    borderfills = []
    for bf in header_root.findall('.//hh:borderFill', NS):
        bid = bf.get('id')
        borders = {}
        for side in ['left', 'right', 'top', 'bottom']:
            b = bf.find(f'hh:{side}Border', NS)
            if b is not None:
                borders[side] = {"type": b.get('type', 'NONE'), "width": b.get('width', '')}
        bg = "none"
        fill = bf.find('.//hc:winBrush', NS)
        if fill is not None:
            fc = fill.get('faceColor', 'none')
            if fc != 'none':
                bg = fc
        borderfills.append({"id": bid, "borders": borders, "background": bg})
    result["borderFills"] = borderfills

    # CharProperties
    charprops = []
    for cp in header_root.findall('.//hh:charPr', NS):
        cid = cp.get('id')
        height = int(cp.get('height', '0'))
        color = cp.get('textColor', '#000000')
        bfref = cp.get('borderFillIDRef', '?')
        fontref = cp.find('hh:fontRef', NS)
        font_id = fontref.get('hangul', '0') if fontref is not None else '0'
        flags = []
        if cp.find('hh:bold', NS) is not None:
            flags.append('bold')
        if cp.find('hh:italic', NS) is not None:
            flags.append('italic')
        ul = cp.find('hh:underline', NS)
        if ul is not None and ul.get('type', 'NONE') != 'NONE':
            flags.append('underline')
        so = cp.find('hh:strikeout', NS)
        if so is not None and so.get('shape', 'NONE') != 'NONE':
            flags.append('strikeout')
        charprops.append({
            "id": cid, "height": height, "sizePt": height / 100,
            "textColor": color, "fontRef": font_id,
            "borderFillIDRef": bfref, "flags": flags,
        })
    result["charProperties"] = charprops

    # ParaProperties
    paraprops = []
    for pp in header_root.findall('.//hh:paraPr', NS):
        pid = pp.get('id')
        align = pp.find('hh:align', NS)
        h_align = align.get('horizontal', '?') if align is not None else '?'
        ls = pp.find('.//hh:lineSpacing', NS)
        ls_val = ls.get('value', '?') if ls is not None else '?'
        ls_type = ls.get('type', 'PERCENT') if ls is not None else '?'
        paraprops.append({
            "id": pid, "align": h_align,
            "lineSpacing": {"value": ls_val, "type": ls_type},
        })
    result["paraProperties"] = paraprops

    # Page info
    secpr = section_root.find('.//hp:secPr', NS)
    page_info = {}
    if secpr is not None:
        pagepr = secpr.find('hp:pagePr', NS)
        if pagepr is not None:
            page_info["width"] = pagepr.get('width', '?')
            page_info["height"] = pagepr.get('height', '?')
            page_info["landscape"] = pagepr.get('landscape', '?')
            margin = pagepr.find('hp:margin', NS)
            if margin is not None:
                page_info["margins"] = {
                    "left": margin.get('left', '0'),
                    "right": margin.get('right', '0'),
                    "top": margin.get('top', '0'),
                    "bottom": margin.get('bottom', '0'),
                    "header": margin.get('header', '0'),
                    "footer": margin.get('footer', '0'),
                }
                left = int(margin.get('left', '0'))
                right = int(margin.get('right', '0'))
                page_info["bodyWidth"] = int(page_info["width"]) - left - right
    result["page"] = page_info

    return result


def main():
    parser = argparse.ArgumentParser(description='Perform an in-depth analysis of an HWPX document structure')
    parser.add_argument('input', help='HWPX file to analyze')
    parser.add_argument('--extract-header', metavar='PATH',
                        help='Extract header.xml to the specified path')
    parser.add_argument('--extract-section', metavar='PATH',
                        help='Extract section0.xml to the specified path')
    parser.add_argument('--json', action='store_true',
                        help='Print the analysis result as JSON to stdout')
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found")
        sys.exit(1)

    # Read only checked members; extraction destinations must not alias source.
    with zipfile.ZipFile(args.input, 'r') as archive:
        validate_archive(archive)
        header_data = read_checked_member(archive, 'Contents/header.xml')
        header_root = parse_xml(header_data)
        entries = _section_entries(args.input)
        sections = [(name, parse_xml(read_checked_member(archive, name))) for name in entries]
        for destination, member in ((args.extract_header, 'Contents/header.xml'),
                                    (args.extract_section, 'Contents/section0.xml')):
            if destination:
                require_distinct_paths(args.input, destination)
                with atomic_output_path(destination) as staged:
                    staged.write_bytes(read_checked_member(archive, member))
                print(f"{member} → {destination}", file=sys.stderr)
    section_root = sections[0][1]
    if args.json:
        analysis = _build_analysis_json(header_root, section_root, os.path.basename(args.input))
        analysis['sections'] = [dict(name=name, **_build_analysis_json(header_root, sec, os.path.basename(args.input))) for name, sec in sections]
        print(json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print("=" * 64)
        print(f"  In-depth HWPX analysis: {os.path.basename(args.input)}")
        for analyzer in (analyze_fonts, analyze_borderfills, analyze_charprops, analyze_paraprops):
            for line in analyzer(header_root):
                print(line)
        for name, sec in sections:
            print(name)
            print(analyze_section(sec))


if __name__ == '__main__':
    main()
