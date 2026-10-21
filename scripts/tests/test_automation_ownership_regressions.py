"""Synthetic ownership boundaries: never call native OS/COM."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hancom_window_guard import HancomWindowGuard, WindowGuardError

class OwnershipTests(unittest.TestCase):
    def backend(self):
        b = Mock()
        b.window_pid.side_effect = lambda hwnd: {10:111, 20:222, 30:333}.get(hwnd, 0)
        b.is_hwp_process.return_value = True
        b.lease = SimpleNamespace(pid=222, hwnd=20, dedicated=True,
            handle=object(), is_current=Mock(return_value=True),
            create_job=Mock(return_value=object()), close=Mock())
        b.acquire_isolated_owner.return_value = b.lease
        return b

    def test_guard_contains_only_bound_pid_and_releases_job(self):
        b=self.backend();g=HancomWindowGuard(b);g.baseline_pids={111}
        hwp=SimpleNamespace(hwp=SimpleNamespace(XHwpWindows=SimpleNamespace(Active_XHwpWindow=SimpleNamespace(WindowHandle=20))))
        g.bind_owner(hwp);g.close()
        b.acquire_isolated_owner.assert_called_once_with(222, 20, frozenset({111}))
        b.lease.create_job.assert_called_once_with()
        b.close_owner_job.assert_called_once_with(b.lease.create_job.return_value)
        b.lease.close.assert_called_once_with()
        b.create_owner_job.assert_not_called()

    def test_failed_containment_never_hides_window(self):
        b=self.backend();b.lease.create_job.side_effect=WindowGuardError('containment failed')
        g=HancomWindowGuard(b);g.baseline_pids={111}
        hwp=SimpleNamespace(hwp=SimpleNamespace(XHwpWindows=SimpleNamespace(Active_XHwpWindow=SimpleNamespace(WindowHandle=20))))
        with self.assertRaisesRegex(WindowGuardError, 'containment failed'):g.bind_owner(hwp)
        b.hide_window.assert_not_called()
        b.lease.create_job.assert_called_once_with()
        b.lease.close.assert_called_once_with()

    def test_unhook_failure_prevents_successful_context_exit(self):
        b=self.backend();b.hwp_pids.return_value=set();b.visible_top_windows.return_value=[]
        b.uninstall_hook.side_effect=RuntimeError('unhook failed')
        with self.assertRaises(WindowGuardError):
            with HancomWindowGuard(b): pass

    def test_unbound_guard_never_hides_new_user_window(self):
        b = self.backend(); g = HancomWindowGuard(b); g.baseline_pids = {111}
        g._consider_window(20)
        b.hide_window.assert_not_called()

    def test_binding_exact_session_does_not_hide_other_new_hwp(self):
        b = self.backend(); g = HancomWindowGuard(b); g.baseline_pids = {111}
        hwp = SimpleNamespace(hwp=SimpleNamespace(XHwpWindows=SimpleNamespace(Active_XHwpWindow=SimpleNamespace(WindowHandle=20))))
        g.bind_owner(hwp)
        g._consider_window(20); g._consider_window(30)
        self.assertEqual(b.hide_window.call_args_list[-1].args, (20,))
        self.assertNotIn(30, g.hidden_windows)
        g.close()
        b.lease.close.assert_called_once_with()

    def test_binding_existing_user_process_rejected(self):
        b = self.backend(); g = HancomWindowGuard(b); g.baseline_pids = {222}
        hwp = SimpleNamespace(hwp=SimpleNamespace(XHwpWindows=SimpleNamespace(Active_XHwpWindow=SimpleNamespace(WindowHandle=20))))
        with self.assertRaises(WindowGuardError): g.bind_owner(hwp)
        b.hide_window.assert_not_called()

if __name__ == '__main__': unittest.main()
