"""Cooperative-policy regressions. Offline only: never activate live COM."""
import sys
import contextlib
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hancom_window_guard import HancomWindowGuard, WindowsWindowBackend, WindowGuardError
from hancom_runtime import create_fresh_hwp


class Backend:
    def __init__(self):
        self.pids = set()
        self.windows = []
        self.visible = []
        self.mapping = {42: 11}
        self.current = True
        self.hidden = []
        self.identity = NS(handle=object(), pid=11, hwnd=42,
                           is_current=lambda: self.current, close=Mock(),
                           wait_for_exit=Mock(return_value=True))
        self.hold_process_identity = Mock(return_value=self.identity)
        self.create_owner_job = Mock(side_effect=AssertionError('no native job'))
        self.acquire_isolated_owner = Mock(side_effect=AssertionError('no fabricated isolation'))
        self.uninstall_hook = Mock()
    def hwp_pids(self): return set(self.pids)
    def window_pid(self, hwnd): return self.mapping.get(hwnd, 0)
    def is_hwp_process(self, pid): return pid in self.pids
    def top_windows(self): return list(self.windows)
    def visible_top_windows(self): return list(self.visible)
    def hide_window(self, hwnd): self.hidden.append(hwnd)
    def install_show_hook(self, callback): return 'hook'
    def pump_messages(self): pass


class Vendor:
    def __init__(self): raise AssertionError('vendor init')
    def __del__(self): raise AssertionError('vendor destructor')
    def quit(self): self.hwp.Quit()
    def open(self, path): return self.hwp.Open(path)


class ExclusiveUseTests(unittest.TestCase):
    def test_native_identity_uses_query_handle_not_job_and_closes_once(self):
        backend = object.__new__(WindowsWindowBackend)
        def image(handle, flags, buffer, size): buffer.value = 'C:/Hancom/Hwp.exe'; return True
        backend.kernel32 = NS(OpenProcess=Mock(return_value=123),
                              QueryFullProcessImageNameW=Mock(side_effect=image),
                              WaitForSingleObject=Mock(return_value=258), CloseHandle=Mock())
        backend.window_pid = Mock(return_value=11)
        identity = backend.hold_process_identity(11, 42)
        self.assertTrue(identity.is_current())
        backend.kernel32.OpenProcess.assert_called_once_with(0x1000 | 0x100000, False, 11)
        backend.kernel32.WaitForSingleObject.return_value = 0
        self.assertFalse(identity.is_current())
        identity.close(); identity.close()
        backend.kernel32.CloseHandle.assert_called_once_with(123)

    def raw(self):
        return NS(XHwpWindows=NS(Count=1, Active_XHwpWindow=NS(WindowHandle=42)),
                  XHwpDocuments=NS(Count=1), Quit=Mock(), Open=Mock(return_value=True))

    def test_normal_binding_holds_identity_without_claiming_isolation(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            guard.ensure_activation_supported()
            backend.pids = {11}; backend.windows = [42]
            self.assertEqual(guard.bind_owner(NS(hwp=self.raw())), 11)
            self.assertIn(42, backend.hidden)
            self.assertIsNone(guard._owner_job)
            self.assertFalse(getattr(backend.identity, 'dedicated', False))
        backend.identity.close.assert_called_once_with()
        backend.acquire_isolated_owner.assert_not_called()
        backend.create_owner_job.assert_not_called()

    def test_bad_hwnd_and_ambiguous_activation_never_hide(self):
        for kind in ('bad-hwnd', 'extra-process', 'extra-window', 'extra-document', 'stale'):
            with self.subTest(kind=kind):
                backend = Backend()
                with (self.assertRaises(WindowGuardError) if kind != 'bad-hwnd' else contextlib.nullcontext()), HancomWindowGuard(backend, exclusive_use=True) as guard:
                    guard.ensure_activation_supported()
                    backend.pids = {11}; backend.windows = [42]
                    raw = self.raw()
                    if kind == 'bad-hwnd': raw.XHwpWindows.Active_XHwpWindow.WindowHandle = 0
                    if kind == 'extra-process': backend.pids.add(22)
                    if kind == 'extra-window':
                        backend.windows.append(43); backend.mapping[43] = 11
                        backend.visible.append(43)
                    if kind == 'extra-document': raw.XHwpDocuments.Count = 2
                    if kind == 'stale': backend.current = False
                    with self.assertRaises(WindowGuardError): guard.bind_owner(NS(hwp=raw))
                    self.assertEqual(backend.hidden, [])
                if backend.hold_process_identity.called:
                    backend.identity.close.assert_called_once_with()

    def test_hidden_native_auxiliary_windows_are_not_extra_documents(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            guard.ensure_activation_supported()
            backend.pids = {11}
            backend.windows = [42, 43, 44]
            backend.mapping.update({43: 11, 44: 11})
            raw = self.raw()
            self.assertEqual(guard.bind_owner(NS(hwp=raw)), 11)
            guard.authorize_close(NS(hwp=raw))
            self.assertTrue(all(hwnd == 42 for hwnd in backend.hidden))
            backend.create_owner_job.assert_not_called()

    def test_startup_occupied_refuses_before_hook_or_activation(self):
        backend = Backend(); backend.pids = {99}
        guard = HancomWindowGuard(backend, exclusive_use=True)
        with self.assertRaisesRegex(WindowGuardError, 'exclusive-use'):
            guard.arm()
        self.assertIsNone(guard._thread)
        self.assertEqual(backend.hidden, [])

    def test_late_arrival_refuses_preflight_without_isolation_lease(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            backend.pids = {99}
            with self.assertRaisesRegex(WindowGuardError, 'exclusive-use'):
                guard.ensure_activation_supported()
        backend.acquire_isolated_owner.assert_not_called()


    def test_poll_detects_transient_new_process_without_visible_windows(self):
        import time
        backend = Backend()
        with self.assertRaises(WindowGuardError), HancomWindowGuard(backend, exclusive_use=True, poll_interval=0.001) as guard:
            raw, com, client = self.factory(backend, guard)
            session = self.create(guard, com, client)
            backend.pids.add(22)
            deadline = time.monotonic() + 1
            while guard._policy_violation is None and time.monotonic() < deadline:
                time.sleep(0.002)
            self.assertIsNotNone(guard._policy_violation)
            backend.pids.remove(22)
            with self.assertRaisesRegex(WindowGuardError, 'Quit refused'): session.close()
            raw.Quit.assert_not_called()

    def test_com_authorization_direct_wrong_thread_is_refused_before_com_access(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard)
            session = self.create(guard, com, client)
            errors = []
            def other():
                try: guard.authorize_close(session)
                except WindowGuardError as exc: errors.append(str(exc))
            thread = threading.Thread(target=other); thread.start(); thread.join()
            self.assertEqual(len(errors), 1)
            self.assertIn('thread', errors[0])
            session.close()

    def test_detected_policy_violation_stops_health_checks(self):
        backend = Backend()
        with self.assertRaises(WindowGuardError), HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard)
            session = self.create(guard, com, client)
            backend.pids.add(22)
            guard._consider_window(0)
            with self.assertRaisesRegex(WindowGuardError, 'exclusive-use'): guard.ensure_healthy()
            with self.assertRaises(WindowGuardError): session.close()
            raw.Quit.assert_not_called()

    def test_makepy_failure_and_quit_failure_release_handles_and_apartment(self):
        for kind in ('makepy', 'quit'):
            with self.subTest(kind=kind):
                backend = Backend()
                with (self.assertRaises(WindowGuardError) if kind == 'quit' else contextlib.nullcontext()), HancomWindowGuard(backend, exclusive_use=True) as guard:
                    raw, com, client = self.factory(backend, guard)
                    if kind == 'makepy':
                        client.gencache.EnsureDispatch.side_effect = RuntimeError('makepy failure')
                        with self.assertRaisesRegex(RuntimeError, 'makepy failure'): self.create(guard, com, client)
                    else:
                        raw.Quit.side_effect = RuntimeError('quit failure')
                        session = self.create(guard, com, client)
                        with self.assertRaisesRegex(RuntimeError, 'quit failure'): session.close()
                        session.close()
                    raw.Quit.assert_called_once_with()
                    backend.identity.close.assert_called_once_with()
                    com.CoUninitialize.assert_called_once_with()

    def test_refused_close_reports_even_when_quit_safely_swallows(self):
        import contextlib
        import io
        from hancom_security import quit_safely
        backend = Backend()
        with self.assertRaises(WindowGuardError), HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard)
            session = self.create(guard, com, client)
            raw.XHwpDocuments.Count = 2
            output = io.StringIO()
            with contextlib.redirect_stderr(output): quit_safely(session)
            self.assertIn('Quit refused', output.getvalue())
            raw.Quit.assert_not_called()
            self.assertIsNone(session.hwp)
            com.CoUninitialize.assert_called_once_with()

    def test_close_refusal_traceback_does_not_retain_raw_com(self):
        backend = Backend()
        with self.assertRaises(WindowGuardError), HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard)
            session = self.create(guard, com, client)
            raw.XHwpDocuments.Count = 2
            try: session.close()
            except WindowGuardError as exc:
                chain = exc
                while chain is not None:
                    tb = chain.__traceback__
                    while tb is not None:
                        if tb.tb_frame.f_code.co_name == '_check_cooperative_com':
                            self.assertNotIn('raw', tb.tb_frame.f_locals)
                        tb = tb.tb_next
                    chain = chain.__cause__
            else: self.fail('close must refuse')

    def test_invalid_identity_coordinates_refuse_before_hide(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            backend.pids = {11}; backend.windows = [42]
            backend.identity.hwnd = 43
            backend.windows = []
            backend.mapping[43] = 11
            with self.assertRaises(WindowGuardError): guard.bind_owner(NS(hwp=self.raw()))
            self.assertEqual(backend.hidden, [])
        backend.identity.close.assert_called_once_with()

    def test_unreadable_com_inventory_is_clear_refusal_before_hide(self):
        backend = Backend()
        with self.assertRaises(WindowGuardError), HancomWindowGuard(backend, exclusive_use=True) as guard:
            backend.pids = {11}; backend.windows = [42]
            raw = self.raw(); del raw.XHwpDocuments.Count
            with self.assertRaisesRegex(WindowGuardError, 'exclusive-use.*inventory'):
                guard.bind_owner(NS(hwp=raw))
            self.assertEqual(backend.hidden, [])
        backend.identity.close.assert_called_once_with()

    def test_ambiguous_factory_activation_refuses_before_wrapper_or_mutation(self):
        backend = Backend()
        with self.assertRaises(WindowGuardError), HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard)
            raw.RegisterModule = Mock()
            def activate(*args, **kwargs):
                backend.pids = {11, 22}; backend.windows = [42]; return raw
            client.DispatchEx.side_effect = activate
            with self.assertRaises(WindowGuardError): self.create(guard, com, client)
            client.gencache.EnsureDispatch.assert_not_called()
            raw.Open.assert_not_called(); raw.RegisterModule.assert_not_called(); raw.Quit.assert_not_called()
            self.assertEqual(backend.hidden, [])
            backend.identity.close.assert_called_once_with()
            com.CoUninitialize.assert_called_once_with()

    def factory(self, backend, guard, *, during_init=None):
        raw = self.raw()
        com = NS(COINIT_APARTMENTTHREADED=2, CLSCTX_LOCAL_SERVER=4,
                 CoInitializeEx=Mock(side_effect=during_init), CoUninitialize=Mock())
        def activate(*args, **kwargs):
            backend.pids = {11}; backend.windows = [42]; return raw
        client = NS(DispatchEx=Mock(side_effect=activate),
                    gencache=NS(EnsureDispatch=Mock(side_effect=lambda obj: obj)))
        return raw, com, client

    def create(self, guard, com, client):
        return create_fresh_hwp(Vendor, guard=guard, pythoncom_module=com,
                                client=client, version='1.7.2', fonts=())

    def test_immediately_before_com_creation_rechecks_after_apartment_init(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard, during_init=lambda _: backend.pids.add(99))
            with self.assertRaisesRegex(WindowGuardError, 'exclusive-use'):
                self.create(guard, com, client)
            client.DispatchEx.assert_not_called()
            raw.Quit.assert_not_called()
            com.CoUninitialize.assert_called_once_with()

    def test_normal_cleanup_open_quit_and_repeated_close_exactly_once(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard)
            session = self.create(guard, com, client)
            self.assertTrue(session.open('synthetic.hwpx'))
            session.close(); session.close(); session.__del__()
            raw.Quit.assert_called_once_with()
            com.CoUninitialize.assert_called_once_with()
            self.assertIsNone(session.hwp)
            backend.identity.close.assert_called_once_with()
        backend.identity.close.assert_called_once_with()

    def test_close_violation_refuses_quit_but_releases_lifecycle(self):
        for kind in ('new-process', 'additional-document', 'additional-window', 'identity-loss'):
            with self.subTest(kind=kind):
                backend = Backend()
                with self.assertRaises(WindowGuardError), HancomWindowGuard(backend, exclusive_use=True) as guard:
                    raw, com, client = self.factory(backend, guard)
                    session = self.create(guard, com, client)
                    if kind == 'new-process': backend.pids.add(22)
                    if kind == 'additional-document': raw.XHwpDocuments.Count = 2
                    if kind == 'additional-window': raw.XHwpWindows.Count = 2
                    if kind == 'identity-loss': backend.current = False
                    with self.assertRaisesRegex(WindowGuardError, 'Quit refused'):
                        session.close()
                    session.close(); session.__del__()
                    raw.Quit.assert_not_called()
                    com.CoUninitialize.assert_called_once_with()
                    backend.identity.close.assert_called_once_with()
                    self.assertIsNone(session.hwp)

    def test_close_authorization_only_on_creating_thread(self):
        backend = Backend()
        with HancomWindowGuard(backend, exclusive_use=True) as guard:
            raw, com, client = self.factory(backend, guard)
            session = self.create(guard, com, client)
            thread_ids = []
            original = guard.authorize_close
            def authorize(holder):
                thread_ids.append(threading.get_ident()); return original(holder)
            guard.authorize_close = authorize
            failures = []
            def wrong_thread():
                try: session.close()
                except Exception as exc: failures.append(exc)
            thread = threading.Thread(target=wrong_thread); thread.start(); thread.join()
            self.assertEqual(len(failures), 1)
            self.assertEqual(thread_ids, [])
            session.close()
            self.assertEqual(thread_ids, [threading.get_ident()])


if __name__ == '__main__': unittest.main()
