from __future__ import annotations

import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hancom_window_guard as window_guard


class FakeBackend:
    def __init__(self, *, fail_hook: bool = False) -> None:
        self.fail_hook = fail_hook
        self.pids = {10}
        self.windows = [100, 200, 300]
        self.window_pids = {100: 10, 200: 20, 300: 30}
        self.hwp_processes = {10, 20}
        self.hidden: list[int] = []
        self.uninstalled = False
        self.handler = None
        self.lease = None
        self.closed_jobs = []

    def acquire_isolated_owner(self, pid, hwnd, baseline):
        self.lease = SimpleNamespace(pid=pid, hwnd=hwnd, dedicated=True,
            handle=object(), is_current=lambda: True,
            create_job=Mock(return_value=object()), close=Mock())
        return self.lease

    def close_owner_job(self, job):
        self.closed_jobs.append(job)

    def hwp_pids(self):
        return set(self.pids)

    def window_pid(self, hwnd):
        return self.window_pids[hwnd]

    def is_hwp_process(self, pid):
        return pid in self.hwp_processes

    def visible_top_windows(self):
        return list(self.windows)

    def hide_window(self, hwnd):
        self.hidden.append(hwnd)

    def install_show_hook(self, handler):
        if self.fail_hook:
            raise window_guard.WindowGuardError("hook failed")
        self.handler = handler
        return "hook"

    def pump_messages(self):
        return None

    def uninstall_hook(self, hook):
        self.uninstalled = hook == "hook"


class WindowGuardTests(unittest.TestCase):
    def test_only_new_hwp_windows_are_hidden_and_hook_is_removed(self) -> None:
        backend = FakeBackend()
        guard = window_guard.HancomWindowGuard(backend=backend, poll_interval=0.001)
        with guard:
            from types import SimpleNamespace
            session = SimpleNamespace(hwp=SimpleNamespace(XHwpWindows=SimpleNamespace(Active_XHwpWindow=SimpleNamespace(WindowHandle=200))))
            guard.bind_owner(session)
            deadline = time.monotonic() + 1
            while 200 not in backend.hidden and time.monotonic() < deadline:
                time.sleep(0.005)
            backend.handler(100)
            backend.handler(200)
            backend.handler(300)
        self.assertIn(200, backend.hidden)
        self.assertNotIn(100, backend.hidden)
        self.assertNotIn(300, backend.hidden)
        self.assertEqual(guard.baseline_pids, {10})
        self.assertTrue(backend.uninstalled)
        backend.lease.create_job.assert_called_once_with()
        self.assertEqual(backend.closed_jobs, [backend.lease.create_job.return_value])
        backend.lease.close.assert_called_once_with()

    def test_hook_failure_prevents_arming(self) -> None:
        guard = window_guard.HancomWindowGuard(backend=FakeBackend(fail_hook=True))
        with self.assertRaisesRegex(window_guard.WindowGuardError, "hook failed"):
            guard.arm()


if __name__ == "__main__":
    unittest.main()
