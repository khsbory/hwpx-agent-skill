"""Offline consent-path tests: never activate COM or inspect live windows."""
import contextlib
import inspect
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import convert_hwp_to_hwpx as converter
import export_document_to_pdf as exporter
import hancom_runtime as runtime
import insert_signature_pyhwpx as signature


class ExclusiveUseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Path.home() / 'AppData/Local/hermes/cache/scratch/exclusive-use-cli'
        cls.scratch.mkdir(parents=True, exist_ok=True)

    def check_consent_path(self, module, api_name, guard_target):
        api = getattr(module, api_name)
        parameter = inspect.signature(api).parameters.get('exclusive_use')
        self.assertIsNotNone(parameter, 'API must expose explicit consent')
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(parameter.default, False)
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            root = Path(folder)
            source = root / ('in.hwp' if module is converter else 'in.hwpx')
            output = root / ('out.pdf' if module is exporter else 'out.hwpx')
            source.write_bytes(b'synthetic')
            api_args = [source, output]
            cli_args = ['tool', '--input', str(source), '--output', str(output)]
            if module is signature:
                from PIL import Image
                from test_insert_signature_pyhwpx import make_hwpx
                make_hwpx(source)
                image = root / 'sig.png'
                Image.new('RGBA', (4, 4)).save(image)
                api_args += [image, ['(인)'], 8, 8, 1]
                cli_args += ['--signature', str(image)]
            for enabled in (False, True):
                with self.subTest(enabled=enabled):
                    guard = Mock(side_effect=RuntimeError('offline guard stop'))
                    stdout = io.StringIO()
                    kwargs = {'exclusive_use': True} if enabled else {}
                    if module is signature:
                        kwargs['guard_factory'] = guard
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(patch.object(runtime, 'load_hwp', return_value=Mock()))
                        if module is not converter:
                            stack.enter_context(patch.object(module, 'load_hwp', return_value=Mock()))
                        stack.enter_context(patch.object(module, 'hancom_automation_lock', contextlib.nullcontext))
                        if module is not signature:
                            stack.enter_context(patch(guard_target, guard))
                        stack.enter_context(contextlib.redirect_stdout(stdout))
                        with self.assertRaisesRegex(Exception, 'offline guard stop'):
                            api(*api_args, **kwargs)
                    guard.assert_called_once_with(**({'exclusive_use': True} if enabled else {}))
                    if enabled:
                        warning = stdout.getvalue()
                        self.assertIn('STAGE: exclusive-use', warning)
                        self.assertIn('all Hancom', warning)
                        self.assertIn('closed before', warning)
                        self.assertIn('no other Hancom', warning)
                        self.assertIn('during', warning)
                        self.assertIn('user cooperation', warning)
                        self.assertIn('not VM/OS isolation', warning)
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(patch.object(sys, 'argv', cli_args + (['--exclusive-use'] if enabled else [])))
                        stack.enter_context(patch.object(runtime, 'dispatch_cli', return_value=None))
                        if module is not converter:
                            stack.enter_context(patch.object(module, 'dispatch_cli', return_value=None))
                        if module is exporter:
                            stack.enter_context(patch.object(module, 'runtime_inspection_lines', return_value=[]))
                        result = (1, None, []) if module is signature else ((None, []) if module is exporter else None)
                        mocked_api = stack.enter_context(patch.object(module, api_name, return_value=result))
                        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                        self.assertEqual(module.main(), 0)
                    self.assertEqual(mocked_api.call_args.kwargs.get('exclusive_use', False), enabled)
            stdout = io.StringIO()
            with patch.object(sys, 'argv', ['tool', '--help']), contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as stopped:
                    module.main()
            self.assertEqual(stopped.exception.code, 0)
            help_text = ' '.join(stdout.getvalue().split())
            for phrase in ('--exclusive-use', 'all Hancom', 'closed before', 'no other Hancom', 'during', 'user cooperation', 'not VM/OS isolation'):
                self.assertIn(phrase, help_text)

    def test_runtime_dispatch_preserves_consent_into_worker_main(self):
        import os
        import subprocess
        for module, api_name in ((converter, 'convert_hwp_to_hwpx'), (exporter, 'export_document_to_pdf'), (signature, 'insert_signatures')):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory(dir=self.scratch) as folder:
                root = Path(folder)
                source = root / ('in.hwp' if module is converter else 'in.hwpx')
                source.write_bytes(b'synthetic')
                args = ['tool', '--input', str(source), '--output', str(root / ('out.pdf' if module is exporter else 'out.hwpx')), '--exclusive-use']
                if module is signature:
                    from test_insert_signature_pyhwpx import make_hwpx
                    make_hwpx(source)
                    args += ['--signature', str(root / 'sig.png')]
                def worker(command, *, env, timeout):
                    self.assertIn('--exclusive-use', command)
                    with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', command[1:]):
                        return subprocess.CompletedProcess(command, module.main())
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ, {'HWPX_RUNTIME_SELECTED': '0'}))
                    stack.enter_context(patch.object(sys, 'argv', args))
                    stack.enter_context(patch.object(runtime, 'find_runtime', return_value=(['synthetic-python.exe'], 'synthetic-python.exe')))
                    stack.enter_context(patch.object(runtime.subprocess, 'run', side_effect=worker))
                    result = (1, None, []) if module is signature else ((None, []) if module is exporter else None)
                    api = stack.enter_context(patch.object(module, api_name, return_value=result))
                    if module is exporter:
                        stack.enter_context(patch.object(module, 'runtime_inspection_lines', return_value=[]))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(module.main(), 0)
                api.assert_called_once()
                self.assertIs(api.call_args.kwargs['exclusive_use'], True)

    def test_dry_run_with_consent_never_dispatches_or_loads_com(self):
        from test_insert_signature_pyhwpx import make_hwpx
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            source = Path(folder) / 'in.hwpx'
            make_hwpx(source)
            with patch.object(sys, 'argv', ['tool', '--input', str(source), '--dry-run', '--exclusive-use']), patch.object(signature, 'dispatch_cli') as dispatch, patch.object(signature, 'load_hwp') as load, patch.object(signature, 'insert_signatures') as insert, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(signature.main(), 0)
            dispatch.assert_not_called()
            load.assert_not_called()
            insert.assert_not_called()

    def test_security_utilities_with_consent_never_load_com(self):
        import hancom_security as security
        for module in (exporter, signature):
            for options in (['--check-security-only'], ['--restore-security-backup', 'synthetic.json']):
                with self.subTest(module=module.__name__, options=options), contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(sys, 'argv', ['tool', '--exclusive-use', *options]))
                    dispatch = stack.enter_context(patch.object(module, 'dispatch_cli', return_value=None))
                    load = stack.enter_context(patch.object(module, 'load_hwp'))
                    inspect_security = stack.enter_context(patch.object(security, 'security_inspection_lines', return_value=[]))
                    restore = stack.enter_context(patch.object(security, 'restore_registry_backup'))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(module.main(), 0)
                    load.assert_not_called()
                    if '--restore-security-backup' in options:
                        dispatch.assert_not_called()
                        restore.assert_called_once()
                    else:
                        inspect_security.assert_called_once()
                        restore.assert_not_called()

    def test_conversion_consent_reaches_guard(self):
        self.check_consent_path(converter, 'convert_hwp_to_hwpx', 'hancom_window_guard.HancomWindowGuard')

    def test_signature_consent_reaches_guard(self):
        self.check_consent_path(signature, 'insert_signatures', None)

    def test_export_consent_reaches_guard(self):
        self.check_consent_path(exporter, 'export_document_to_pdf', 'export_document_to_pdf.HancomWindowGuard')


if __name__ == '__main__':
    unittest.main()
