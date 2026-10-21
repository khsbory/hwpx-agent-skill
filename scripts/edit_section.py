#!/usr/bin/env python3
"""edit_section.py -- Search, replace, and delete text in an existing HWPX document.

A standalone CLI tool that performs find/replace and paragraph deletion
on section0.xml inside HWPX archives.

Usage:
    python edit_section.py input.hwpx --find "old" --replace "new" --output out.hwpx
    python edit_section.py input.hwpx --delete-containing "paragraph text"
    python edit_section.py input.hwpx --find "old" --replace "new"  (overwrites input)
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from lxml import etree
from document_safety import (safe_extract, parse_xml, atomic_output_path,
                             require_distinct_paths)
from validate import validate

# ---------------------------------------------------------------------------
# HWPX XML namespace
# ---------------------------------------------------------------------------
NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
}


# ---------------------------------------------------------------------------
# Unpack / Pack helpers
# ---------------------------------------------------------------------------

def _unpack_hwpx(hwpx_path: Path, output_dir: Path) -> None:
    """Extract all entries from an HWPX (ZIP) archive into *output_dir*.

    Args:
        hwpx_path: Path to the source .hwpx file.
        output_dir: Directory to extract files into.
    """
    with ZipFile(str(hwpx_path), "r") as zf:
        safe_extract(zf, output_dir)


def _pack_hwpx(input_dir: Path, hwpx_path: Path) -> None:
    """Repack a directory into an HWPX file following OPC conventions.

    The ``mimetype`` file is stored as the first entry without compression,
    as required by OPC packaging rules.

    Args:
        input_dir: Root directory containing the unpacked HWPX contents.
        hwpx_path: Destination path for the resulting .hwpx file.
    """
    root = input_dir
    mimetype_file = root / "mimetype"

    all_files = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
    )

    with atomic_output_path(hwpx_path) as staged:
        with ZipFile(str(staged), "w", ZIP_DEFLATED) as zf:
            # mimetype MUST be first entry, stored without compression
            if mimetype_file.is_file():
                zf.write(str(mimetype_file), "mimetype", compress_type=ZIP_STORED)
    
            for rel_path in all_files:
                if rel_path == "mimetype":
                    continue
                full_path = root / rel_path
                zf.write(str(full_path), rel_path, compress_type=ZIP_DEFLATED)
        errors = validate(str(staged))
        if errors:
            raise ValueError("HWPX validation failed: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# XML load / save
# ---------------------------------------------------------------------------

def _load_section_xml(section_path: Path) -> etree._Element:
    """Parse a section XML file and return its root element.

    Args:
        section_path: Path to the section XML file (e.g. section0.xml).

    Returns:
        The root lxml Element of the parsed XML.
    """
    return parse_xml(section_path.read_bytes())


def _save_section_xml(root: etree._Element, section_path: Path) -> None:
    """Serialize an lxml element tree back to an XML file.

    Args:
        root: The root lxml Element to serialize.
        section_path: Destination file path.
    """
    tree = etree.ElementTree(root)
    tree.write(
        str(section_path),
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def find_and_replace(root: etree._Element, find_text: str, replace_text: str) -> int:
    """Replace contiguous own-paragraph text across runs and hp:t tails.

    Nested paragraphs and object/inline-control boundaries cannot form a match.
    Replacement text inherits the first matched slot; other run XML is preserved.

    Args:
        root: The root element of the section XML.
        find_text: The text string to search for.
        replace_text: The text string to substitute in.

    Returns:
        The total number of replacements made across all elements.
    """
    if not find_text:
        raise ValueError('find_text must not be empty')
    total_count = 0
    for para in root.iter('{%s}p' % NS['hp']):
        slots = _paragraph_slots(para)
        text = ''.join(value for _, _, value in slots)
        starts = []
        offset = 0
        for _, _, value in slots:
            starts.append(offset)
            offset += len(value)
        matches = []
        at = 0
        while (at := text.find(find_text, at)) >= 0:
            matches.append(at)
            at += len(find_text)
        for at in reversed(matches):
            end = at + len(find_text)
            first = True
            for (node, attr, value), start in zip(slots, starts):
                if node is None or start >= end or start + len(value) <= at:
                    continue
                current = getattr(node, attr) or ''
                lo, hi = max(0, at - start), min(len(value), end - start)
                setattr(node, attr, current[:lo] + (replace_text if first else '') + current[hi:])
                first = False
            total_count += 1
    return total_count


def _paragraph_slots(para):
    """Own text/tails only; object/control boundaries cannot form a match."""
    slots = []
    hp = '{%s}' % NS['hp']
    def walk(node, in_text=False):
        if not isinstance(node.tag, str):
            return  # non-visible payload is not an edit slot; parent keeps tail
        if node is not para and node.tag not in {
                hp+'run', hp+'t', hp+'tab', hp+'lineBreak', hp+'br'}:
            slots.append((None, None, '\0'))
            return
        inside = in_text or node.tag == hp+'t'
        if inside and node.text:
            slots.append((node, 'text', node.text))
        for child in node:
            if inside and child.tag in {hp+'tab', hp+'lineBreak', hp+'br'}:
                slots.append((None, None, '\0'))
            else:
                walk(child, inside)
            if inside and child.tail:
                slots.append((child, 'tail', child.tail))
    walk(para)
    return slots


def delete_paragraphs_containing(root: etree._Element, search_text: str) -> int:
    """Delete matching own-paragraph text without deleting ancestor objects.

    Keep section/control-bearing paragraphs and a last required cell/body
    paragraph as empty scaffolding. The count includes these cleared paragraphs.

    Args:
        root: The root element of the section XML.
        search_text: Text to search for within paragraphs.

    Returns:
        The number of paragraphs deleted.
    """
    if not search_text:
        raise ValueError('search_text must not be empty')
    count = 0
    for para in list(root.iter('{%s}p' % NS['hp'])):
        slots = _paragraph_slots(para)
        if search_text not in ''.join(value for _, _, value in slots):
            continue
        # Preserve non-text structures, including section settings and tables.
        for node, attr, _ in slots:
            if node is not None:
                setattr(node, attr, '')
        parent = para.getparent()
        # Only a text-only paragraph is disposable. Unknown anchored objects
        # must survive too; enumerating known picture/shape types is incomplete.
        text_tags = {'{%s}%s' % (NS['hp'], name)
                     for name in ('run', 't', 'tab', 'lineBreak', 'br')}
        protected = any(node.tag not in text_tags for node in para.iterdescendants())
        siblings = parent.findall('hp:p', NS) if parent is not None else []
        if parent is not None and not protected and len(siblings) > 1:
            parent.remove(para)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def edit_section(
    input_path: Path,
    output_path: Path,
    find_text: str | None = None,
    replace_text: str | None = None,
    delete_containing: str | None = None,
) -> dict:
    """Run safe unpack -> edit section0 -> validate staged archive -> atomic publish.

    Passing the same input/output path explicitly requests atomic in-place editing.
    Cached preview images/text and layout caches are not regenerated.
    This operation does not establish rendered pagination or appearance.

    At least one operation (find/replace or delete-containing) must be
    specified.  Both may be combined in a single invocation.

    Args:
        input_path: Path to the source .hwpx file.
        output_path: Path for the resulting .hwpx file.
        find_text: Text to search for (used with *replace_text*).
        replace_text: Replacement text (used with *find_text*).
        delete_containing: If set, paragraphs containing this text are deleted.

    Returns:
        A dict with ``replacement_count`` and ``deleted_paragraph_count`` keys.

    Raises:
        SystemExit: On invalid input or missing section0.xml.
    """
    if (find_text is None) != (replace_text is None) or (find_text is None and delete_containing is None):
        raise ValueError("Provide find/replace and/or delete-containing")
    if find_text == "" or delete_containing == "":
        raise ValueError("Search text must not be empty")
    if not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="hwpx_edit_section_") as tmp:
        temp_dir = Path(tmp)
        _unpack_hwpx(input_path, temp_dir)

        section_path = temp_dir / "Contents" / "section0.xml"
        if not section_path.is_file():
            print(
                "Error: section0.xml not found inside the HWPX archive.",
                file=sys.stderr,
            )
            sys.exit(1)

        root = _load_section_xml(section_path)

        replacement_count = 0
        deleted_paragraph_count = 0

        if find_text is not None and replace_text is not None:
            replacement_count = find_and_replace(root, find_text, replace_text)
            print(f"Replacements made: {replacement_count}")

        if delete_containing is not None:
            deleted_paragraph_count = delete_paragraphs_containing(
                root, delete_containing
            )
            print(f"Paragraphs deleted: {deleted_paragraph_count}")

        _save_section_xml(root, section_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        _pack_hwpx(temp_dir, output_path)

    print(f"Output written to: {output_path}")
    return {
        "replacement_count": replacement_count,
        "deleted_paragraph_count": deleted_paragraph_count,
    }


def main() -> int:
    """CLI entry point for edit_section.

    Returns:
        Exit code (0 on success, 1 on error).
    """
    parser = argparse.ArgumentParser(
        description="Search, replace, and delete text in an HWPX document's section0.xml."
    )
    parser.add_argument(
        "input",
        help="Path to the input .hwpx file.",
    )
    parser.add_argument(
        "--find",
        default=None,
        help="Text to search for in <hp:t> elements.",
    )
    parser.add_argument(
        "--replace",
        default=None,
        help="Replacement text (requires --find).",
    )
    parser.add_argument(
        "--delete-containing",
        default=None,
        help="Delete all <hp:p> paragraphs containing this text.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output .hwpx path. Defaults to overwriting the input file.",
    )
    args = parser.parse_args()

    # --- Validation ---
    if not os.path.isfile(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        return 1

    has_find_replace = args.find is not None
    has_delete = args.delete_containing is not None

    if not has_find_replace and not has_delete:
        print(
            "Error: at least one of --find/--replace or --delete-containing is required.",
            file=sys.stderr,
        )
        return 1

    if has_find_replace and args.replace is None:
        print("Error: --replace is required when --find is specified.", file=sys.stderr)
        return 1

    if args.replace is not None and args.find is None:
        print("Error: --find is required when --replace is specified.", file=sys.stderr)
        return 1

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve() if args.output else input_path

    # Historical no-output CLI intentionally permits atomic in-place editing.
    if args.output:
        require_distinct_paths(input_path, output_path)
    edit_section(input_path=input_path, output_path=output_path,
                 find_text=args.find, replace_text=args.replace,
                 delete_containing=args.delete_containing)

    return 0


if __name__ == "__main__":
    sys.exit(main())
