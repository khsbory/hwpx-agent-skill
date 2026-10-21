from __future__ import annotations

import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import insert_signature_pyhwpx as signature


def make_hwpx(path: Path, text: str = "(인)") -> None:
    section = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f'<hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hs:sec>'
    )
    from lxml import etree
    base = SCRIPTS_DIR.parent / "templates" / "base"
    parts = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    section_root = etree.fromstring(parts['Contents/section0.xml'])
    ns = {'hp':'http://www.hancom.co.kr/hwpml/2011/paragraph'}
    text_nodes = section_root.xpath('.//hp:t', namespaces=ns)
    if text_nodes:
        text_nodes[0].text = text
    else:
        run = section_root.xpath('./hp:p/hp:run', namespaces=ns)[-1]
        etree.SubElement(run, '{'+ns['hp']+'}t').text = text
    parts['Contents/section0.xml'] = etree.tostring(section_root, xml_declaration=True, encoding='UTF-8')
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


class FakeHwp:
    def __init__(self, events: list[str], find_results: list[bool]) -> None:
        self._owned_session = True
        self.image_bytes = []
        self.close_calls = 0
        self.events = events
        self.find_results = iter(find_results)
        self.insert_calls: list[tuple[tuple, dict]] = []
        self.quit_calls = 0
        self.hwp = Mock()

    def open(self, path):
        self.events.append("open")
        return True

    def MovePos(self, value):
        self.events.append(f"move:{value}")

    def find(self, marker):
        self.events.append(f"find:{marker}")
        return next(self.find_results, False)

    def insert_picture(self, *args, **kwargs):
        self.events.append("insert")
        self.insert_calls.append((args, kwargs))
        self.image_bytes.append(Path(args[0]).read_bytes())

    def save_as(self, path):
        self.events.append("save")
        make_hwpx(Path(path), "signed")
        from lxml import etree
        with zipfile.ZipFile(path) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        manifest = etree.fromstring(parts['Contents/content.hpf'])
        section = etree.fromstring(parts['Contents/section0.xml'])
        opf = 'http://www.idpf.org/2007/opf/'
        hp = 'http://www.hancom.co.kr/hwpml/2011/paragraph'
        hc = 'http://www.hancom.co.kr/hwpml/2011/core'
        for index, (args, kwargs) in enumerate(self.insert_calls):
            item_id = f'image{index}'
            href = f'BinData/{item_id}.png'
            etree.SubElement(manifest.find('{'+opf+'}manifest'), '{'+opf+'}item', id=item_id, href=href, **{'media-type':'image/png'})
            picture = etree.SubElement(section.find('.//{'+hp+'}run'), '{'+hp+'}pic')
            etree.SubElement(picture, '{'+hc+'}img', binaryItemIDRef=item_id)
            parts[href] = self.image_bytes[index]
        parts['Contents/content.hpf'] = etree.tostring(manifest)
        parts['Contents/section0.xml'] = etree.tostring(section)
        with zipfile.ZipFile(path,'w') as archive:
            for name,data in parts.items(): archive.writestr(name,data)
        return True

    def quit(self):
        self.quit_calls += 1

    def close(self):
        self.close_calls += 1
        if self._owned_session:
            self._owned_session = False
            self.quit()


class FakeGuard:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self):
        self.events.append("guard-arm")
        return self

    def ensure_healthy(self):
        self.events.append("guard-check")

    def __exit__(self, *_args):
        self.events.append("guard-close")


class SignatureTests(unittest.TestCase):
    def test_image_validation_and_aspect_fit(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "signature.png"
            Image.new("RGBA", (100, 25)).save(image)
            self.assertEqual(signature.validate_signature_image(image, 8, 8, True), (8, 2))
            image.write_bytes(b"invalid")
            with self.assertRaisesRegex(signature.SignatureError, "Invalid or unsupported"):
                signature.validate_signature_image(image, 8, 8, True)

    def test_actual_search_with_no_match_preserves_output(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, image = root / "in.hwpx", root / "out.hwpx", root / "sig.png"
            make_hwpx(source)
            output.write_bytes(b"existing")
            Image.new("RGBA", (40, 20)).save(image)
            events = []
            hwp = FakeHwp(events, [False])
            with patch.dict(sys.modules, {"pyhwpx": types.SimpleNamespace(Hwp=Mock())}), patch.object(signature, "create_registered_hwp", return_value=(hwp, None, [])):
                with self.assertRaisesRegex(signature.SignatureError, "no matching marker"):
                    signature.insert_signatures(source, output, image, ["(인)"], 8, 8, 1,
                                                guard_factory=lambda: FakeGuard(events))
            self.assertEqual(output.read_bytes(), b"existing")

    def test_pyhwpx_order_arguments_multiple_markers_and_atomic_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, output, image = root / "in.hwpx", root / "out.hwpx", root / "sig.png"
            make_hwpx(source, "(인)(인)")
            from PIL import Image
            Image.new("RGBA", (40, 20), (0, 0, 0, 255)).save(image)
            events: list[str] = []
            hwp = FakeHwp(events, [True, True])

            def registered(_factory, **_kwargs):
                events.append("register")
                return hwp, None, []

            module = types.SimpleNamespace(Hwp=Mock())
            with patch.dict(sys.modules, {"pyhwpx": module}), patch.object(
                signature, "create_registered_hwp", side_effect=registered
            ):
                result = signature.insert_signatures(
                    source, output, image, ["(인)"], 9, 7, 2,
                    guard_factory=lambda: FakeGuard(events),
                )
            self.assertEqual(result, 2)
            self.assertTrue(zipfile.is_zipfile(output))
            self.assertLess(events.index("guard-arm"), events.index("register"))
            self.assertLess(events.index("register"), events.index("open"))
            self.assertEqual(len(hwp.insert_calls), 2)
            for args, kwargs in hwp.insert_calls:
                self.assertEqual(len(args), 1)
                self.assertNotEqual(Path(args[0]), image)
                self.assertTrue(Path(args[0]).name.startswith('signature-image'))
                self.assertFalse(Path(args[0]).exists())
                self.assertFalse(Path(args[0]).parent.exists())
                self.assertEqual(
                    kwargs,
                    {"embedded": True, "sizeoption": 1, "width": 9, "height": 7},
                )
            self.assertEqual(hwp.quit_calls, 1)
            self.assertEqual(hwp.close_calls, 1)
            self.assertEqual(hwp.image_bytes, [image.read_bytes()] * 2)
            self.assertEqual(list(root.glob(".*-signature-*.hwpx")), [])

    def test_fresh_hidden_factory_and_register_before_open_are_delegated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, output, image = root / "in.hwpx", root / "out.hwpx", root / "sig.png"
            make_hwpx(source)
            from PIL import Image
            Image.new("RGBA", (40, 20), (0, 0, 0, 255)).save(image)
            events: list[str] = []
            hwp = FakeHwp(events, [True, False])
            module_factory = Mock()

            def registered(factory, **kwargs):
                self.assertIs(factory, module_factory)
                self.assertFalse(kwargs["repair_security_module"])
                events.append("register")
                return hwp, None, []

            with patch.dict(sys.modules, {"pyhwpx": types.SimpleNamespace(Hwp=module_factory)}), patch.object(
                signature, "create_registered_hwp", side_effect=registered
            ):
                signature.insert_signatures(
                    source, output, image, ["(인)"], 8, 8, 1,
                    guard_factory=lambda: FakeGuard(events),
                )
            self.assertLess(events.index("register"), events.index("open"))

    def test_failure_preserves_output_and_quits_owned_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, output, image = root / "in.hwpx", root / "out.hwpx", root / "sig.png"
            make_hwpx(source)
            output.write_bytes(b"existing")
            from PIL import Image
            Image.new("RGBA", (40, 20), (0, 0, 0, 255)).save(image)
            events: list[str] = []
            hwp = FakeHwp(events, [True])
            hwp.save_as = Mock(side_effect=RuntimeError("save failed"))
            with patch.dict(sys.modules, {"pyhwpx": types.SimpleNamespace(Hwp=Mock())}), patch.object(
                signature, "create_registered_hwp", return_value=(hwp, None, [])
            ):
                with self.assertRaisesRegex(signature.SignatureError, "save failed"):
                    signature.insert_signatures(
                        source, output, image, ["(인)"], 8, 8, 1,
                        guard_factory=lambda: FakeGuard(events),
                    )
            self.assertEqual(output.read_bytes(), b"existing")
            self.assertEqual(hwp.quit_calls, 1)
            self.assertEqual(hwp.close_calls, 1)
            self.assertEqual(list(root.glob(".*-signature-*.hwpx")), [])

    def test_scan_counts_markers_in_each_paragraph_and_split_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "paragraphs.hwpx"
            xml = ('<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
                   'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                   '<hp:p><hp:run><hp:t>(서명</hp:t></hp:run><hp:run><hp:t> 또는 인)</hp:t></hp:run></hp:p>'
                   '<hp:p><hp:run><hp:t>Other paragraph</hp:t></hp:run></hp:p></hs:sec>')
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Contents/section0.xml", xml)
            self.assertEqual(signature.count_markers(source, ["(서명 또는 인)"]), {"(서명 또는 인)": 1})

    def test_dry_run_does_not_start_guard_or_com(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, output = root / "in.hwpx", root / "out.hwpx"
            make_hwpx(source)
            for output_args in ([], ["--output", str(output)]):
                argv = ["insert_signature_pyhwpx.py", "--input", str(source), *output_args, "--dry-run"]
                with patch.object(sys, "argv", argv), patch.object(signature, "insert_signatures") as insert, patch.object(signature, "dispatch_cli") as dispatch:
                    self.assertEqual(signature.main(), 0)
                insert.assert_not_called()
                dispatch.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
