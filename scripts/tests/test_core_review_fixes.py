"""Synthetic DATA01-04 review regressions; no native automation."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile
from lxml import etree

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from document_safety import parse_xml
import edit_section as edit

HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'
NS = {'hp': HP}
from portability_support import scratch_directory

SCRATCH = scratch_directory('hwpx-review-fix-data')

def xml(body):
    return parse_xml(f'<sec xmlns:hp="{HP}">{body}</sec>')

def para(text):
    return '<hp:p><hp:run><hp:t>'+text+'</hp:t></hp:run></hp:p>'

class DataReviewFixes(unittest.TestCase):
    def test_data01_delete_own_text_preserves_arbitrary_anchored_descendants(self):
        for kind in ('pic', 'rect', 'ellipse', 'unknownBlock'):
            with self.subTest(kind=kind):
                root = xml('<hp:p><hp:run><hp:t>DELETE</hp:t><hp:'+kind+' id="anchor"><hp:subList>'+para('NESTED')+'</hp:subList></hp:'+kind+'></hp:run></hp:p>'+para('KEEP'))
                anchor = root.find('.//hp:'+kind, NS)
                before = etree.tostring(anchor)
                self.assertEqual(edit.delete_paragraphs_containing(root, 'DELETE'), 1)
                self.assertIs(root.find('.//hp:'+kind, NS), anchor)
                self.assertEqual(etree.tostring(anchor), before)
                self.assertNotIn('DELETE', ''.join(root.itertext()))
                self.assertIn('KEEP', ''.join(root.itertext()))

    def test_data01_delete_does_not_clear_anchored_block_text_descendants(self):
        root = xml('<hp:p><hp:run><hp:t>DELETE</hp:t><hp:unknownBlock><hp:t>OBJECT TEXT</hp:t></hp:unknownBlock></hp:run></hp:p>'+para('KEEP'))
        block = root.find('.//hp:unknownBlock', NS)
        before = etree.tostring(block)
        self.assertEqual(edit.delete_paragraphs_containing(root, 'DELETE'), 1)
        self.assertEqual(etree.tostring(block), before)

    def test_data02_refuses_geometry_before_column_range(self):
        import analyze_template as analysis
        from unittest.mock import patch
        for rows, cols in (('1', '100000'), ('100000', '2'), ('-1', '1'), ('1', '0')):
            with self.subTest(rows=rows, cols=cols):
                table = xml(f'<hp:tbl rowCnt="{rows}" colCnt="{cols}"/>')[0]
                # A refusal after constructing the column range is too late.
                with patch.object(analysis, 'range', side_effect=AssertionError('geometry reached allocation'), create=True):
                    with self.assertRaises(ValueError):
                        analysis.analyze_table(table)

    def test_data02_refuses_width_output_before_column_range(self):
        import analyze_template as analysis
        from unittest.mock import patch
        table = xml('<hp:tbl rowCnt="1" colCnt="1"><hp:tr><hp:tc><hp:cellAddr colAddr="0"/><hp:cellSz width="'+'1'*1000001+'"/></hp:tc></hp:tr></hp:tbl>')[0]
        with patch.object(analysis, 'range', side_effect=AssertionError('output reached allocation'), create=True):
            with self.assertRaises(ValueError):
                analysis.analyze_table(table)

    def test_data03_existing_font_latin_generates_distinct_latin_mapping(self):
        import create_rich_document as rich
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest = Path(tmp)/'latin.hwpx'
            with contextlib.redirect_stdout(io.StringIO()):
                rich.create_rich_document({'styles': {'body': {'font': 'Arial', 'font_latin': 'Times New Roman'}}, 'content': [{'text': 'LATIN'}]}, dest)
            with ZipFile(dest) as z:
                header = parse_xml(z.read('Contents/header.xml'))
            hh = {'hh': 'http://www.hancom.co.kr/hwpml/2011/head'}
            fonts = header.findall('.//hh:fontface[@lang="LATIN"]/hh:font', hh)
            latin_id = next(f.get('id') for f in fonts if f.get('face') == 'Times New Roman')
            self.assertTrue(header.xpath('.//hh:fontRef[@latin=$fid]', namespaces=hh, fid=latin_id))

    def test_data04_comments_pis_are_not_visible_text_or_edit_slots(self):
        import text_extract as ext
        import analyze_template as analysis
        root = xml(para('A<!--COMMENT--><?review PRIVATE?>B'))
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path = Path(tmp)/'comments.hwpx'
            with ZipFile(path, 'w') as z:
                z.writestr('Contents/section0.xml', etree.tostring(root))
            self.assertEqual(ext.extract_plain(path), 'AB')
            self.assertIn('<p>AB</p>', ext.extract_html(path))
        self.assertEqual(ext._get_para_text(root[0]), 'AB')
        self.assertIn('"AB"', analysis.analyze_paragraph(root[0]))
        self.assertEqual(edit.find_and_replace(root, 'COMMENT', 'LEAK'), 0)
        self.assertEqual(edit.delete_paragraphs_containing(root, 'PRIVATE'), 0)
        self.assertEqual(edit.find_and_replace(root, 'AB', 'VISIBLE'), 1)
        self.assertEqual(ext._get_para_text(root[0]), 'VISIBLE')
        self.assertIn(b'<!--COMMENT--><?review PRIVATE?>', etree.tostring(root))

if __name__ == '__main__':
    unittest.main()
