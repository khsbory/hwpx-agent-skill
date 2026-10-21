from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_pdf_pages as capture
import compare_rendered_pdfs as compare


class PdfToolsTests(unittest.TestCase):
    def test_invalid_page_spec_closes_document(self):
        doc = Mock()
        doc.__len__ = Mock(return_value=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / 'in.pdf'
            pdf.write_bytes(b'placeholder')
            with patch('fitz.open', return_value=doc):
                with self.assertRaises((SystemExit, ValueError)):
                    capture.capture(pdf, root / 'pages', 72, '0')
            doc.close.assert_called_once()

    def test_failed_second_open_closes_first_document(self):
        left = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('fitz.open', side_effect=[left, RuntimeError('bad second PDF')]):
                with self.assertRaisesRegex(RuntimeError, 'bad second PDF'):
                    compare.compare_pdfs(root / 'left.pdf', root / 'right.pdf', root / 'out', 72)
            left.close.assert_called_once()

    def test_capture_rejects_excessive_dpi_before_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / 'in.pdf'
            pdf.write_bytes(b'placeholder')
            with patch('fitz.open') as opened:
                with self.assertRaises((ValueError, SystemExit)):
                    capture.capture(pdf, root / 'pages', 100000)
                opened.assert_not_called()

    def test_size_mismatch_report_is_strict_json(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, width in [('left', 200), ('right', 250)]:
                with fitz.open() as doc:
                    doc.new_page(width=width, height=200)
                    doc.save(root / (name + '.pdf'))
            report = compare.compare_pdfs(root / 'left.pdf', root / 'right.pdf', root / 'out', 72)
            self.assertFalse(report['pages'][0]['same_size'])
            self.assertTrue(report['pages'][0]['has_visual_diff'])
            json.dumps(report, allow_nan=False)

    def test_comparison_report_cannot_overwrite_source(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.pdf'
            with fitz.open() as document:
                document.new_page(width=20, height=20)
                document.save(source)
            before = source.read_bytes()
            argv = ['compare_rendered_pdfs.py', '--left', str(source), '--right', str(source), '--output-dir', str(root / 'rendered'), '--report-json', str(source), '--dpi', '72']
            with patch.object(sys, 'argv', argv):
                self.assertNotEqual(compare.main(), 0)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((root / 'rendered').exists())

    def test_capture_refuses_generated_image_aliasing_source(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'page_001.png'
            with fitz.open() as document:
                document.new_page(width=20, height=20)
                source.write_bytes(document.tobytes())
            before = source.read_bytes()
            with self.assertRaises((ValueError, SystemExit)):
                capture.capture(source, root, 72)
            self.assertEqual(source.read_bytes(), before)

    def test_low_amplitude_rgb_difference_is_not_rounded_away(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, color in [('left', (0, 0, 0)), ('right', (0, 0, 1 / 255))]:
                with fitz.open() as document:
                    page = document.new_page(width=20, height=20)
                    page.draw_rect(page.rect, color=None, fill=color)
                    document.save(root / (name + '.pdf'))
            result = compare.compare_pdfs(root / 'left.pdf', root / 'right.pdf', root / 'out', 72)
            self.assertGreater(result['pages'][0]['rms'], 0)
            self.assertGreater(result['pages'][0]['diff_ratio'], 0)
            self.assertTrue(result['pages'][0]['has_visual_diff'])

    def test_capture_and_comparison_normal_paths(self):
        import fitz
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text, count in [('a', 'alpha', 1), ('b', 'beta', 1), ('c', 'alpha', 2)]:
                with fitz.open() as doc:
                    for _ in range(count):
                        doc.new_page(width=200, height=200).insert_text((20, 30), text)
                    doc.save(root / (name + '.pdf'))
            result = capture.capture(root / 'a.pdf', root / 'pages', 72)
            self.assertEqual(result['captured_pages'], 1)
            same = compare.compare_pdfs(root / 'a.pdf', root / 'a.pdf', root / 'same', 72)
            self.assertEqual(same['summary']['pages_with_diff'], 0)
            diff = compare.compare_pdfs(root / 'a.pdf', root / 'b.pdf', root / 'diff', 72)
            self.assertEqual(diff['summary']['pages_with_diff'], 1)
            count = compare.compare_pdfs(root / 'a.pdf', root / 'c.pdf', root / 'count', 72)
            self.assertFalse(count['summary']['page_count_match'])


if __name__ == '__main__':
    unittest.main()
