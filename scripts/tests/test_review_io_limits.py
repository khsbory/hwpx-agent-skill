"""RA03/04/05: synthetic-only topology, expansion, and report preflight."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / 'table_extract'))
import extract_tables as wrapper
import hwpx_table_parser as tables
import quick_fill
import extract_fill_schema as schema
import hancom_runtime

from portability_support import scratch_directory

SCRATCH = scratch_directory('hwpx-review-fix-io')


def merged(index=0):
    cell = tables.Cell(0, 0, row_span=2, col_span=2, text='한|\\\n' * 16)
    return tables.Table(index=index, n_rows=2, n_cols=2, cells=[cell], grid=[
        [cell, tables.Cell(0, 1, is_occupied=True, origin=(0, 0))],
        [tables.Cell(1, 0, is_occupied=True, origin=(0, 0)),
         tables.Cell(1, 1, is_occupied=True, origin=(0, 0))]])


class ReviewIOLimits(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_selected_worker_composes_with_real_dispatch_without_grandchild(self):
        src = self.root / 'source.hwp'
        src.write_bytes(b'synthetic')
        def bounded(cmd, timeout, **kwargs):
            self.assertEqual(timeout, 0.25)
            env = kwargs.get('env', {})
            self.assertEqual(env.get('HWPX_RUNTIME_SELECTED'), '1')
            with patch.dict(os.environ, env, clear=True), patch.object(hancom_runtime, 'find_runtime') as find, patch.object(hancom_runtime.subprocess, 'run') as spawn:
                self.assertIsNone(hancom_runtime.dispatch_cli(str(SCRIPTS / 'convert_hwp_to_hwpx.py'), ('lxml',)))
                find.assert_not_called()
                spawn.assert_not_called()
            Path(cmd[cmd.index('--output') + 1]).write_bytes(b'worker output')
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        with patch.object(wrapper, '_resolve_converter_python', return_value=[sys.executable]), patch.object(wrapper, '_run_bounded', side_effect=bounded) as run:
            wrapper.convert_hwp_to_hwpx(str(src), str(self.root), timeout=0.25)
            self.assertEqual(run.call_count, 1)

    def test_explicit_runtime_override_is_probed_not_trusted(self):
        with patch.dict(os.environ, {'PYHWPX_PYTHON': 'explicit-python'}), patch.object(hancom_runtime, 'find_runtime', return_value=(['verified-python'], 'verified-python')) as find:
            self.assertEqual(wrapper._resolve_converter_python(), ['verified-python'])
            find.assert_called_once_with(('lxml',))

    def test_document_budget_precedes_all_render_methods(self):
        parser = tables.HwpxTableParser()
        parser.tables = [merged(0), merged(1)]
        for fmt, method in [('matrix', 'to_matrix'), ('markdown', 'to_markdown'), ('grid', 'grid_dump')]:
            with self.subTest(fmt=fmt), patch.object(tables, 'MAX_RENDERED_UTF8_BYTES', 2000, create=True):
                tables._render(parser, fmt, 0)  # each table alone fits
                with patch.object(tables.Table, method, side_effect=AssertionError('allocated before preflight')):
                    with self.assertRaisesRegex(ValueError, 'rendered.*budget'):
                        tables._render(parser, fmt, None)

    def test_actual_subprocess_enters_converter_worker_not_dispatcher(self):
        src = self.root / 'original.hwp'
        src.write_bytes(b'synthetic')
        helper = self.root / 'synthetic_converter.py'
        helper.write_text(
            'import sys,os,json\nfrom pathlib import Path\n'
            f'sys.path.insert(0, {str(SCRIPTS)!r})\n'
            'import convert_hwp_to_hwpx as converter\nimport hancom_runtime\n'
            'def forbidden(*a, **k):\n    raise AssertionError("dispatcher grandchild")\n'
            'hancom_runtime.subprocess.run = forbidden\n'
            'def synthetic_worker(source, target, *, exclusive_use=False):\n'
            '    assert exclusive_use is False\n'
            '    assert os.environ.get("HWPX_RUNTIME_SELECTED") == "1"\n'
            '    import importlib.metadata as metadata\n'
            '    try: vendor = metadata.distribution("pyhwpx"); version = vendor.version; location = str(vendor.locate_file(""))\n'
            '    except metadata.PackageNotFoundError: version = location = None\n'
            '    target.write_text(json.dumps({"pid": os.getpid(), "parent": os.getppid(), "prefix": sys.prefix, "vendor": version, "location": location}))\n'
            'converter.convert_hwp_to_hwpx = synthetic_worker\n'
            'raise SystemExit(converter.main())\n', encoding='utf-8')
        results = []
        real_run = wrapper._run_bounded
        def record(*args, **kwargs):
            completed = real_run(*args, **kwargs)
            results.append(completed)
            return completed
        with patch.object(wrapper, '_resolve_converter_python', return_value=[sys.executable]), patch.object(wrapper, '_CONVERTER_PATH', str(helper)), patch.object(wrapper, '_run_bounded', side_effect=record):
            result = wrapper.convert_hwp_to_hwpx(str(src), str(self.root), timeout=10)
        topology = json.loads(Path(result).read_text())
        if os.name == 'nt':
            root_pid = results[0].owned_root_pid
            if topology['pid'] == root_pid:
                self.assertEqual(topology['parent'], os.getpid())
            else:
                # The selected venv's native redirector, not an app dispatcher.
                self.assertEqual(topology['parent'], root_pid)
        else:
            self.assertEqual(topology['parent'], os.getpid())
        self.assertNotEqual(topology['pid'], os.getpid())
        self.assertEqual(topology['prefix'], sys.prefix)
        import importlib.metadata as metadata
        try:
            vendor = metadata.distribution('pyhwpx')
        except metadata.PackageNotFoundError:
            self.assertIsNone(topology['vendor'])
            self.assertIsNone(topology['location'])
        else:
            self.assertEqual(topology['vendor'], vendor.version)
            self.assertEqual(topology['location'], str(vendor.locate_file('')))

    def test_direct_merged_methods_preflight_small_limit(self):
        for method in ['to_matrix', 'to_markdown', 'grid_dump']:
            with self.subTest(method=method), patch.object(tables, 'MAX_RENDERED_UTF8_BYTES', 64, create=True):
                with self.assertRaisesRegex(ValueError, 'rendered.*budget'):
                    getattr(merged(), method)()

    def test_normal_projection_preserved(self):
        t = merged()
        self.assertEqual(t.to_matrix(), [[t.cells[0].text] * 2] * 2)
        escaped = t.cells[0].text.replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>')
        self.assertEqual(t.to_markdown(), '| ' + escaped + ' | ' + escaped + ' |\n| --- | --- |\n| ' + escaped + ' | ' + escaped + ' |')

    def fill_paths(self):
        paths = {name: self.root / filename for name, filename in [('input', 'in.hwpx'), ('output', 'out.hwpx'), ('signed', 'signed.hwpx'), ('data', 'data.json'), ('rules', 'rules.json'), ('image', 'signature.png'), ('reference', 'reference.hwpx')]}
        for p in paths.values():
            p.write_bytes(b'KEEP')
        paths['data'].write_text(json.dumps({'signature': {'enabled': True, 'image_path': str(paths['image']), 'target_output': str(paths['signed'])}}), encoding='utf-8')
        paths['rules'].write_text('{}', encoding='utf-8')
        return paths

    def test_fill_rejects_all_protected_report_aliases_before_mutation(self):
        p = self.fill_paths()
        for name, target in p.items():
            for hardlink in [False, True]:
                with self.subTest(name=name, hardlink=hardlink):
                    report = target
                    if hardlink:
                        report = self.root / ('alias-' + name)
                        os.link(target, report)
                    before = {n: path.read_bytes() for n, path in p.items()}
                    argv = ['quick_fill', '--input', str(p['input']), '--output', str(p['output']), '--data', str(p['data']), '--rules', str(p['rules']), '--page-guard-reference', str(p['reference']), '--report-json', str(report)]
                    with patch.object(sys, 'argv', argv), patch.object(quick_fill, 'run_fixed_form_quick_fill', return_value={}) as fill, patch.object(quick_fill, 'run_signature_step', return_value={}) as sign, contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(quick_fill.main(), 1)
                        fill.assert_not_called()
                        sign.assert_not_called()
                    self.assertEqual(before, {n: path.read_bytes() for n, path in p.items()})

    def test_schema_rejects_alias_before_conversion_or_extract(self):
        src = self.root / 'in.hwp'
        src.write_bytes(b'KEEP')
        alias = self.root / 'alias.json'
        os.link(src, alias)
        for dest in [src, alias]:
            with self.subTest(dest=dest), patch.object(sys, 'argv', ['schema', str(src), '--output', str(dest)]), patch.object(schema, 'prepare_input_hwpx') as prepare, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(schema.main(), 1)
                prepare.assert_not_called()
                self.assertEqual(src.read_bytes(), b'KEEP')

    def test_safe_reports_publish_normal_json(self):
        p = self.fill_paths()
        dest = self.root / 'report.json'
        argv = ['quick_fill', '--input', str(p['input']), '--output', str(p['output']), '--data', str(p['data']), '--rules', str(p['rules']), '--report-json', str(dest)]
        with patch.object(sys, 'argv', argv), patch.object(quick_fill, 'run_fixed_form_quick_fill', return_value={'filled': 1}), patch.object(quick_fill, 'run_signature_step', return_value=None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(quick_fill.main(), 0)
        self.assertEqual(json.loads(dest.read_text(encoding='utf-8'))['fill_report'], {'filled': 1})
        with zipfile.ZipFile(p['input'], 'w') as zf:
            zf.writestr('Contents/section0.xml', '<sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:t>{{name}}</hp:t></hp:run></hp:p></sec>')
        with patch.object(sys, 'argv', ['schema', str(p['input']), '--output', str(dest)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(schema.main(), 0)
        self.assertEqual(json.loads(dest.read_text(encoding='utf-8'))['placeholder_fields'], [{'name': 'name', 'occurrences': 1}])
        self.assertEqual(list(self.root.glob('.report.json.*')), [])

    def test_reports_use_atomic_stage_and_preserve_previous_on_write_failure(self):
        p = self.fill_paths()
        dest = self.root / 'report.json'
        dest.write_bytes(b'PREVIOUS')

        def fail_stage(path, *args, **kwargs):
            if path.name.startswith('.report.json.'):
                path.write_bytes(b'PARTIAL')
                raise OSError('injected stage failure')
            self.fail('report bypassed atomic stage')
        argv = ['quick_fill', '--input', str(p['input']), '--output', str(p['output']), '--data', str(p['data']), '--rules', str(p['rules']), '--report-json', str(dest)]
        with patch.object(sys, 'argv', argv), patch.object(quick_fill, 'run_fixed_form_quick_fill', return_value={}), patch.object(quick_fill, 'run_signature_step', return_value=None), patch.object(Path, 'write_text', fail_stage), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(quick_fill.main(), 1)
        self.assertEqual(dest.read_bytes(), b'PREVIOUS')
        prepared = type('Prepared', (), {'converted': False, 'source_path': p['input'], 'resolved_path': p['input'], 'converter_python': None})()
        with patch.object(sys, 'argv', ['schema', str(p['input']), '--output', str(dest)]), patch.object(schema, 'prepare_input_hwpx', return_value=contextlib.nullcontext(prepared)), patch.object(schema, 'extract_schema', return_value={}), patch.object(Path, 'write_text', fail_stage), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(schema.main(), 1)
        self.assertEqual(dest.read_bytes(), b'PREVIOUS')
        self.assertEqual(list(self.root.glob('.report.json.*')), [])


if __name__ == '__main__':
    unittest.main()
