"""Synthetic core regressions; no Hancom or real documents."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import edit_section as edit

HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'
HS = 'http://www.hancom.co.kr/hwpml/2011/section'
NS = {'hp': HP}
from portability_support import scratch_directory

SCRATCH = scratch_directory('hwpx-implementation-core')

def xml(body):
    return etree.fromstring(f'<hs:sec xmlns:hp="{HP}" xmlns:hs="{HS}">{body}</hs:sec>')

def para(text):
    return f'<hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>'

def table(text='CELL', span='1'):
    return '<hp:p><hp:run><hp:tbl rowCnt="1" colCnt="1"><hp:tr><hp:tc header="1"><hp:cellAddr rowAddr="0" colAddr="0"/><hp:cellSpan colSpan="'+span+'" rowSpan="1"/><hp:subList>'+para(text)+'</hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>'

def archive(path, sections, **extra):
    with ZipFile(path, 'w') as z:
        for name, root in sections.items():
            z.writestr('Contents/'+name+'.xml', etree.tostring(root))
        for name, data in extra.items():
            z.writestr(name, data)
    return str(path)

class EditRegressions(unittest.TestCase):
    def test_nested_delete_keeps_outer_table_and_valid_empty_cell(self):
        root = xml(table())
        self.assertEqual(edit.delete_paragraphs_containing(root, 'CELL'), 1)
        self.assertEqual(len(root.findall('.//hp:tbl', NS)), 1)
        self.assertEqual(len(root.findall('.//hp:subList/hp:p', NS)), 1)
        self.assertNotIn('CELL', ''.join(root.itertext()))

    def test_replace_spans_runs_and_text_tails(self):
        root = xml('<hp:p><hp:run><hp:t>마</hp:t></hp:run><hp:run><hp:t>커<hp:tab/>끝</hp:t></hp:run></hp:p>')
        self.assertEqual(edit.find_and_replace(root, '마커', '새말'), 1)
        self.assertEqual(edit.find_and_replace(root, '끝', '완료'), 1)
        self.assertEqual(''.join(root.itertext()), '새말완료')
        self.assertEqual(len(root.findall('.//hp:tab', NS)), 1)

    def test_delete_preserves_section_properties(self):
        root = xml('<hp:p><hp:run><hp:secPr/><hp:t>remove</hp:t></hp:run></hp:p>')
        edit.delete_paragraphs_containing(root, 'remove')
        self.assertEqual(len(root.findall('.//hp:secPr', NS)), 1)
        self.assertNotIn('remove', ''.join(root.itertext()))

class ExtractionRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.hwpx'

    def test_preserves_duplicate_body_and_table_identity(self):
        import text_extract as ext
        path = archive(self.path, {'section0': xml(para('CELL')+para('REPEAT')+para('REPEAT')+table())})
        html = ext.extract_html(path)
        self.assertEqual(html.count('REPEAT'), 2)
        self.assertEqual(html.count('CELL'), 2)
        data = json.loads(ext.extract_json(path))['sections'][0]['paragraphs']
        self.assertEqual([p['content'] for p in data if p['type']=='text'], ['CELL','REPEAT','REPEAT'])
        self.assertEqual(data[-1]['cells'][0][0]['colSpan'], 1)
        self.assertIn('<th', html)
        self.assertIn('lang="ko"', html)

    def test_plain_table_flag_and_inline_controls_numeric_order(self):
        import text_extract as ext
        path = archive(self.path, {'section10':xml(para('TEN')), 'section2':xml(para('TWO')), 'section0':xml(para('A<hp:tab/>B<hp:lineBreak/>C')+table())})
        with patch.object(ext, 'TextExtractor', None):
            self.assertEqual(ext.extract_plain(path), 'A\tB\nC\nTWO\nTEN')
            self.assertEqual(ext.extract_plain(path, include_tables=True).count('CELL'), 1)

    def test_spine_order_overrides_numeric_fallback(self):
        import text_extract as ext
        path = archive(self.path, {'section0':xml(para('ZERO')), 'section2':xml(para('TWO'))}, **{'Contents/content.hpf': b'<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="b" href="section2.xml"/><item id="a" href="section0.xml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>'})
        self.assertEqual(ext.extract_plain(path), 'TWO\nZERO')

    def test_html_rejects_noninteger_span(self):
        import text_extract as ext
        path = archive(self.path, {'section0': xml(table(span='2&quot; onclick=&quot;alert(1)'))})
        with self.assertRaises(ValueError):
            ext.extract_html(path)

    def test_nested_table_and_notes_emitted_once_in_order(self):
        import text_extract as ext
        root = xml(para('BEFORE<hp:ctrl><hp:footNote><hp:subList>'+para('NOTE')+'</hp:subList></hp:footNote></hp:ctrl>AFTER')+table('OUTER'))
        cell = root.find('.//hp:subList', NS)
        # This subList is the note; nested table belongs in the outer cell.
        root.findall('.//hp:subList', NS)[-1].append(xml(table('INNER'))[0])
        path = archive(self.path, {'section0':root})
        # JSON retains legacy flat rows as a compatibility view. Count the
        # authoritative cells/content tree, not both serialized views together.
        structured = json.loads(ext.extract_json(path))['sections'][0]['paragraphs']
        canonical = '\n'.join(ext._plain_items(structured, True))
        for value in (ext.extract_plain(path, include_tables=True), ext.extract_html(path), canonical):
            for token in ('BEFORE','NOTE','AFTER','OUTER','INNER'):
                self.assertEqual(value.count(token), 1, (token,value))

class GenerationRegressions(unittest.TestCase):
    def test_footer_multiple_fields_keep_all_text(self):
        import create_rich_document as rich
        node = rich._build_header_footer_ctrl_run('footer', 'A{page}B{page}C', rich.IDAllocator())
        self.assertEqual(''.join(node.itertext()), 'ABC')
        self.assertEqual(len(node.findall('.//hp:autoNum', NS)), 2)

    def test_unknown_type_and_bad_input_fail_before_output(self):
        import create_rich_document as rich
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest = Path(tmp) / 'existing.hwpx'
            for spec in ({'content':[{'type':'heading','text':'lost'}]}, {'surprise':1}, {'content':'wrong'}, {'styles':{'body':{'size':float('nan')}}}, {'page':{'margins':{'left':1000}}}):
                with self.subTest(spec=spec):
                    dest.write_bytes(b'original')
                    with self.assertRaises(ValueError):
                        rich.create_rich_document(spec, dest)
                    self.assertEqual(dest.read_bytes(), b'original')

    def test_malformed_page_selectors_raise_validation_error(self):
        import create_rich_document as rich
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            for page in ({'paper':[]},{'orientation':{}}, {'paper':None}):
                with self.subTest(page=page),self.assertRaises(ValueError):
                    rich.create_rich_document({'page':page},Path(tmp)/'out.hwpx')

    def test_invalid_table_widths_and_merges_fail(self):
        import create_rich_document as rich
        invalid = [ {'col_widths':[-10,110]}, {'col_widths':[100]}, {'col_widths':[0,100]},
                    {'merges':[{'colSpan':3}]}, {'merges':[{'row':-1}]},
                    {'merges':[{'rowSpan':0}]}, {'merges':[{'colSpan':2},{'col':1}]},
                    {'rows':True}, {'cols':0}, {'data':[['x','y','lost']]} ]
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            for extra in invalid:
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    rich.create_rich_document({'content':[dict(type='table', rows=2, cols=2, **{}) | extra]}, Path(tmp)/'out.hwpx')

class AnalysisRegressions(unittest.TestCase):
    def test_control_run_body_visible(self):
        import analyze_template as analysis
        node = xml('<hp:p><hp:run><hp:t>BODY</hp:t><hp:ctrl><hp:footNote><hp:subList>'+para('NOTE')+'</hp:subList></hp:footNote></hp:ctrl><hp:t>AFTER</hp:t></hp:run></hp:p>')[0]
        text = analysis.analyze_paragraph(node)
        self.assertIn('BODY',text)
        self.assertIn('AFTER',text)

    def test_analysis_json_has_lossless_body_structure(self):
        import analyze_template as analysis
        root = xml(para('L'*100)+table())
        header = etree.fromstring(b'<head/>')
        result = analysis._build_analysis_json(header, root, 'synthetic')
        self.assertIn('body', result)
        serialized = json.dumps(result['body'])
        self.assertIn('L'*100, serialized)
        self.assertIn('CELL', serialized)
        self.assertIn('cellSpan', serialized)

    def test_cli_json_with_extract_and_multiple_sections(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path = Path(tmp)/'input.hwpx'
            archive(path, {'section0':xml(para('ZERO')), 'section2':xml(para('TWO'))}, **{'Contents/header.xml':b'<head/>'})
            proc = subprocess.run([sys.executable,'-X','utf8',str(SCRIPTS/'analyze_template.py'),str(path),'--json','--extract-header',str(Path(tmp)/'header.xml')], capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(proc.returncode,0,proc.stderr)
            try:
                value = json.loads(proc.stdout)
            except ValueError:
                self.fail('stdout is not one JSON document: '+proc.stdout[:200])
            self.assertEqual(len(value['sections']),2)
            self.assertIn('TWO',json.dumps(value))

class GuardRegressions(unittest.TestCase):
    def test_extra_section_and_layout_changes_are_detected(self):
        import page_guard as guard
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            a,b = Path(tmp)/'a.hwpx',Path(tmp)/'b.hwpx'
            archive(a, {'section0':xml(para('same'))})
            archive(b, {'section0':xml(para('same')), 'section1':xml(para('more'))})
            self.assertTrue(guard.compare_metrics(guard.collect_metrics(a), guard.collect_metrics(b),.15,.25))
            archive(b, {'section0':xml('<hp:p pageBreak="1"><hp:run charPrIDRef="99"><hp:t>same</hp:t></hp:run></hp:p>')})
            self.assertTrue(guard.compare_metrics(guard.collect_metrics(a), guard.collect_metrics(b),.15,.25))

class PersistenceRegressions(unittest.TestCase):
    def test_pack_failure_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            work = Path(tmp)/'work'
            work.mkdir()
            (work/'mimetype').write_text('application/hwp+zip')
            dest = Path(tmp)/'original.hwpx'
            dest.write_bytes(b'original bytes')
            with patch.object(edit.ZipFile, 'write', side_effect=OSError('injected disk failure')):
                with self.assertRaises(OSError):
                    edit._pack_hwpx(work,dest)
            self.assertEqual(dest.read_bytes(), b'original bytes')

    def test_generator_invalid_staged_archive_preserves_destination(self):
        import create_rich_document as rich
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest=Path(tmp)/'original.hwpx'
            dest.write_bytes(b'original bytes')
            def corrupt(work, path):
                Path(path).write_bytes(b'not a zip')
            with patch.object(rich,'pack_hwpx',side_effect=corrupt):
                with self.assertRaises((ValueError, OSError)):
                    rich.create_rich_document({'content':[{'text':'safe'}]},dest)
            self.assertEqual(dest.read_bytes(),b'original bytes')

    def test_normal_generation_edit_analysis_composition(self):
        import create_rich_document as rich
        import text_extract as ext
        from validate import validate
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            source=Path(tmp)/'source.hwpx'
            output=Path(tmp)/'edited.hwpx'
            spec={'footer_text':'A{page}B{page}C','styles':{'body':{'size':11}}, 'content':[{'type':'paragraph','text':'본문[^1]뒤','footnotes':{'1':'각주'}},{'type':'table','rows':2,'cols':2,'headers':['제목','값'],'data':[['내용','10']],'col_widths':[30,70]},{'type':'page_break'},{'text':'끝'}]}
            rich.create_rich_document(spec,source)
            self.assertEqual(validate(str(source)),[])
            before=source.read_bytes()
            edit.edit_section(source,output,find_text='내용',replace_text='수정')
            self.assertEqual(source.read_bytes(),before)
            self.assertEqual(validate(str(output)),[])
            text=ext.extract_plain(str(output),include_tables=True)
            for token in ('본문','뒤','각주','제목','수정','10','끝'):
                self.assertEqual(text.count(token),1,text)

class BoundaryRegressions(unittest.TestCase):
    def test_legacy_cli_routes_without_optional_dependency(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest=Path(tmp)/'legacy.hwpx'
            proc=subprocess.run([sys.executable,'-X','utf8',str(SCRIPTS/'_deprecated_create_document.py'),'--output',str(dest),'--format','json'], input=json.dumps({'header':'머리','footer':'꼬리','content':[{'type':'heading','level':2,'text':'제목'},{'type':'table','rows':[['가','나']]}]}), capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertIn('DEPRECATED',proc.stderr)
            import text_extract as ext
            text=ext.extract_plain(str(dest),include_tables=True)
            for token in ('머리','꼬리','제목','가','나'):
                self.assertIn(token,text)

    def test_edit_empty_operations_reject(self):
        root=xml(para('safe'))
        with self.assertRaises(ValueError):
            edit.find_and_replace(root,'','bad')
        with self.assertRaises(ValueError):
            edit.delete_paragraphs_containing(root,'')
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            src=Path(tmp)/'source.hwpx'
            archive(src,{'section0':root})
            for kwargs in ({},{'find_text':'safe'},{'replace_text':'x'},{'find_text':'' ,'replace_text':'x'}):
                with self.subTest(kwargs=kwargs), patch.object(edit,'_unpack_hwpx',side_effect=AssertionError('invalid operation reached unpack')), self.assertRaises(ValueError):
                    edit.edit_section(src,Path(tmp)/'out.hwpx',**kwargs)

    def test_html_heading_only_from_declared_outline_semantics(self):
        import text_extract as ext
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            header=b'<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"><hh:paraPr id="7"><hh:heading type="OUTLINE" level="1"/></hh:paraPr></hh:head>'
            root=xml('<hp:p paraPrIDRef="7"><hp:run><hp:t>TITLE</hp:t></hp:run></hp:p>'+para('BODY'))
            path=archive(Path(tmp)/'heading.hwpx',{'section0':root}, **{'Contents/header.xml':header})
            self.assertIn('<h2>TITLE</h2>',ext.extract_html(path))
            self.assertIn('<p>BODY</p>',ext.extract_html(path))

    def test_generated_table_header_is_semantic_html(self):
        import create_rich_document as rich
        import text_extract as ext
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest=Path(tmp)/'header.hwpx'
            rich.create_rich_document({'content':[{'type':'table','headers':['HEADING','VALUE']}]},dest)
            html=ext.extract_html(str(dest))
            self.assertIn('<th scope="col"><p>HEADING</p></th>',html)

    def test_guard_ignores_xml_indentation_in_text_counts(self):
        import page_guard as guard
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            root=xml(para('ABC')+table('DEF'))
            path=Path(tmp)/'pretty.hwpx'
            with ZipFile(path,'w') as z:
                z.writestr('Contents/section0.xml',etree.tostring(root,pretty_print=True))
            metrics=guard.collect_metrics(path)
            self.assertEqual(metrics.text_char_total,6)
            self.assertEqual(metrics.paragraph_text_lengths,[3,0,3])

    def test_invalid_explicit_spine_is_not_silently_reordered(self):
        import text_extract as ext
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path=archive(Path(tmp)/'invalid-spine.hwpx',{'section0':xml(para('ZERO'))}, **{'Contents/content.hpf':b'<package xmlns="http://www.idpf.org/2007/opf/"><manifest/><spine><itemref idref="missing"/></spine></package>'})
            with self.assertRaisesRegex(ValueError,'spine'):
                ext.extract_plain(path)

    def test_spine_real_hwpx_namespace_root_relative_hrefs(self):
        import text_extract as ext
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            path=archive(Path(tmp)/'spine.hwpx',{'section0':xml(para('ZERO')),'section2':xml(para('TWO'))}, **{'Contents/content.hpf':b'<package xmlns="http://www.idpf.org/2007/opf/"><manifest><item id="two" href="Contents/section2.xml"/><item id="zero" href="Contents/section0.xml"/></manifest><spine><itemref idref="two"/><itemref idref="zero"/></spine></package>'})
            self.assertEqual(ext.extract_plain(path),'TWO\nZERO')

if __name__ == '__main__':
    unittest.main()
