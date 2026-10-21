"""Offline creation/lifetime regressions: never import or activate live COM."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hancom_runtime as runtime
import hancom_security as security
from hancom_window_guard import HancomWindowGuard, WindowGuardError


class Vendor:
    def __init__(self, *args, **kwargs):
        raise AssertionError('unsafe vendor constructor was called')
    def __del__(self):
        raise AssertionError('unsafe vendor destructor was called')
    def open(self, path):
        return self.hwp.Open(path)
    def quit(self):
        return self.hwp.Quit()


class FreshFactoryTests(unittest.TestCase):
    def fixture(self, *, reject=False):
        self.events = []
        raw = NS(XHwpWindows=NS(Active_XHwpWindow=NS(WindowHandle=42)),
                 Quit=Mock(), Open=Mock(return_value=True))
        com = NS(COINIT_APARTMENTTHREADED=2, CLSCTX_LOCAL_SERVER=4,
                 CoInitializeEx=Mock(side_effect=lambda x: self.events.append('init')),
                 CoUninitialize=Mock(side_effect=lambda: self.events.append('uninit')))
        def dispatch(*args, **kwargs):
            self.events.append('create'); return raw
        client = NS(DispatchEx=Mock(side_effect=dispatch),
                    gencache=NS(EnsureDispatch=Mock(side_effect=lambda obj: obj)))
        def bind(holder):
            self.events.append('bind')
            self.assertIs(holder.hwp, raw)
            if reject: raise WindowGuardError('no positive proof')
            return 11
        guard = NS(ensure_healthy=Mock(), bind_owner=Mock(side_effect=bind))
        return raw, com, client, guard

    def create(self, com, client, guard, version='1.7.2'):
        return runtime.create_fresh_hwp(Vendor, guard=guard, pythoncom_module=com,
                                        client=client, version=version, fonts=('font',))

    def test_explicit_create_bypasses_constructor_and_balances_apartment(self):
        raw, com, client, guard = self.fixture()
        hwp = self.create(com, client, guard)
        client.DispatchEx.assert_called_once_with('HWPFrame.HwpObject', clsctx=4)
        client.gencache.EnsureDispatch.assert_called_once_with(raw)
        self.assertEqual(self.events, ['init', 'create', 'bind'])
        self.assertEqual(hwp.htf_fonts, ('font',))
        self.assertFalse(hwp.on_quit)
        self.assertTrue(hwp.open('synthetic.hwpx'))
        security.quit_safely(hwp)
        hwp.close(); hwp.__del__()
        raw.Quit.assert_called_once_with()
        com.CoUninitialize.assert_called_once_with()
        self.assertIsNone(hwp.hwp)

    def test_unowned_refusal_does_not_quit_and_releases_apartment(self):
        raw, com, client, guard = self.fixture(reject=True)
        with self.assertRaises(WindowGuardError): self.create(com, client, guard)
        raw.Quit.assert_not_called()
        com.CoUninitialize.assert_called_once_with()
        self.assertIsNone(guard.bind_owner.call_args.args[0].hwp,
                          'failed bind traceback holder must not retain COM')

    def test_owned_makepy_failure_quits_once_and_releases(self):
        raw, com, client, guard = self.fixture()
        client.gencache.EnsureDispatch.side_effect = RuntimeError('makepy failed')
        with self.assertRaisesRegex(RuntimeError, 'makepy failed'): self.create(com, client, guard)
        raw.Quit.assert_called_once_with(); com.CoUninitialize.assert_called_once_with()

    def test_quit_failure_still_releases_once(self):
        raw, com, client, guard = self.fixture()
        raw.Quit.side_effect = RuntimeError('Quit failed')
        hwp = self.create(com, client, guard)
        security.quit_safely(hwp); hwp.close(); hwp.__del__()
        raw.Quit.assert_called_once_with(); com.CoUninitialize.assert_called_once_with()

    def test_public_registration_api_orders_binding_before_mutation(self):
        from unittest.mock import patch
        raw, com, client, guard = self.fixture()
        raw.RegisterModule = Mock(side_effect=lambda **kwargs: self.events.append('register') or True)
        original = runtime.create_fresh_hwp
        def fresh(Hwp, *, guard):
            return original(Hwp, guard=guard, pythoncom_module=com, client=client,
                            version='1.7.2', fonts=())
        with patch.object(runtime, 'create_fresh_hwp', side_effect=fresh):
            hwp, backup, changes = security.create_registered_hwp(Vendor, guard=guard)
        self.assertIsNone(backup); self.assertEqual(changes, [])
        self.assertLess(self.events.index('bind'), self.events.index('register'))
        self.assertFalse(raw.XHwpWindows.Active_XHwpWindow.Visible)
        self.assertTrue(hwp.open('synthetic.hwpx'))
        security.quit_safely(hwp)
        raw.Quit.assert_called_once_with(); com.CoUninitialize.assert_called_once_with()

    def test_creation_failure_balances_apartment(self):
        raw, com, client, guard = self.fixture()
        client.DispatchEx.side_effect = RuntimeError('activation failed')
        with self.assertRaises(RuntimeError): self.create(com, client, guard)
        guard.bind_owner.assert_not_called(); raw.Quit.assert_not_called()
        com.CoUninitialize.assert_called_once_with()

    def test_initialize_failure_does_not_uninitialize(self):
        raw, com, client, guard = self.fixture()
        com.CoInitializeEx.side_effect = RuntimeError('changed mode')
        with self.assertRaises(RuntimeError): self.create(com, client, guard)
        client.DispatchEx.assert_not_called(); com.CoUninitialize.assert_not_called()

    def test_version_pin_rejects_before_activation(self):
        raw, com, client, guard = self.fixture()
        with self.assertRaises(runtime.RuntimeErrorDetail): self.create(com, client, guard, '1.7.3')
        com.CoInitializeEx.assert_not_called(); client.DispatchEx.assert_not_called()

    def test_direct_quit_balances_lifecycle(self):
        raw, com, client, guard = self.fixture()
        hwp = self.create(com, client, guard)
        hwp.quit(); hwp.close()
        raw.Quit.assert_called_once_with()
        com.CoUninitialize.assert_called_once_with()

    def test_wrong_thread_close_refused_then_owner_closes(self):
        raw, com, client, guard = self.fixture()
        hwp = self.create(com, client, guard)
        failures = []
        def other_thread():
            try: hwp.close()
            except runtime.RuntimeErrorDetail as exc: failures.append(str(exc))
        thread = threading.Thread(target=other_thread); thread.start(); thread.join()
        self.assertEqual(len(failures), 1)
        raw.Quit.assert_not_called(); com.CoUninitialize.assert_not_called()
        hwp.close(); com.CoUninitialize.assert_called_once_with()

    def test_unowned_visibility_and_registration_are_refused(self):
        raw = Mock()
        unknown = NS(hwp=raw)
        with self.assertRaises(security.HancomSecurityError): security.force_hidden(unknown)
        with self.assertRaises(security.HancomSecurityError): security.register_security_module_with_hancom(unknown)
        self.assertFalse(raw.mock_calls)

    def test_native_without_isolation_contract_refused_before_activation(self):
        raw, com, client, _guard = self.fixture()
        guard = HancomWindowGuard(backend=NS())
        guard.ensure_healthy = lambda: None
        with self.assertRaises(WindowGuardError): self.create(com, client, guard)
        client.DispatchEx.assert_not_called(); com.CoInitializeEx.assert_not_called()

    def test_unknown_quit_is_not_authorized(self):
        unknown = NS(quit=Mock())
        security.quit_safely(unknown)
        unknown.quit.assert_not_called()


class OwnershipProofTests(unittest.TestCase):
    def guard(self, backend):
        guard = HancomWindowGuard(backend=backend)
        guard.ensure_healthy = lambda: None
        return guard

    def test_nonbaseline_pid_alone_is_not_ownership(self):
        backend = NS(window_pid=lambda hwnd: 111, is_hwp_process=lambda pid: True,
                     create_owner_job=Mock(), hide_window=Mock())
        guard = self.guard(backend)
        holder = NS(hwp=NS(XHwpWindows=NS(Active_XHwpWindow=NS(WindowHandle=42))))
        with self.assertRaises(WindowGuardError): guard.bind_owner(holder)
        backend.create_owner_job.assert_not_called(); backend.hide_window.assert_not_called()

    def test_stale_held_identity_does_not_hide_reused_pid(self):
        current = [True]
        lease = NS(pid=111, hwnd=42, dedicated=True, handle=object(),
                   is_current=lambda: current[0], create_job=lambda: 77, close=Mock())
        backend = NS(window_pid=lambda hwnd: 111, is_hwp_process=lambda pid: True,
                     acquire_isolated_owner=lambda *args: lease, hide_window=Mock(), close_owner_job=Mock())
        guard = self.guard(backend)
        holder = NS(hwp=NS(XHwpWindows=NS(Active_XHwpWindow=NS(WindowHandle=42))))
        guard.bind_owner(holder)
        backend.hide_window.reset_mock(); current[0] = False
        guard._consider_window(42)
        backend.hide_window.assert_not_called(); guard.close()

    def test_verified_lease_is_released_exactly_once(self):
        lease = NS(pid=111, hwnd=42, dedicated=True, handle=object(),
                   is_current=lambda: True, create_job=Mock(return_value=77), close=Mock())
        backend = NS(window_pid=lambda hwnd: 111, is_hwp_process=lambda pid: True,
                     acquire_isolated_owner=Mock(return_value=lease), hide_window=Mock(),
                     close_owner_job=Mock())
        guard = self.guard(backend)
        holder = NS(hwp=NS(XHwpWindows=NS(Active_XHwpWindow=NS(WindowHandle=42))))
        self.assertEqual(guard.bind_owner(holder), 111)
        guard.close(); guard.close()
        lease.close.assert_called_once_with()
        backend.close_owner_job.assert_called_once_with(77)

    def test_concurrent_ambiguity_and_stale_identity_refused(self):
        for result in (None, NS(pid=111, hwnd=42, dedicated=False, handle=object(),
                               is_current=lambda: True, create_job=Mock(), close=Mock()),
                       NS(pid=111, hwnd=42, dedicated=True, handle=object(),
                          is_current=lambda: False, create_job=Mock(), close=Mock())):
            with self.subTest(result=result):
                backend = NS(window_pid=lambda hwnd: 111, is_hwp_process=lambda pid: True,
                             acquire_isolated_owner=Mock(return_value=result), hide_window=Mock())
                guard = self.guard(backend)
                holder = NS(hwp=NS(XHwpWindows=NS(Active_XHwpWindow=NS(WindowHandle=42))))
                with self.assertRaises(WindowGuardError): guard.bind_owner(holder)
                backend.hide_window.assert_not_called()
                if result is not None: result.create_job.assert_not_called()

    def test_baseline_rejected_before_positive_proof(self):
        backend = NS(window_pid=lambda hwnd: 111, is_hwp_process=lambda pid: True,
                     acquire_isolated_owner=Mock(), hide_window=Mock())
        guard = self.guard(backend); guard.baseline_pids = {111}
        holder = NS(hwp=NS(XHwpWindows=NS(Active_XHwpWindow=NS(WindowHandle=42))))
        with self.assertRaises(WindowGuardError): guard.bind_owner(holder)
        backend.acquire_isolated_owner.assert_not_called()


class VendorCompatibilityTests(unittest.TestCase):
    def test_installed_vendor_helpers_run_on_fresh_facade(self):
        import ast
        import os
        from portability_support import vendor_source
        source = vendor_source(required=('core.py', 'param_helpers.py', 'run_methods.py'))
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Hwp')
        names = {'open', 'save_as', 'find', 'MovePos', 'move_pos', 'quit', 'clear',
                 'Path', 'HAction', 'HParameterSet', 'FindDir', 'SetMessageBoxMode', 'set_message_box_mode'}
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        cls.bases = []  # Helpers below are extracted from their actual vendor modules.
        for module_name, class_name in [('param_helpers', 'ParamHelpers'), ('run_methods', 'RunMethods')]:
            helper_tree = ast.parse((source.parent / (module_name + '.py')).read_text(encoding='utf-8'))
            helper_cls = next(n for n in helper_tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
            cls.body.extend(n for n in helper_cls.body if isinstance(n, ast.FunctionDef) and n.name in names)
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
        env = {'os': os}
        exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), env)
        helper = FreshFactoryTests(); raw, com, client, guard = helper.fixture()
        raw.Path = ''
        raw.Open = Mock(return_value=True); raw.SaveAs = Mock(return_value=True)
        raw.MovePos = Mock(return_value=True); raw.FindDir = Mock(return_value=0)
        raw.SetMessageBoxMode = Mock()
        raw.XHwpDocuments = NS(Active_XHwpDocument=NS(Clear=Mock()))
        raw.HAction = NS(GetDefault=Mock(), Execute=Mock(return_value=True))
        raw.HParameterSet = NS(HFindReplace=NS(HSet=object()), HFileOpenSave=NS(HSet=object()))
        hwp = runtime.create_fresh_hwp(env['Hwp'], guard=guard, pythoncom_module=com,
                                      client=client, version='1.7.2', fonts=())
        self.assertTrue(hwp.open('C:/synthetic/input.hwpx'))
        self.assertTrue(hwp.save_as('C:/synthetic/output.hwpx'))
        self.assertTrue(hwp.save_as('C:/synthetic/output.pdf', format='PDF'))
        self.assertTrue(hwp.MovePos(2)); self.assertTrue(hwp.find('(signature)'))
        hwp.close()
        raw.Open.assert_called_once_with(filename='C:/synthetic/input.hwpx', Format='', arg='')
        raw.SaveAs.assert_called_once_with(Path='C:/synthetic/output.hwpx', Format='HWPX', arg='')
        raw.XHwpDocuments.Active_XHwpDocument.Clear.assert_called_once_with(option=1)
        raw.Quit.assert_called_once_with(); com.CoUninitialize.assert_called_once_with()


if __name__ == '__main__': unittest.main()
