"""Pack/build preservation and bounded structural checks on synthetic input."""
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_hwpx
from office.pack import pack
from validate import validate

SCRATCH = Path.home() / 'AppData/Local/hermes/cache/scratch/hwpx-implementation-safety'
SCRATCH.mkdir(parents=True, exist_ok=True)
BASE = build_hwpx.BASE_DIR

class PackTests(unittest.TestCase):
    def test_pack_write_failure_preserves_output(self):
        for writer in [build_hwpx.pack_hwpx, pack]:
            with self.subTest(writer=writer.__name__), tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
                out = Path(tmp) / 'existing.hwpx'
                out.write_bytes(b'preserve')
                original = ZipFile.write
                def fail(zf, filename, arcname=None, *args, **kwargs):
                    if arcname != 'mimetype':
                        raise OSError('injected disk failure')
                    return original(zf, filename, arcname, *args, **kwargs)
                with patch.object(ZipFile, 'write', fail), self.assertRaises(OSError):
                    writer(BASE, out)
                self.assertEqual(out.read_bytes(), b'preserve')
                self.assertEqual(list(Path(tmp).iterdir()), [out])

    def test_pack_rejects_output_inside_input(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            root = Path(tmp) / 'input'
            shutil.copytree(BASE, root)
            for writer in [build_hwpx.pack_hwpx, pack]:
                with self.subTest(writer=writer.__name__), self.assertRaises(ValueError):
                    writer(root, root / 'self.hwpx')

    def test_build_final_validation_failure_preserves_output(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            out = Path(tmp) / 'existing.hwpx'
            out.write_bytes(b'preserve')
            # First pack validation passes; the build's final staged check fails.
            with patch.object(build_hwpx, 'validate_hwpx', side_effect=[[], ['injected final error']]):
                with self.assertRaises((ValueError, SystemExit)):
                    build_hwpx.build(None, None, None, None, None, out)
            self.assertEqual(out.read_bytes(), b'preserve')

    def test_invalid_package_pack_preserves_output(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            root = Path(tmp) / 'input'
            shutil.copytree(BASE, root)
            (root / 'Contents/header.xml').write_bytes(b'<wrong/>')
            out = Path(tmp) / 'out.hwpx'
            for writer in [build_hwpx.pack_hwpx, pack]:
                out.write_bytes(b'preserve')
                with self.subTest(writer=writer.__name__), self.assertRaises(ValueError):
                    writer(root, out)
                self.assertEqual(out.read_bytes(), b'preserve')

class ValidationTests(unittest.TestCase):
    def fixture(self, root, changes=None):
        changes = changes or {}
        path = root / 'synthetic.hwpx'
        with ZipFile(path, 'w') as zf:
            for file in [BASE / 'mimetype', *sorted(p for p in BASE.rglob('*') if p.is_file() and p.name != 'mimetype')]:
                name = file.relative_to(BASE).as_posix()
                zf.writestr(name, changes.get(name, file.read_bytes()), compress_type=ZIP_STORED)
        return path

    def test_base_template_passes(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            self.assertEqual(validate(self.fixture(Path(tmp))), [])

    def test_bundled_templates_pass_basic_checks(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            for template in build_hwpx.AVAILABLE_TEMPLATES:
                with self.subTest(template=template):
                    out = Path(tmp) / (template + '.hwpx')
                    build_hwpx.build(template, None, None, None, None, out)
                    self.assertEqual(validate(out), [])

    def test_wrong_roots_rejected(self):
        for member in ['Contents/header.xml', 'Contents/content.hpf', 'Contents/section0.xml']:
            with self.subTest(member=member), tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
                self.assertTrue(validate(self.fixture(Path(tmp), {member: b'<wrong/>'})))

    def test_missing_manifest_target_rejected(self):
        content = (BASE / 'Contents/content.hpf').read_bytes().replace(b'Contents/header.xml', b'Contents/missing.xml')
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            self.assertTrue(validate(self.fixture(Path(tmp), {'Contents/content.hpf': content})))

    def test_unknown_spine_id_rejected(self):
        content = (BASE / 'Contents/content.hpf').read_bytes().replace(b'idref="section0"', b'idref="missing"')
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            self.assertTrue(validate(self.fixture(Path(tmp), {'Contents/content.hpf': content})))

    def test_missing_container_rejected(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path = self.fixture(Path(tmp))
            with ZipFile(path) as src:
                entries = [(i, src.read(i)) for i in src.infolist() if i.filename != 'META-INF/container.xml']
            with ZipFile(path, 'w') as dest:
                for info, data in entries:
                    dest.writestr(info, data)
            self.assertTrue(validate(path))

    def test_dangling_style_rejected(self):
        sec = (BASE / 'Contents/section0.xml').read_bytes().replace(b'charPrIDRef="0"', b'charPrIDRef="999999"')
        self.assertNotEqual(sec, (BASE / 'Contents/section0.xml').read_bytes())
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            self.assertTrue(validate(self.fixture(Path(tmp), {'Contents/section0.xml': sec})))

    def test_illegal_table_geometry_rejected(self):
        template = b'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:tbl rowCnt="1" colCnt="1"><hp:tr><hp:tc><hp:cellAddr rowAddr="0" colAddr="0"/><hp:cellSpan rowSpan="1" colSpan="2"/></hp:tc></hp:tr></hp:tbl></hs:sec>'
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            self.assertTrue(validate(self.fixture(Path(tmp), {'Contents/section0.xml': template})))

    def test_binary_crc_corruption_is_error_not_exception(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path = self.fixture(Path(tmp), {'Preview/PrvText.txt': b'unique_crc_fixture'})
            raw = path.read_bytes().replace(b'unique_crc_fixture', b'broken_crc_fixture')
            path.write_bytes(raw)
            self.assertTrue(validate(path))

    def test_deflate_corruption_is_error_not_exception(self):
        from zipfile import ZIP_DEFLATED
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path = self.fixture(Path(tmp))
            with ZipFile(path, 'a', compression=ZIP_DEFLATED) as zf:
                zf.writestr('BinData/bad.bin', b'compress me' * 30)
            with ZipFile(path) as zf:
                info = zf.getinfo('BinData/bad.bin')
                offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
            raw = bytearray(path.read_bytes())
            raw[offset:offset + info.compress_size] = b'\xff' * info.compress_size
            path.write_bytes(raw)
            self.assertTrue(validate(path))

    def test_style_next_and_image_references(self):
        header = (BASE / 'Contents/header.xml').read_bytes().replace(b'nextStyleIDRef="0"', b'nextStyleIDRef="999999"')
        section = (BASE / 'Contents/section0.xml').read_bytes().replace(b'</hs:sec>', b'<hp:pic><hp:img binaryItemIDRef="not_in_manifest"/></hp:pic></hs:sec>')
        for changes in [{'Contents/header.xml': header}, {'Contents/section0.xml': section}]:
            with self.subTest(changes=list(changes)), tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
                self.assertTrue(validate(self.fixture(Path(tmp), changes)))

    def test_dtd_is_error(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            self.assertTrue(validate(self.fixture(Path(tmp), {'Contents/header.xml': b'<!DOCTYPE r><r/>'})))

if __name__ == '__main__':
    unittest.main()
