"""Offline native-isolation refusal checks; no live COM or Win32 calls.

These characterize the supported boundary, not successful native isolation.
No positive lease is invented and no production guard is weakened.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hancom_runtime
from hancom_window_guard import HancomWindowGuard, WindowsWindowBackend, WindowGuardError


class NativeIsolationResolutionTests(unittest.TestCase):
    def test_actual_windows_backend_refuses_before_any_activation(self):
        # Allocate the actual class WITHOUT loading DLLs/enumerating the desktop.
        backend = object.__new__(WindowsWindowBackend)
        guard = HancomWindowGuard(backend=backend)
        guard.ensure_healthy = lambda: None
        com, client = Mock(), Mock()
        for baseline in (set(), {101}):
            with self.subTest(baseline=baseline):
                guard.baseline_pids = baseline
                with self.assertRaisesRegex(WindowGuardError, 'session-isolation'):
                    hancom_runtime.create_fresh_hwp(
                        object, guard=guard, pythoncom_module=com,
                        client=client, version='1.7.2', fonts=(),
                    )
                self.assertEqual(com.mock_calls, [])
                self.assertEqual(client.mock_calls, [])
        self.assertIsNone(guard._owner_job)
        self.assertEqual(guard.hidden_windows, set())

    def test_pid_identity_does_not_authorize_process_containment(self):
        backend = object.__new__(WindowsWindowBackend)
        backend.window_pid = Mock(return_value=202)
        backend.is_hwp_process = Mock(return_value=True)
        backend.create_owner_job = Mock(side_effect=AssertionError('unsafe job'))
        backend.hide_window = Mock(side_effect=AssertionError('unsafe hide'))
        guard = HancomWindowGuard(backend=backend)
        raw = SimpleNamespace(XHwpWindows=SimpleNamespace(
            Active_XHwpWindow=SimpleNamespace(WindowHandle=303)))
        with self.assertRaisesRegex(WindowGuardError, 'positive.*isolation proof'):
            guard.bind_owner(SimpleNamespace(hwp=raw))
        backend.create_owner_job.assert_not_called()
        backend.hide_window.assert_not_called()
        self.assertIsNone(guard._owner_job)
        self.assertEqual(guard.hidden_windows, set())


if __name__ == '__main__':
    unittest.main()
