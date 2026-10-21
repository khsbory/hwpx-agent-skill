import ctypes
from contextlib import ExitStack
import types
from ctypes import wintypes
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hancom_window_guard as w

class NativeTests(unittest.TestCase):
    def setUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(patch.object(w, 'os', types.SimpleNamespace(name='nt')))
        contexts.enter_context(patch.object(w.ctypes, 'WinDLL', create=True))
        contexts.enter_context(patch.object(w.ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True))
        contexts.enter_context(patch.object(w.ctypes, 'get_last_error', return_value=18, create=True))
    def test_snapshot_enumeration_failure_is_not_empty_baseline(self):
        dlls=[Mock(),Mock()]
        with patch.object(w.ctypes,'WinDLL',side_effect=dlls): b=w.WindowsWindowBackend()
        b.kernel32.CreateToolhelp32Snapshot.return_value=123
        b.kernel32.Process32FirstW.return_value=False
        with patch.object(w.ctypes,'get_last_error',return_value=5):
            with self.assertRaises(w.WindowGuardError): b.hwp_pids()
        b.kernel32.CloseHandle.assert_called_once_with(123)

    def test_owned_process_job_is_kill_on_close_and_assigned_exact_pid(self):
        dlls=[Mock(),Mock()]
        with patch.object(w.ctypes,'WinDLL',side_effect=dlls): b=w.WindowsWindowBackend()
        b.kernel32.CreateJobObjectW.return_value=777
        b.kernel32.OpenProcess.return_value=888
        b.kernel32.SetInformationJobObject.return_value=True
        b.kernel32.AssignProcessToJobObject.return_value=True
        job=b.create_owner_job(222)
        self.assertEqual(job,777)
        self.assertEqual(b.kernel32.OpenProcess.call_args.args[-1],222)
        b.kernel32.AssignProcessToJobObject.assert_called_once_with(777,888)
        b.kernel32.CloseHandle.assert_called_once_with(888)
        b.close_owner_job(job)
        self.assertEqual(b.kernel32.CloseHandle.call_args.args,(777,))

    def test_native_signatures_are_pointer_safe_without_os_calls(self):
        dlls = [Mock(), Mock()]
        with patch.object(w.ctypes, 'WinDLL', side_effect=dlls):
            b = w.WindowsWindowBackend()
        self.assertIs(b.kernel32.CreateToolhelp32Snapshot.restype, wintypes.HANDLE)
        self.assertIs(b.kernel32.OpenProcess.restype, wintypes.HANDLE)
        self.assertIs(b.user32.SetWinEventHook.restype, wintypes.HANDLE)
        for dll, names in [(b.kernel32,['CloseHandle','Process32FirstW','Process32NextW','QueryFullProcessImageNameW']), (b.user32,['GetWindowThreadProcessId','EnumWindows','ShowWindowAsync','UnhookWinEvent','PeekMessageW','DispatchMessageW'])]:
            for name in names:
                self.assertIsInstance(getattr(dll,name).argtypes, list, name)
        b.user32.SetWinEventHook.assert_not_called()
        b.kernel32.OpenProcess.assert_not_called()

if __name__=='__main__': unittest.main()
