"""Synthetic, offline safety regressions. No real user documents."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from office.unpack import unpack

SCRATCH = Path.home() / 'AppData/Local/hermes/cache/scratch/hwpx-implementation-safety'
SCRATCH.mkdir(parents=True, exist_ok=True)

class DocumentSafetyTests(unittest.TestCase):
    def test_unpack_rejects_parent_escape_before_writing(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            root = Path(tmp)
            archive = root / 'bad.hwpx'
            with ZipFile(archive, 'w') as zf:
                zf.writestr('ok.txt', b'ok')
                zf.writestr('../escaped.txt', b'bad')
            with self.assertRaises(ValueError):
                unpack(str(archive), str(root / 'out'))
            self.assertFalse((root / 'escaped.txt').exists())
            self.assertFalse((root / 'out' / 'ok.txt').exists())

class ArchiveBoundaryTests(unittest.TestCase):
    def check_rejected(self, entries):
        import document_safety as safety
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as zf:
            for name, data in entries:
                zf.writestr(name, data)
        # Windows ZipInfo normalizes backslashes on write; mutate raw names to
        # represent an externally supplied archive rather than a sanitized one.
        raw = stream.getvalue()
        for name, _ in entries:
            if chr(92) in name:
                raw = raw.replace(name.replace(chr(92), '/').encode(), name.encode())
        stream = io.BytesIO(raw)
        with ZipFile(stream) as zf, self.assertRaises(ValueError):
            safety.validate_archive(zf)

    def test_dangerous_paths(self):
        for name in ['/absolute', 'C:/drive', '//server/share', 'a\\b', 'a/../b', 'x:y', 'a//b']:
            with self.subTest(name=name):
                self.check_rejected([(name, b'x')])

    def test_case_alias_duplicate(self):
        self.check_rejected([('A.txt', b'a'), ('a.txt', b'b')])

    def test_symlink_entry(self):
        import document_safety as safety
        from zipfile import ZipInfo
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as zf:
            info = ZipInfo('link')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            zf.writestr(info, '../out')
        with ZipFile(stream) as zf, self.assertRaises(ValueError):
            safety.validate_archive(zf)

    def test_archive_resource_limits_before_reads(self):
        import document_safety as safety
        from unittest.mock import patch
        for limit in ['MAX_MEMBERS', 'MAX_MEMBER_BYTES', 'MAX_TOTAL_BYTES', 'MAX_COMPRESSION_RATIO']:
            with self.subTest(limit=limit), patch.object(safety, limit, 0, create=True):
                self.check_rejected([('x', b'x')])

    def test_read_is_bounded_and_crc_checked(self):
        import document_safety as safety
        from unittest.mock import patch
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as zf:
            zf.writestr('x', b'abcd')
        with ZipFile(stream) as zf:
            with patch.object(safety, 'MAX_MEMBER_BYTES', 3, create=True):
                with self.assertRaises(ValueError):
                    safety.read_checked_member(zf, 'x')
            self.assertEqual(safety.read_checked_member(zf, 'x'), b'abcd')
            with self.assertRaises(ValueError):
                safety.read_checked_member(zf, 'absent')

    def test_xml_dtd_rejected_including_utf16(self):
        import document_safety as safety
        self.assertTrue(callable(getattr(safety, 'parse_xml', None)), 'safe XML API absent')
        for encoding in ['utf-8', 'utf-16']:
            data = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "bad">]><r>&x;</r>'.encode(encoding)
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                safety.parse_xml(data)
        self.assertEqual(safety.parse_xml(b'<r>ok</r>').text, 'ok')

    def test_xml_size_and_depth_limits(self):
        import document_safety as safety
        from unittest.mock import patch
        self.assertTrue(callable(getattr(safety, 'parse_xml', None)), 'safe XML API absent')
        with patch.object(safety, 'MAX_XML_BYTES', 3, create=True), self.assertRaises(ValueError):
            safety.parse_xml(b'<r/>')
        with patch.object(safety, 'MAX_XML_DEPTH', 2, create=True), self.assertRaises(ValueError):
            safety.parse_xml(b'<r><a><b/></a></r>')

    def test_atomic_failure_preserves_existing_and_cleans_stage(self):
        import document_safety as safety
        self.assertTrue(callable(getattr(safety, 'atomic_output_path', None)), 'atomic API absent')
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest = Path(tmp) / 'out.hwpx'
            dest.write_bytes(b'original')
            with self.assertRaises(RuntimeError):
                with safety.atomic_output_path(dest) as stage:
                    self.assertEqual(stage.parent, dest.parent)
                    stage.write_bytes(b'partial')
                    raise RuntimeError('injected failure')
            self.assertEqual(dest.read_bytes(), b'original')
            self.assertEqual(list(Path(tmp).iterdir()), [dest])
            with safety.atomic_output_path(dest) as stage:
                stage.write_bytes(b'new')
            self.assertEqual(dest.read_bytes(), b'new')

    def test_xml_node_budget_stops_before_parsing_later_input(self):
        import document_safety as safety
        from unittest.mock import patch
        data = b'<r><a/><b/>' + b' ' * 100 + b'<invalid'
        with patch.object(safety, 'MAX_XML_NODES', 2), patch.object(safety, 'CHUNK_BYTES', 4):
            with self.assertRaisesRegex(ValueError, 'node/depth limit'):
                safety.parse_xml(data)

    def test_extraction_does_not_modify_existing_hardlink_target(self):
        import document_safety as safety
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            root = Path(tmp)
            outside = root / 'outside'
            outside.write_bytes(b'keep')
            output = root / 'out'
            output.mkdir()
            os.link(outside, output / 'member')
            stream = io.BytesIO()
            with ZipFile(stream, 'w') as zf:
                zf.writestr('member', b'new')
            with ZipFile(stream) as zf:
                safety.safe_extract(zf, output)
            self.assertEqual(outside.read_bytes(), b'keep')
            self.assertEqual((output / 'member').read_bytes(), b'new')

    def test_input_output_alias_rejected(self):
        import document_safety as safety
        self.assertTrue(callable(getattr(safety, 'require_distinct_paths', None)), 'distinct API absent')
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            src = Path(tmp) / 'src'
            src.write_bytes(b'x')
            alias = Path(tmp) / 'alias'
            os.link(src, alias)
            for dest in [src, src.parent / '.' / src.name, alias]:
                with self.subTest(dest=dest), self.assertRaises(ValueError):
                    safety.require_distinct_paths(src, dest)

    def test_atomic_replace_failure_preserves_existing(self):
        import document_safety as safety
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest = Path(tmp) / 'out'
            dest.write_bytes(b'keep')
            with patch.object(safety.os, 'replace', side_effect=OSError('injected replace failure')):
                with self.assertRaises(OSError):
                    with safety.atomic_output_path(dest) as staged:
                        staged.write_bytes(b'new')
            self.assertEqual(dest.read_bytes(), b'keep')
            self.assertEqual(list(Path(tmp).iterdir()), [dest])

    def test_checked_stream_requests_are_bounded(self):
        import document_safety as safety
        from unittest.mock import patch
        requests = []
        class BoundedStream(io.BytesIO):
            def read(self, n=-1):
                requests.append(n)
                return super().read(n)
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as zf:
            zf.writestr('x', b'0123456789')
        with ZipFile(stream) as zf, patch.object(zf, 'open', return_value=BoundedStream(b'0123456789')):
            with patch.object(safety, 'CHUNK_BYTES', 3):
                self.assertEqual(safety.read_checked_member(zf, 'x'), b'0123456789')
        self.assertTrue(requests)
        self.assertTrue(all(0 < n <= 3 for n in requests))

if __name__ == '__main__':
    unittest.main()
