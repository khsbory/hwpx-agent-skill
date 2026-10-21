#!/usr/bin/env python3
"""Build an HWPX document from templates and XML overrides.

Assembles a valid HWPX file by:
1. Copying the base template
2. Optionally overlaying a document-type template (gonmun, report, minutes, proposal)
3. Optionally overriding header.xml and/or section0.xml with custom files
4. Optionally setting metadata (title, creator)
5. Validating XML well-formedness
6. Packaging as HWPX (ZIP with mimetype first, ZIP_STORED)

Usage:
    # Empty document from base template
    python build_hwpx.py --output result.hwpx

    # Using a document-type template
    python build_hwpx.py --template gonmun --output result.hwpx

    # Custom section XML override
    python build_hwpx.py --template gonmun --section my_section0.xml --output result.hwpx

    # Custom header and section
    python build_hwpx.py --header my_header.xml --section my_section0.xml --output result.hwpx

    # With metadata
    python build_hwpx.py --template gonmun --section my.xml --title "제목" --creator "작성자" --output result.hwpx
"""

import argparse
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from lxml import etree
from document_safety import (DocumentSafetyError, atomic_output_path, parse_xml,
                             require_distinct_paths)
from validate import validate as validate_package

# Resolve paths relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATES_DIR = SKILL_DIR / "templates"
BASE_DIR = TEMPLATES_DIR / "base"

AVAILABLE_TEMPLATES = ["gonmun", "report", "minutes", "proposal"]


def validate_xml(filepath: Path) -> None:
    """Check that an XML file is well-formed. Raises on error."""
    try:
        parse_xml(filepath.read_bytes())
    except ValueError as e:
        raise SystemExit(f"Malformed XML in {filepath.name}: {e}")


def update_metadata(content_hpf: Path, title: str | None, creator: str | None) -> None:
    """Update title and/or creator in content.hpf."""
    if not title and not creator:
        return

    root = parse_xml(content_hpf.read_bytes())
    tree = root.getroottree()
    ns = {"opf": "http://www.idpf.org/2007/opf/"}

    if title:
        title_el = root.find(".//opf:title", ns)
        if title_el is not None:
            title_el.text = title

    now = datetime.now(timezone.utc)
    iso_now = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    for meta in root.findall(".//opf:meta", ns):
        name = meta.get("name", "")
        if creator and name == "creator":
            meta.text = creator
        elif creator and name == "lastsaveby":
            meta.text = creator
        elif name == "CreatedDate":
            meta.text = iso_now
        elif name == "ModifiedDate":
            meta.text = iso_now
        elif name == "date":
            meta.text = now.strftime("%Y년 %m월 %d일")

    etree.indent(root, space="  ")
    tree.write(
        str(content_hpf),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    )


def pack_hwpx(input_dir: Path, output_path: Path) -> None:
    """Stage, validate, then replace; never package links or the output itself."""
    input_dir, output_path = Path(input_dir), Path(output_path)
    root = input_dir.resolve()
    if output_path.resolve().is_relative_to(root):
        raise DocumentSafetyError('Output must not be inside the input directory')
    all_paths = sorted(input_dir.rglob('*'))
    total = 0
    from document_safety import MAX_MEMBER_BYTES, MAX_TOTAL_BYTES, MAX_MEMBERS
    for path in [input_dir, *all_paths]:
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise DocumentSafetyError(f'Packaging links is forbidden: {path}')
        if path.is_file():
            require_distinct_paths(path, output_path)
            size = path.stat().st_size
            total += size
            if size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                raise DocumentSafetyError('Package input size limit exceeded')
        elif not path.is_dir():
            raise DocumentSafetyError(f'Special package input: {path}')
    if len(all_paths) > MAX_MEMBERS:
        raise DocumentSafetyError('Package member count limit exceeded')
    mimetype_file = input_dir / "mimetype"
    if not mimetype_file.is_file():
        raise SystemExit(f"Missing 'mimetype' in {input_dir}")

    all_files = sorted(
        p.relative_to(input_dir).as_posix()
        for p in input_dir.rglob("*")
        if p.is_file()
    )

    with atomic_output_path(output_path) as staged:
        with ZipFile(staged, 'w', ZIP_DEFLATED) as zf:
            zf.write(mimetype_file, 'mimetype', compress_type=ZIP_STORED)
            for rel_path in all_files:
                if rel_path != 'mimetype':
                    zf.write(input_dir / rel_path, rel_path, compress_type=ZIP_DEFLATED)
        errors = validate_hwpx(staged)
        if errors:
            raise DocumentSafetyError('; '.join(errors))


def validate_hwpx(hwpx_path: Path) -> list[str]:
    """Basic package validation, not full schema or rendered-layout validation."""
    return validate_package(hwpx_path)


def build(
    template: str | None,
    header_override: Path | None,
    section_override: Path | None,
    title: str | None,
    creator: str | None,
    output: Path,
) -> None:
    """Main build logic."""

    if not BASE_DIR.is_dir():
        raise SystemExit(f"Base template not found: {BASE_DIR}")
    for source in (header_override, section_override):
        if source:
            require_distinct_paths(source, output)
    if template and template not in AVAILABLE_TEMPLATES:
        raise DocumentSafetyError(f'Unknown template: {template}')

    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir) / "build"

        # 1. Copy base template
        shutil.copytree(BASE_DIR, work)

        # 2. Apply template overlay
        if template:
            overlay_dir = TEMPLATES_DIR / template
            if not overlay_dir.is_dir():
                raise SystemExit(
                    f"Template '{template}' not found. "
                    f"Available: {', '.join(AVAILABLE_TEMPLATES)}"
                )
            for overlay_file in overlay_dir.iterdir():
                if overlay_file.is_file() and overlay_file.suffix == ".xml":
                    dest = work / "Contents" / overlay_file.name
                    shutil.copy2(overlay_file, dest)

        # 3. Apply custom overrides
        if header_override:
            if not header_override.is_file():
                raise SystemExit(f"Header file not found: {header_override}")
            shutil.copy2(header_override, work / "Contents" / "header.xml")

        if section_override:
            if not section_override.is_file():
                raise SystemExit(f"Section file not found: {section_override}")
            shutil.copy2(section_override, work / "Contents" / "section0.xml")

        # 4. Update metadata
        update_metadata(work / "Contents" / "content.hpf", title, creator)

        # 5. Validate all XML files
        for xml_file in work.rglob("*.xml"):
            validate_xml(xml_file)
        for hpf_file in work.rglob("*.hpf"):
            validate_xml(hpf_file)

        # 6. Pack
        with atomic_output_path(output) as staged:
            pack_hwpx(work, staged)
            errors = validate_hwpx(staged)
            if errors:
                raise DocumentSafetyError('; '.join(errors))

    # Validation completed before publishing the staged output.
    print(f'VALID (basic structure only): {output}')
    print(f'  Template: {template or "base"}')
    if header_override:
        print(f'  Header: {header_override}')
    if section_override:
        print(f'  Section: {section_override}')


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build HWPX document from templates and XML overrides"
    )
    parser.add_argument(
        "--template", "-t",
        choices=AVAILABLE_TEMPLATES,
        help="Document type template to use as overlay",
    )
    parser.add_argument(
        "--header",
        type=Path,
        help="Custom header.xml to override",
    )
    parser.add_argument(
        "--section",
        type=Path,
        help="Custom section0.xml to override",
    )
    parser.add_argument(
        "--title",
        help="Document title (updates content.hpf metadata)",
    )
    parser.add_argument(
        "--creator",
        help="Document creator (updates content.hpf metadata)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Output .hwpx file path",
    )
    args = parser.parse_args()

    build(
        template=args.template,
        header_override=args.header,
        section_override=args.section,
        title=args.title,
        creator=args.creator,
        output=args.output,
    )


if __name__ == "__main__":
    main()
