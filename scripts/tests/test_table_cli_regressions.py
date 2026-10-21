"""Synthetic table safety/CLI regressions; no Hancom or user documents."""
import contextlib
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TABLES = ROOT / 'scripts' / 'table_extract'
sys.path.insert(0, str(TABLES))
import hwpx_table_parser as parser
import extract_tables as cli

from portability_support import scratch_directory

SCRATCH = (Path(os.environ['HWPX_TABLE_TEST_SCRATCH'])
           if os.environ.get('HWPX_TABLE_TEST_SCRATCH')
           else scratch_directory('hwpx-implementation-tables'))
SCRATCH.mkdir(parents=True, exist_ok=True)

def cell(text='OK', extra='', attrs=''):
    return ('<tc><cellAddr rowAddr="0" colAddr="0"/><cellSpan rowSpan="1" colSpan="1" ' + attrs +
            '/><subList><p><run><t>' + text + '</t></run></p>' + extra + '</subList></tc>')

def table(text='OK', extra='', rows=1, cols=1):
    return f'<tbl rowCnt="{rows}" colCnt="{cols}"><tr>{cell(text, extra)}</tr></tbl>'

class TableRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def package(self, xml=None, entries=None):
        path = self.dir / 'synthetic.hwpx'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('mimetype', 'application/hwp+zip')
            for name, data in (entries or [('Contents/section0.xml', '<sec>' + (xml or '') + '</sec>')]):
                zf.writestr(name, data)
        return path

    def command(self, script, *args):
        return subprocess.run([sys.executable, '-B', str(TABLES / script), *map(str, args)],
            capture_output=True, text=True, encoding='utf-8', timeout=15,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})

    def test_samefile_and_hardlink_output_refused(self):
        src = self.package(table())
        original = src.read_bytes()
        alias = self.dir / 'alias.hwpx'
        os.link(src, alias)
        for script in ('extract_tables.py', 'hwpx_table_parser.py'):
            for dest in (src, alias):
                with self.subTest(script=script, dest=dest.name):
                    result = self.command(script, src, '-f', 'json', '-o', dest)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('ERROR', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(src.read_bytes(), original)

    def test_resource_bounds_before_models(self):
        cases = [
            '<tbl rowCnt="600" colCnt="600"/>',
            table().replace('rowAddr="0"', 'rowAddr="999999"'),
            table().replace('rowSpan="1"', 'rowSpan="0"'),
            table().replace('colSpan="1"', 'colSpan="-1"'),
            '<tbl rowCnt="-1" colCnt="1"/>',
        ]
        for xml in cases:
            with self.subTest(xml=xml), self.assertRaisesRegex(ValueError, 'limit|span|dimension|address'):
                parser.HwpxTableParser.load(self.package(xml))

    def test_document_budgets_and_depth(self):
        cases = [
            ('MAX_DOCUMENT_SLOTS', 1, table() + table()),
            ('MAX_DOCUMENT_ROWS', 1, table() + table()),
            ('MAX_DOCUMENT_COLS', 1, table() + table()),
            ('MAX_CELLS', 1, table() + table()),
            ('MAX_TABLES', 1, table() + table()),
            ('MAX_SPAN_WORK', 1, table() + table()),
            ('MAX_NESTING', 1, table('A', table('B', table('C')))),
            ('MAX_XML_NODES', 3, table()),
        ]
        for limit, value, xml in cases:
            with self.subTest(limit=limit), mock.patch.object(parser, limit, value, create=True):
                with self.assertRaisesRegex(ValueError, 'limit|budget|depth'):
                    parser.HwpxTableParser.load(self.package(xml))

    def test_safe_xml_both_backends(self):
        malicious = '<!DOCTYPE sec [<!ENTITY x "BOUNDED">]><sec><p>&x;</p></sec>'
        for backend in (False, True):
            if backend and parser._LXML_ET is None:
                continue
            with self.subTest(lxml=backend), mock.patch.object(parser, '_HAVE_LXML', backend):
                for data in (malicious.encode(), malicious.encode('utf-16')):
                    with self.assertRaisesRegex(ValueError, 'DTD|DOCTYPE|entity'):
                        parser._parse_xml_bytes(data)
                self.assertEqual(parser._local_name(parser._parse_xml_bytes(b'<sec><!--ok--><?pi x?><p/></sec>').tag), 'sec')
                src = self.package(entries=[('Contents/header.xml', malicious), ('Contents/section0.xml', '<sec/>')])
                with self.assertRaises(ValueError):
                    parser.HwpxTableParser.load(src)

    def test_xml_bytes_checked_before_zip_read(self):
        src = self.package(table())
        original = zipfile.ZipFile.open
        seen = []
        def read(zf, name, *args, **kwargs):
            seen.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
            return original(zf, name, *args, **kwargs)
        with mock.patch.object(parser, 'MAX_XML_BYTES', 8, create=True), mock.patch.object(zipfile.ZipFile, 'open', read):
            with self.assertRaisesRegex(ValueError, 'XML.*limit|XML.*budget'):
                parser.HwpxTableParser.load(src)
        self.assertNotIn('Contents/section0.xml', seen)

    def test_nested_render_selection_and_counts(self):
        doc = parser.HwpxTableParser.load(self.package(table('OUTER', table('INNER_ONLY'))))
        for fmt in ('grid', 'markdown', 'matrix', 'json'):
            with self.subTest(fmt=fmt):
                self.assertIn('INNER_ONLY', parser._render(doc, fmt, None))
                self.assertIn('INNER_ONLY', parser._render(doc, fmt, 1))
        import json
        self.assertEqual(json.loads(doc.to_json())['tableCount'], 2)

    def test_shape_table_order_and_host_metadata(self):
        shape = '<rect><drawText><subList>' + table('BOX') + '</subList></drawText></rect>'
        doc = parser.HwpxTableParser.load(self.package(table('PARENT', shape + table('NESTED'))))
        self.assertEqual([t.index for t in doc.tables], [0, 1])
        self.assertEqual([t.cells[0].text for t in doc.iter_tables()], ['PARENT', 'BOX', 'NESTED'])
        self.assertEqual(doc.shape_texts[0].to_dict()['hostTableIndex'], 0)
        self.assertEqual(doc.shape_texts[0].to_dict()['hostCell'], [0, 0])
        self.assertEqual(doc.tables[1].to_dict()['hostCell'], [0, 0])
        self.assertEqual(doc.tables[0].cells[0].children[0].to_dict()['hostTableIndex'], 0)

    def test_reversed_numeric_section_sort_and_decoys(self):
        src = self.package(entries=[
            ('Contents/section10.xml', '<sec>' + table('TEN') + '</sec>'),
            ('unrelated/section0.xml', '<sec>' + table('DECOY') + '</sec>'),
            ('Contents/sectionevil.xml', '<sec>' + table('EVIL') + '</sec>'),
            ('Contents/section2.xml', '<sec>' + table('TWO') + '</sec>'),
        ])
        with zipfile.ZipFile(src) as zf:
            self.assertEqual(zf.namelist()[1], 'Contents/section10.xml')
        doc = parser.HwpxTableParser.load(src)
        self.assertEqual([t.cells[0].text for t in doc.tables], ['TWO', 'TEN'])

    def test_cli_input_failure_contracts(self):
        cases = [
            [('unrelated/section0.xml', '<sec/>')],
            [('Contents/section0.xml', '<wrong/>')],
            [('Contents/section0.xml', '<sec>')],
        ]
        for entries in cases:
            src = self.package(entries=entries)
            for script in ('extract_tables.py', 'hwpx_table_parser.py'):
                with self.subTest(entries=entries, script=script):
                    result = self.command(script, src, '-f', 'validate')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('ERROR', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
        # A valid, table-free section is not an input error.
        src = self.package(entries=[('Contents/section0.xml', '<sec/>')])
        self.assertEqual(self.command('extract_tables.py', src, '-f', 'validate').returncode, 0)
        with zipfile.ZipFile(src, 'w') as zf:
            zf.writestr('note.txt', 'not HWPX')
        self.assertEqual(cli.detect_format(str(src)), cli._FMT_UNKNOWN)

    def test_strict_validation_and_filters(self):
        src = self.package(table('GOOD') + table('HOLE', cols=2))
        for script in ('extract_tables.py', 'hwpx_table_parser.py'):
            with self.subTest(script=script):
                selected = self.command(script, src, '-f', 'validate', '-t', '0')
                self.assertEqual(selected.returncode, 0)
                self.assertNotIn('holes', selected.stdout)
                strict_good = self.command(script, src, '-f', 'validate', '-t', '0', '--strict')
                self.assertEqual(strict_good.returncode, 0, strict_good.stderr)
                strict_bad = self.command(script, src, '-f', 'validate', '-t', '1', '--strict')
                self.assertEqual(strict_bad.returncode, 1)
                self.assertIn('holes', strict_bad.stdout + strict_bad.stderr)
                self.assertNotIn('Traceback', strict_bad.stderr)
                missing = self.command(script, src, '-t', '99')
                self.assertNotEqual(missing.returncode, 0)
                shapes = self.command(script, src, '-f', 'shapes', '-t', '0')
                self.assertNotEqual(shapes.returncode, 0)
                self.assertIn('not supported', shapes.stderr)
        self.assertIn('INNER', parser._render(parser.HwpxTableParser.load(
            self.package(table('OUTER', table('INNER')))), 'matrix', 0))

    def test_import_does_not_probe_hwp_runtime(self):
        import types
        probe = mock.Mock(return_value=([sys.executable], 'synthetic'))
        with mock.patch.dict(sys.modules, {'quick_fill_common': types.SimpleNamespace(_find_pyhwpx_python=probe)}), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PYHWPX_PYTHON', None)
            importlib.reload(cli)
        probe.assert_not_called()

    def test_subprocess_timeouts(self):
        sleeper = self.dir / 'sleeper.py'
        sleeper.write_text('import time; time.sleep(0.2)', encoding='utf-8')
        with mock.patch.object(cli, '_import_parser', return_value=None), mock.patch.object(cli, '_PARSER_PATH', str(sleeper)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                cli.parse_and_render('synthetic.hwpx', 'json', None, timeout=0.03)
        src = self.dir / 'synthetic.hwp'
        src.write_bytes(cli._SIG_OLE)
        work = self.dir / 'work'
        work.mkdir()
        with mock.patch.object(cli, '_CONVERTER_PATH', str(sleeper)), mock.patch.object(cli, '_CONVERTER_PYTHON', [sys.executable]):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                cli.convert_hwp_to_hwpx(str(src), str(work), timeout=0.03)

    def test_output_failure_is_clean_and_conversion_temp_removed(self):
        src = self.package(table())
        for script in ('extract_tables.py', 'hwpx_table_parser.py'):
            result = self.command(script, src, '-o', self.dir)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('ERROR', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        legacy = self.dir / 'legacy.hwp'
        legacy.write_bytes(cli._SIG_OLE)
        work = self.dir / 'conversion'
        work.mkdir()
        with mock.patch.object(cli.tempfile, 'mkdtemp', return_value=str(work)), mock.patch.object(cli, 'convert_hwp_to_hwpx', return_value=str(src)):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.run([str(legacy), '-o', str(self.dir)]), 1)
        self.assertFalse(work.exists())

    def test_table_only_shapes_and_shape_xml_order(self):
        wrapped = '<rect><drawText><subList><p><run>' + table('WRAPPED') + '</run></p></subList></drawText></rect>'
        direct = '<rect><drawText><subList>' + table('DIRECT') + '</subList></drawText></rect>'
        def shape(text):
            return '<rect><drawText><subList><p><run><t>' + text + '</t></run></p></subList></drawText></rect>'
        doc = parser.HwpxTableParser.load(self.package(table('PARENT', shape('FIRST') + table('CHILD', shape('SECOND'))) + wrapped + direct))
        self.assertEqual([s.text for s in doc.shape_texts], ['FIRST', 'SECOND', '', ''])
        self.assertEqual([s.paragraphs for s in doc.shape_texts[-2:]], [[], []])
        self.assertEqual([len(s.tables) for s in doc.shape_texts[-2:]], [1, 1])
        for fmt in ('grid', 'markdown', 'matrix'):
            self.assertIn('projection', parser._render(doc, fmt, None).lower())
        self.assertIn('4 table(s) total', cli._summarize_tables(doc))

    def test_cli_missing_directory_and_broken_pipe(self):
        for path in (self.dir / 'missing.hwpx', self.dir):
            for script in ('extract_tables.py', 'hwpx_table_parser.py'):
                result = self.command(script, path)
                self.assertEqual(result.returncode, 2)
                self.assertIn('ERROR', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
        src = self.package(table())
        with mock.patch.object(parser.sys, 'stdout') as output, contextlib.redirect_stderr(io.StringIO()):
            output.write.side_effect = BrokenPipeError('synthetic pipe')
            self.assertEqual(parser.main([str(src)]), 1)

    def test_selected_hosted_table_json_and_validation_preserve_information(self):
        shape = '<rect><drawText><subList>' + table('HOSTED', cols=2) + '</subList></drawText></rect>'
        doc = parser.HwpxTableParser.load(self.package(table('PARENT', shape)))
        self.assertIn('HOSTED', parser._render(doc, 'json', 0))
        self.assertIn('holes', parser._render(doc, 'validate', 0))

    def test_fallback_xml_limits_during_construction_including_header(self):
        with mock.patch.object(parser, '_HAVE_LXML', False), mock.patch.object(parser, 'MAX_XML_NODES', 3):
            with self.assertRaisesRegex(ValueError, 'XML.*limit'):
                parser._parse_xml_bytes(b'<head><a/><b/><c/></head>')

    def test_atomic_failure_preserves_existing_output(self):
        src = self.package(table())
        output = self.dir / 'result.json'
        output.write_bytes(b'KEEP')
        import document_safety
        for module, entry in ((parser, parser.main), (cli, cli.run)):
            before = set(self.dir.iterdir())
            with mock.patch.object(document_safety.os, 'replace', side_effect=OSError('synthetic publish failure')), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(entry([str(src), '-f', 'json', '-o', str(output)]), 1)
            self.assertEqual(output.read_bytes(), b'KEEP')
            self.assertEqual(set(self.dir.iterdir()), before)

    def test_interrupt_and_parser_exception_cleanup(self):
        legacy = self.dir / 'legacy.hwp'
        legacy.write_bytes(cli._SIG_OLE)
        for error in (KeyboardInterrupt(), RuntimeError('synthetic conversion failure')):
            work = self.dir / 'conversion'
            work.mkdir()
            with mock.patch.object(cli.tempfile, 'mkdtemp', return_value=str(work)), mock.patch.object(cli, 'convert_hwp_to_hwpx', side_effect=error), contextlib.redirect_stderr(io.StringIO()):
                if isinstance(error, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        cli.run([str(legacy)])
                else:
                    self.assertEqual(cli.run([str(legacy)]), 1)
            self.assertFalse(work.exists())

    def test_legacy_suites_both_xml_backends(self):
        import re
        sys.path.insert(0, str(TABLES / 'tests'))
        import make_merge_fixture, make_shape_fixture, make_style_fixture
        import test_merge_and_nested, test_shapes, test_fixes
        for backend in (False, True):
            if backend and parser._LXML_ET is None:
                continue
            for suite, builder, expected in (
                (test_merge_and_nested, make_merge_fixture, '26'),
                (test_shapes, make_shape_fixture, '27'),
                (test_fixes, make_style_fixture, '31'),
            ):
                with self.subTest(lxml=backend, suite=suite.__name__), mock.patch.object(parser, '_HAVE_LXML', backend), mock.patch.object(builder, 'FIX_DIR', str(self.dir)):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(suite.main(), 0)
                    counts = re.search(r'=== (\d+)/(\d+) checks passed ===', output.getvalue())
                    self.assertIsNotNone(counts)
                    self.assertEqual(counts.groups(), (expected, expected))

    def test_real_closed_stdout_pipe_contract(self):
        src = self.package(table())
        for script in ('extract_tables.py', 'hwpx_table_parser.py'):
            with self.subTest(script=script):
                proc = subprocess.Popen([sys.executable, '-B', str(TABLES / script), str(src), '-f', 'json'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc.stdout.close()
                errors = proc.stderr.read().decode('utf-8')
                proc.stderr.close()
                self.assertEqual(proc.wait(timeout=15), 1, errors)
                self.assertIn('ERROR', errors)
                self.assertNotIn('Traceback', errors)

if __name__ == '__main__':
    unittest.main()
