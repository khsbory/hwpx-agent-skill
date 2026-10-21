"""Harmless owned Python helpers only: Windows venv/descendant lifetime."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / 'table_extract'))
import extract_tables as wrapper
from portability_support import scratch_directory


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object containment')
class OwnedWorkerTests(unittest.TestCase):
    def test_timeout_terminates_worker_and_its_owned_python_descendant(self):
        self.check_lifetime('timeout')

    def test_crash_terminates_owned_python_descendant(self):
        self.check_lifetime('crash')

    def test_supervisor_crash_closes_job_and_terminates_owned_tree(self):
        self.check_lifetime('supervisor-crash')

    def test_membership_verification_failure_never_resumes_synthetic_worker(self):
        from unittest.mock import patch
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        def denied(*args):
            ctypes.set_last_error(5)
            return False
        class Proxy:
            def __getattr__(self, name):
                return denied if name == 'IsProcessInJob' else getattr(kernel, name)
        with tempfile.TemporaryDirectory(dir=scratch_directory('owned-worker')) as folder:
            marker = Path(folder) / 'must-not-run'
            code = f'from pathlib import Path; Path({str(marker)!r}).touch()'
            with patch.object(ctypes, 'WinDLL', return_value=Proxy()):
                with self.assertRaises(OSError):
                    wrapper._run_bounded([sys.executable, '-c', code], 5)
            self.assertFalse(marker.exists())

    def test_supervisor_crash_immediately_after_create_leaves_no_suspended_orphan(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory(dir=scratch_directory('owned-worker')) as folder:
            root = Path(folder)
            manifest, ack = root / 'created', root / 'observed'
            supervisor = root / 'create-crash.py'
            supervisor.write_text(
                'import ctypes,os,sys,time\nfrom pathlib import Path\n'
                f'sys.path.insert(0,{str(SCRIPTS)!r})\n'
                'import owned_worker as owner\n'
                'real=ctypes.WinDLL("kernel32",use_last_error=True)\n'
                'class Call:\n'
                ' def __init__(self): object.__setattr__(self,"fn",real.CreateProcessW)\n'
                ' def __setattr__(self,k,v): setattr(self.fn,k,v)\n'
                ' def __call__(self,*args):\n'
                '  result=self.fn(*args)\n'
                '  if not result: return result\n'
                '  info=ctypes.cast(args[-1],ctypes.POINTER(owner._ProcessInfo)).contents\n'
                f'  p=Path({str(manifest)!r}); stage=p.with_suffix(".stage"); stage.write_text(str(info.dwProcessId)); stage.replace(p)\n'
                f'  while not Path({str(ack)!r}).exists(): time.sleep(.01)\n'
                '  os._exit(9)\n'
                'class Proxy:\n'
                ' def __getattr__(self,name): return Call() if name=="CreateProcessW" else getattr(real,name)\n'
                'ctypes.WinDLL=lambda *a,**k: Proxy()\n'
                'owner.run_windows_owned([sys.executable,"-c","raise SystemExit(0)"],5)\n')
            parent = subprocess.Popen([sys.executable, str(supervisor)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            handle = None
            try:
                deadline = time.monotonic() + 4
                while not manifest.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                handle = kernel.OpenProcess(0x100000 | 1, False, int(manifest.read_text()))
                self.assertTrue(handle)
                ack.touch()
                self.assertEqual(parent.wait(timeout=5), 9)
                self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0,
                                 'crash between CreateProcess and assignment leaked suspended helper')
            finally:
                ack.touch()
                parent.wait(timeout=5)
                if handle:
                    if kernel.WaitForSingleObject(handle, 0) == 258:
                        kernel.TerminateProcess(handle, 99)
                        kernel.WaitForSingleObject(handle, 5000)
                    kernel.CloseHandle(handle)

    def check_lifetime(self, mode):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory(dir=scratch_directory('owned-worker')) as folder:
            root = Path(folder)
            manifest = root / 'owned.json'
            ready = root / 'ready'
            ack = root / 'observed'
            child = root / 'child.py'
            child.write_text('import pathlib,sys,time,os,json\np=pathlib.Path(sys.argv[1]); stage=p.with_suffix(".stage"); stage.write_text(json.dumps(os.getpid())); stage.replace(p)\ntime.sleep(8)\n')
            helper = root / 'helper.py'
            helper.write_text(
                'import os,sys,subprocess,json,time\nfrom pathlib import Path\n'
                f'p=subprocess.Popen([sys.executable,{str(child)!r},{str(ready)!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
                f'Path({str(manifest)!r}).write_text(json.dumps([os.getpid(),p.pid]))\n'
                f'while not Path({str(ack)!r}).exists(): time.sleep(.01)\n'
                + ('raise SystemExit(7)\n' if mode == 'crash' else 'time.sleep(8)\n'))
            handles = []
            observer_errors = []
            def observe():
                try:
                    deadline = time.monotonic() + 4
                    while (not manifest.exists() or not ready.exists()) and time.monotonic() < deadline:
                        time.sleep(.01)
                    # Child PID may itself be a venv redirector: hold the actual
                    # child's handle as well, not merely the Popen PID.
                    pids = set(json.loads(manifest.read_text()) + [json.loads(ready.read_text())])
                    for pid in pids:
                        handle = kernel.OpenProcess(0x100000 | 0x1000 | 1, False, pid)
                        if not handle:
                            raise ctypes.WinError(ctypes.get_last_error())
                        handles.append(handle)
                    ack.touch()
                except Exception as exc:
                    observer_errors.append(exc)
            observer = threading.Thread(target=observe)
            observer.start()
            started = time.monotonic()
            try:
                if mode == 'timeout':
                    with self.assertRaisesRegex(RuntimeError, 'timed out'):
                        wrapper._run_bounded([sys.executable, str(helper)], 1.5)
                elif mode == 'supervisor-crash':
                    supervisor = root / 'supervisor.py'
                    supervisor.write_text(
                        'import sys,os,time,threading\nfrom pathlib import Path\n'
                        f'sys.path.insert(0, {str(SCRIPTS / "table_extract")!r})\n'
                        'import extract_tables as wrapper\n'
                        f'threading.Thread(target=lambda: wrapper._run_bounded([sys.executable,{str(helper)!r}],20)).start()\n'
                        f'while not Path({str(ack)!r}).exists(): time.sleep(.01)\n'
                        'os._exit(9)\n')
                    result = subprocess.run([sys.executable, str(supervisor)], capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 9, result.stderr)
                else:
                    result = wrapper._run_bounded([sys.executable, str(helper)], 5)
                    self.assertEqual(result.returncode, 7)
                elapsed = time.monotonic() - started
                observer.join(5)
                self.assertFalse(observer.is_alive())
                self.assertEqual(observer_errors, [])
                self.assertEqual(len(handles), len(set(json.loads(manifest.read_text()) + [json.loads(ready.read_text())])))
                self.assertLess(elapsed, 5)
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0,
                                     'owned Python worker/descendant survived')
            finally:
                observer.join(5)
                for handle in handles:
                    if kernel.WaitForSingleObject(handle, 0) == 258:
                        kernel.TerminateProcess(handle, 99)
                        kernel.WaitForSingleObject(handle, 5000)
                    kernel.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
