"""Real publication paths and guard/facade; fake COM only, no native Hancom."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_exclusive_use as fixtures
Backend, Vendor = fixtures.Backend, fixtures.Vendor
from hancom_runtime import create_fresh_hwp
from hancom_window_guard import HancomWindowGuard
import export_document_to_pdf as exporter


class CleanupPublicationTests(unittest.TestCase):
    def test_swallowed_raw_quit_failure_survives_guard_exit(self):
        from hancom_security import quit_safely
        from hancom_window_guard import WindowGuardError
        backend = Backend()
        guard = HancomWindowGuard(backend, exclusive_use=True)
        with self.assertRaisesRegex(WindowGuardError, 'raw Quit failure'):
            with guard:
                raw, com, client = fixtures.ExclusiveUseTests().factory(backend, guard)
                session = create_fresh_hwp(Vendor, guard=guard, pythoncom_module=com,
                                           client=client, version='1.7.2', fonts=())
                raw.Quit.side_effect = RuntimeError('raw Quit failure')
                quit_safely(session)
                session.close(); session.__del__()
        raw.Quit.assert_called_once_with()
        backend.identity.close.assert_called_once_with()
        com.CoUninitialize.assert_called_once_with()

    def test_real_publication_paths_reject_both_cleanup_failures(self):
        import fitz
        from PIL import Image
        import test_insert_signature_pyhwpx as document_fixtures
        import insert_signature_pyhwpx as signature
        import convert_hwp_to_hwpx as converter
        import hancom_runtime as runtime
        import hancom_security as security
        import hancom_window_guard as windows
        for operation in ('exporter', 'signature', 'converter'):
            for failure in ('policy', 'raw-quit', None):
                with self.subTest(operation=operation, failure=failure), \
                     tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as folder, \
                     contextlib.ExitStack() as stack:
                    root = Path(folder)
                    source = root / ('source.hwp' if operation == 'converter' else 'source.hwpx')
                    output = root / ('out.pdf' if operation == 'exporter' else 'out.hwpx')
                    image = root / 'tmp-original-signature.png'
                    document_fixtures.make_hwpx(source)
                    Image.new('RGB', (5, 5), 'red').save(image)
                    output.write_bytes(b'previous-output')
                    originals = source.read_bytes(), image.read_bytes()
                    document = document_fixtures.FakeHwp([], [True])
                    class Facade(Vendor):
                        def MovePos(self, value): return document.MovePos(value)
                        def find(self, marker): return document.find(marker)
                        def insert_picture(self, path, **kwargs):
                            result = document.insert_picture(path, **kwargs)
                            Path(path).unlink()  # emulate destructive vendor input cleanup
                            return result
                        def save_as(self, path, **kwargs):
                            if operation == 'exporter':
                                with fitz.open() as pdf:
                                    pdf.new_page(); pdf.save(path)
                                return True
                            return document.save_as(path)
                    backend = Backend()
                    guard = HancomWindowGuard(backend, exclusive_use=True)
                    raw, com, client = fixtures.ExclusiveUseTests().factory(backend, guard)
                    authorize = guard.authorize_close
                    def late_close(session):
                        if failure == 'policy': raw.XHwpDocuments.Count = 2
                        return authorize(session)
                    guard.authorize_close = late_close
                    if failure == 'raw-quit': raw.Quit.side_effect = RuntimeError('raw Quit failure')
                    def create(*args, **kwargs):
                        return create_fresh_hwp(Facade, guard=guard, pythoncom_module=com,
                                                client=client, version='1.7.2', fonts=()), None, []
                    for module in (exporter, signature, runtime):
                        stack.enter_context(patch.object(module, 'load_hwp', return_value=Facade))
                    for module in (exporter, signature, security):
                        stack.enter_context(patch.object(module, 'create_registered_hwp', side_effect=create))
                    for module in (exporter, windows):
                        stack.enter_context(patch.object(module, 'HancomWindowGuard', return_value=guard))
                    for module in (exporter, signature, converter):
                        stack.enter_context(patch.object(module, 'hancom_automation_lock', contextlib.nullcontext))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                    def invoke():
                        if operation == 'exporter':
                            return exporter.export_document_to_pdf(source, output, exclusive_use=True)
                        if operation == 'converter':
                            return converter.convert_hwp_to_hwpx(source, output, exclusive_use=True)
                        return signature.insert_signatures(source, output, image, ['(인)'], 8, 8, 1,
                                                           guard_factory=lambda **kw: guard, exclusive_use=True)
                    if failure:
                        error = {'exporter': exporter.ExportError, 'signature': signature.SignatureError,
                                 'converter': converter.ConversionError}[operation]
                        with self.assertRaisesRegex(error, 'Quit refused' if failure == 'policy' else 'raw Quit failure'):
                            invoke()
                        self.assertEqual(output.read_bytes(), b'previous-output')
                    else:
                        invoke()
                        self.assertNotEqual(output.read_bytes(), b'previous-output')
                    self.assertEqual((source.read_bytes(), image.read_bytes()), originals)
                    self.assertEqual({p.name for p in root.iterdir()}, {source.name, output.name, image.name})
                    self.assertEqual(raw.Quit.call_count, 0 if failure == 'policy' else 1)
                    backend.identity.close.assert_called_once_with()
                    com.CoInitializeEx.assert_called_once_with(com.COINIT_APARTMENTTHREADED)
                    com.CoUninitialize.assert_called_once_with()
                    backend.create_owner_job.assert_not_called()
                    backend.acquire_isolated_owner.assert_not_called()

    def test_cleanup_failure_never_masks_primary_body_exception(self):
        from hancom_security import quit_safely
        for failure in ('policy', 'raw-quit'):
            with self.subTest(failure=failure):
                backend = Backend()
                guard = HancomWindowGuard(backend, exclusive_use=True)
                primary = ValueError('primary body failure')
                with self.assertRaises(ValueError) as caught:
                    with guard:
                        raw, com, client = fixtures.ExclusiveUseTests().factory(backend, guard)
                        session = create_fresh_hwp(Vendor, guard=guard, pythoncom_module=com,
                                                   client=client, version='1.7.2', fonts=())
                        try:
                            raise primary
                        finally:
                            if failure == 'policy': raw.XHwpDocuments.Count = 2
                            else: raw.Quit.side_effect = RuntimeError('raw Quit failure')
                            with contextlib.redirect_stderr(io.StringIO()): quit_safely(session)
                            session.close(); session.__del__()
                self.assertIs(caught.exception, primary)
                self.assertEqual(raw.Quit.call_count, 0 if failure == 'policy' else 1)
                backend.identity.close.assert_called_once_with()
                com.CoUninitialize.assert_called_once_with()

    def test_exporter_refused_cleanup_cannot_publish_independent_probe(self):
        import fitz
        class Facade(Vendor):
            def save_as(self, path, **kwargs):
                with fitz.open() as pdf:
                    pdf.new_page()
                    pdf.save(path)
                return True
        backend = Backend()
        guard = HancomWindowGuard(backend, exclusive_use=True)
        raw, com, client = fixtures.ExclusiveUseTests().factory(backend, guard)
        authorize = guard.authorize_close
        def late_document(session):
            raw.XHwpDocuments.Count = 2
            return authorize(session)
        guard.authorize_close = late_document
        def create(*args, **kwargs):
            return create_fresh_hwp(Facade, guard=guard, pythoncom_module=com,
                                    client=client, version='1.7.2', fonts=()), None, []
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as folder:
            source, output = Path(folder)/'source.hwpx', Path(folder)/'out.pdf'
            source.write_bytes(b'original-source')
            output.write_bytes(b'previous-output')
            with patch.object(exporter, 'load_hwp', return_value=Facade), \
                 patch.object(exporter, 'HancomWindowGuard', return_value=guard), \
                 patch.object(exporter, 'create_registered_hwp', side_effect=create), \
                 patch.object(exporter, 'hancom_automation_lock', contextlib.nullcontext), \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(exporter.ExportError, 'Quit refused'):
                    exporter.export_document_to_pdf(source, output, exclusive_use=True)
            self.assertEqual(output.read_bytes(), b'previous-output')
            self.assertEqual(source.read_bytes(), b'original-source')
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['out.pdf', 'source.hwpx'])
        raw.Quit.assert_not_called()
        backend.identity.close.assert_called_once_with()
        com.CoUninitialize.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
