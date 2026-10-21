"""Bounded document I/O. Resource limits are policy, not schema validation."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zlib
from zipfile import BadZipFile
from lxml import etree

MAX_MEMBERS = 4096
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_XML_NODES = 250000
MAX_XML_DEPTH = 128
CHUNK_BYTES = 64 * 1024


class DocumentSafetyError(ValueError):
    """Unsafe or malformed document input."""


def _member_path(name):
    if (not name or '\\' in name or ':' in name or name.startswith('/')
            or any(ord(c) < 32 for c in name)
            or any(p.endswith((' ', '.')) for p in name.rstrip('/').split('/'))
            or any(re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p, re.I)
                   for p in name.rstrip('/').split('/'))
            or any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))):
        raise DocumentSafetyError(f'Unsafe archive path: {name!r}')
    return PurePosixPath(name)


def validate_archive(zf):
    """Check metadata before any decompression."""
    if len(zf.infolist()) > MAX_MEMBERS:
        raise DocumentSafetyError('Archive member count limit exceeded')
    seen = {}
    total = 0
    for info in zf.infolist():
        _member_path(info.orig_filename)
        path = _member_path(info.filename)
        key = str(path).casefold()
        if key in seen:
            raise DocumentSafetyError(f'Duplicate archive path: {info.filename}')
        seen[key] = info.is_dir()
        kind = stat.S_IFMT(info.external_attr >> 16)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise DocumentSafetyError(f'Link or special archive entry: {info.filename}')
        if info.flag_bits & 1:
            raise DocumentSafetyError('Encrypted archive entries are unsupported')
        if info.file_size > MAX_MEMBER_BYTES:
            raise DocumentSafetyError('Archive member size limit exceeded')
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise DocumentSafetyError('Archive expanded size limit exceeded')
        if info.file_size / max(1, info.compress_size) > MAX_COMPRESSION_RATIO:
            raise DocumentSafetyError('Archive compression ratio limit exceeded')
    for name in seen:
        for parent in PurePosixPath(name).parents:
            if str(parent) in seen and not seen[str(parent)]:
                raise DocumentSafetyError('Archive file/directory path collision')


def read_checked_member(zf, name):
    validate_archive(zf)
    try:
        info = zf.getinfo(name)
        limit = min(MAX_MEMBER_BYTES, info.file_size)
        chunks, count = [], 0
        with zf.open(info) as member:
            while True:
                chunk = member.read(min(CHUNK_BYTES, limit - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                if count > limit:
                    raise DocumentSafetyError(f'Expanded member exceeds limit: {name}')
                chunks.append(chunk)
        if count != info.file_size:
            raise DocumentSafetyError(f'Member length mismatch: {name}')
        return b''.join(chunks)
    except (KeyError, BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise DocumentSafetyError(f'Cannot read archive member {name}: {exc}') from exc


def parse_xml(data):
    """Parse XML with DTD/entities/network/recovery/huge trees forbidden."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    if len(data) > MAX_XML_BYTES:
        raise DocumentSafetyError('XML byte limit exceeded')
    probe = data.replace(b'\x00', b'')
    if re.search(br'<!\s*(DOCTYPE|ENTITY)\b', probe, re.I):
        raise DocumentSafetyError('DTD/entity declarations are forbidden')
    parser = etree.XMLPullParser(events=('start', 'end', 'comment', 'pi'),
                                 resolve_entities=False, load_dtd=False, no_network=True,
                                 huge_tree=False, recover=False)
    try:
        count = depth = 0
        # Enforce budgets during construction, rather than allocating the whole
        # tree before counting. At most one bounded feed chunk is buffered.
        for offset in range(0, len(data), CHUNK_BYTES):
            parser.feed(data[offset:offset + CHUNK_BYTES])
            for event, node in parser.read_events():
                if event == 'end':
                    depth -= 1
                    continue
                count += 1
                if event == 'start':
                    depth += 1
                if count > MAX_XML_NODES or depth > MAX_XML_DEPTH:
                    raise DocumentSafetyError('XML node/depth limit exceeded')
        root = parser.close()
        if root.getroottree().docinfo.doctype:
            raise DocumentSafetyError('DTD is forbidden')
        return root
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise DocumentSafetyError(f'Unsafe or malformed XML: {exc}') from exc


def safe_extract(zf, output):
    validate_archive(zf)
    output = Path(output).absolute()
    targets = [(info, output.joinpath(*_member_path(info.filename).parts))
               for info in zf.infolist()]
    for _, target in targets:
        for part in (target, *target.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise DocumentSafetyError('Extraction through links is forbidden')
        if not target.resolve().is_relative_to(output.resolve()):
            raise DocumentSafetyError('Extraction target escapes output directory')
    for info, target in targets:
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            data = read_checked_member(zf, info.filename)
            if info.filename.lower().endswith(('.xml', '.hpf')):
                parse_xml(data)
            target.parent.mkdir(parents=True, exist_ok=True)
            with atomic_output_path(target) as staged:
                staged.write_bytes(data)


@contextmanager
def atomic_output_path(destination):
    """Yield same-directory stage; replace only on successful context exit."""
    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=f'.{destination.name}.',
                                    suffix=destination.suffix, dir=destination.parent)
    os.close(fd)
    stage = Path(filename)
    try:
        yield stage
        os.replace(stage, destination)
    finally:
        stage.unlink(missing_ok=True)


def require_distinct_paths(source, destination):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve() or (
            source.exists() and destination.exists() and source.samefile(destination)):
        raise DocumentSafetyError('Input and output must be distinct paths')
