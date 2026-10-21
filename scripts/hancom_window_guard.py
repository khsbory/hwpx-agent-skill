#!/usr/bin/env python3
"""Exact-window guarding with strict isolation or explicit cooperative policy."""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path


class WindowGuardError(RuntimeError):
    """Raised when the guard cannot be armed or stops operating safely."""


class _HeldProcessIdentity:
    """Query/synchronize handle only; no dedication or termination authority."""
    def __init__(self, backend, pid, hwnd, handle):
        self.backend, self.pid, self.hwnd, self.handle = backend, pid, hwnd, handle

    def is_current(self):
        if self.handle is None or self.backend.kernel32.WaitForSingleObject(self.handle, 0) != 258:
            return False
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        return bool(self.backend.kernel32.QueryFullProcessImageNameW(
            self.handle, 0, buffer, ctypes.byref(size)) and
            Path(buffer.value).name.lower() == "hwp.exe" and
            self.backend.window_pid(self.hwnd) == self.pid)

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            if not self.backend.kernel32.CloseHandle(handle):
                raise WindowGuardError("[exclusive-use] Cannot release held process identity.")

    def wait_for_exit(self, timeout_seconds):
        import math
        seconds = float(timeout_seconds)
        if self.handle is None or not math.isfinite(seconds) or seconds <= 0:
            raise WindowGuardError("[exclusive-use] Invalid normal shutdown wait.")
        result = self.backend.kernel32.WaitForSingleObject(self.handle, min(0xfffffffe, math.ceil(seconds * 1000)))
        if result == 0:
            return True
        if result == 258:
            raise WindowGuardError("[exclusive-use] Normal shutdown timed out; nothing was force-terminated. Inspect Hancom manually.")
        raise WindowGuardError("[exclusive-use] Cannot verify normal Hancom shutdown.")


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _JobIOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JobBasicLimits), ("IoInfo", _JobIOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class WindowsWindowBackend:
    EVENT_OBJECT_SHOW = 0x8002
    WINEVENT_OUTOFCONTEXT = 0x0000
    WINEVENT_SKIPOWNPROCESS = 0x0002
    SW_HIDE = 0
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PM_REMOVE = 0x0001

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowGuardError("The Hanword window guard requires Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._win_event_type = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._enum_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._callbacks: list[object] = []
        # Every native boundary declares pointer-width-correct handles/LPARAM.
        signatures = [
            (self.kernel32, "CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.kernel32, "SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            (self.kernel32, "AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            (self.kernel32, "CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel32, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel32, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel32, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (self.kernel32, "Process32FirstW", [wintypes.HANDLE, ctypes.POINTER(self.PROCESSENTRY32W)], wintypes.BOOL),
            (self.kernel32, "Process32NextW", [wintypes.HANDLE, ctypes.POINTER(self.PROCESSENTRY32W)], wintypes.BOOL),
            (self.kernel32, "QueryFullProcessImageNameW", [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.user32, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user32, "IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            (self.user32, "EnumWindows", [self._enum_type, wintypes.LPARAM], wintypes.BOOL),
            (self.user32, "ShowWindowAsync", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (self.user32, "SetWinEventHook", [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, self._win_event_type, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            (self.user32, "UnhookWinEvent", [wintypes.HANDLE], wintypes.BOOL),
            (self.user32, "PeekMessageW", [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            (self.user32, "TranslateMessage", [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
            (self.user32, "DispatchMessageW", [ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t),
        ]
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result

    def create_owner_job(self, pid: int) -> object:
        """Contain only a verified owned PID, including RPC-launched COM servers.

        This is an unnamed non-inheritable job, not a PID/name-wide kill. Windows
        closes its last handle even if the Python helper is forcibly terminated.
        """
        job = self.kernel32.CreateJobObjectW(None, None)
        if not job:
            raise WindowGuardError("Cannot create the owned-session lifetime job.")
        handle = None
        try:
            limits = _JobExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise WindowGuardError("Cannot set owned-session lifetime limits.")
            handle = self.kernel32.OpenProcess(0x0100 | 0x0001 | self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle or not self.kernel32.AssignProcessToJobObject(job, handle):
                raise WindowGuardError("Cannot contain the verified Hanword session; document was not opened.")
            return job
        except BaseException:
            self.kernel32.CloseHandle(job)
            raise
        finally:
            if handle:
                self.kernel32.CloseHandle(handle)

    def close_owner_job(self, job: object) -> None:
        if not self.kernel32.CloseHandle(job):
            raise WindowGuardError("Cannot close the owned-session lifetime job.")

    def hwp_pids(self) -> set[int]:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        invalid = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid:
            raise WindowGuardError("Could not enumerate running processes.")
        result: set[int] = set()
        entry = self.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            ok = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            if not ok and ctypes.get_last_error() != 18:
                raise WindowGuardError("Could not read process snapshot.")
            while ok:
                if entry.szExeFile.lower() == "hwp.exe":
                    result.add(int(entry.th32ProcessID))
                ok = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:
                raise WindowGuardError("Process snapshot ended with an API failure.")
        finally:
            self.kernel32.CloseHandle(snapshot)
        return result

    def window_pid(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def is_hwp_process(self, pid: int) -> bool:
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        try:
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return False
            return Path(buffer.value).name.lower() == "hwp.exe"
        finally:
            self.kernel32.CloseHandle(handle)

    def hold_process_identity(self, pid: int, hwnd: int) -> object:
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION | 0x100000, False, pid)
        if not handle:
            raise WindowGuardError("[exclusive-use] Cannot hold exact Hancom identity; session left untouched.")
        identity = _HeldProcessIdentity(self, pid, hwnd, handle)
        try:
            if not identity.is_current():
                raise WindowGuardError("[exclusive-use] Hancom identity changed; session left untouched.")
            return identity
        except BaseException:
            identity.close()
            raise

    def top_windows(self, *, visible_only: bool = False) -> list[int]:
        result: list[int] = []

        def visit(hwnd, _lparam):
            if not visible_only or self.user32.IsWindowVisible(hwnd):
                result.append(int(hwnd))
            return True

        callback = self._enum_type(visit)
        if not self.user32.EnumWindows(callback, 0):
            raise WindowGuardError("Could not enumerate top-level windows.")
        return result

    def visible_top_windows(self) -> list[int]:
        return self.top_windows(visible_only=True)

    def hide_window(self, hwnd: int) -> None:
        self.user32.ShowWindowAsync(hwnd, self.SW_HIDE)

    def install_show_hook(self, handler) -> object:
        def callback(_hook, _event, hwnd, object_id, _child_id, _thread, _time):
            if hwnd and object_id == 0:
                handler(int(hwnd))

        wrapped = self._win_event_type(callback)
        hook = self.user32.SetWinEventHook(
            self.EVENT_OBJECT_SHOW,
            self.EVENT_OBJECT_SHOW,
            0,
            wrapped,
            0,
            0,
            self.WINEVENT_OUTOFCONTEXT | self.WINEVENT_SKIPOWNPROCESS,
        )
        if not hook:
            raise WindowGuardError("Could not install the Hanword window event hook.")
        self._callbacks.append(wrapped)
        return hook

    def pump_messages(self) -> None:
        message = wintypes.MSG()
        while self.user32.PeekMessageW(ctypes.byref(message), 0, 0, 0, self.PM_REMOVE):
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))

    def uninstall_hook(self, hook: object) -> None:
        if not self.user32.UnhookWinEvent(hook):
            raise WindowGuardError("Could not uninstall the window event hook.")
        self._callbacks.clear()


class HancomWindowGuard:
    """Hide only a verified COM window, never a PID-wide window sweep.

    exclusive_use=True is caller approval to start with all Hancom closed and
    not use Hancom during automation. It is NOT an intrinsic isolation lease.
    """

    def __init__(self, backend=None, *, poll_interval: float = 0.005, exclusive_use: bool = False) -> None:
        self.backend = backend
        # Explicit caller approval of a cooperative policy, NOT OS isolation.
        self.exclusive_use = exclusive_use
        self.poll_interval = max(poll_interval, 0.001)
        self.baseline_pids: set[int] = set()
        self.hidden_windows: set[int] = set()
        self._owner_job = None
        self._cooperative_identity = None
        self._closing = False
        self._identity_lock = threading.RLock()
        self._policy_violation = None
        self._cleanup_failure = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def ensure_activation_supported(self) -> None:
        """Refuse before COM creation when dedicated isolation cannot be proven.

        WindowsWindowBackend intentionally has no acquire_isolated_owner:
        installed Hancom's class-factory/process isolation is not established.
        No command-line/registry/PID-delta heuristic substitutes for that proof.
        """
        self.ensure_healthy()
        if self.exclusive_use:
            if self.baseline_pids or self.backend.hwp_pids():
                raise WindowGuardError("[exclusive-use] Close all Hancom documents before starting; do not use Hancom during automation. COM activation refused.")
            return
        if not callable(getattr(self.backend, "acquire_isolated_owner", None)):
            raise WindowGuardError("[session-isolation] No supported positive dedicated-process isolation contract; activation refused before COM creation.")

    def bind_owner(self, hwp: object) -> int:
        """Bind only the COM session's documented active WindowHandle.

        Before binding the guard is deliberately no-hide. Baseline exclusion is
        an additional rejection, never proof of ownership. No PID-wide sweep.
        """
        try:
            hwnd = int(hwp.hwp.XHwpWindows.Active_XHwpWindow.WindowHandle)
            pid = self.backend.window_pid(hwnd)
        except Exception as exc:
            raise WindowGuardError("Cannot establish the session's exact window ownership.") from exc
        if hwnd <= 0 or pid <= 0 or pid in self.baseline_pids or not self.backend.is_hwp_process(pid):
            raise WindowGuardError("Session window is not a fresh verified Hanword owner.")
        if getattr(self, "owner_hwnd", None) is not None and (self.owner_hwnd, self.owner_pid) != (hwnd, pid):
            raise WindowGuardError("The guard owner cannot be rebound.")
        if self.exclusive_use:
            with self._identity_lock:
                if self._cooperative_identity is not None:
                    raise WindowGuardError("[exclusive-use] Session already bound.")
                identity = self.backend.hold_process_identity(pid, hwnd)
                try:
                    if identity.pid != pid or identity.hwnd != hwnd:
                        raise WindowGuardError("[exclusive-use] Held identity does not match exact COM HWND/PID; activation refused.")
                    self._check_cooperative_native(identity)
                    self._check_cooperative_com(hwp.hwp, hwnd)
                except BaseException:
                    identity.close()
                    raise
                self._cooperative_identity = identity
                self._closing = False
                self._com_thread = threading.get_ident()
                self.owner_hwnd, self.owner_pid = hwnd, pid
                self._consider_window(hwnd)
                return pid
        if self._owner_job is None:
            # A new PID and DispatchEx do not prove dedicated ownership. The
            # backend must retain a stable process handle AND provide positive
            # isolation evidence. Never attach an uncertain PID to a kill job.
            acquire = getattr(self.backend, "acquire_isolated_owner", None)
            if acquire is None:
                raise WindowGuardError("No positive dedicated-process isolation proof; session left untouched.")
            lease = acquire(pid, hwnd, frozenset(self.baseline_pids))
            if lease is None:
                raise WindowGuardError("Ambiguous Hanword activation; session left untouched.")
            try:
                if (lease.pid != pid or lease.hwnd != hwnd or
                        lease.dedicated is not True or lease.handle is None or
                        lease.is_current() is not True):
                    raise WindowGuardError("No stable dedicated owner identity; session left untouched.")
                self._owner_job = lease.create_job()
                if self._owner_job is None:
                    raise WindowGuardError("Dedicated owner containment failed.")
                self._owner_lease = lease
            except BaseException:
                lease.close()
                raise
        self.owner_hwnd, self.owner_pid = hwnd, pid
        self._consider_window(hwnd)
        return pid

    def _check_cooperative_native(self, identity):
        if (self._policy_violation or self.baseline_pids or identity.handle is None or
                identity.is_current() is not True or self.backend.hwp_pids() != {identity.pid} or
                self.backend.window_pid(identity.hwnd) != identity.pid or
                any(hwnd != identity.hwnd and self.backend.window_pid(hwnd) == identity.pid
                    # Hancom creates hidden OLE/IME/WPF helper windows even for
                    # one blank COM document. They are not additional documents.
                    # Visible extra windows remain a cooperative-policy warning;
                    # the creating COM thread separately checks document counts.
                    for hwnd in self.backend.visible_top_windows())):
            self._policy_violation = "[exclusive-use] Unexpected Hancom process/window or lost identity. Quit refused; inspect and close Hancom manually."
            raise WindowGuardError(self._policy_violation)

    def _check_cooperative_com(self, raw, hwnd):
        # Bind/close on creating COM thread only; never polling thread.
        try:
            invalid = (int(raw.XHwpWindows.Active_XHwpWindow.WindowHandle) != hwnd or
                       int(raw.XHwpWindows.Count) != 1 or
                       int(raw.XHwpDocuments.Count) not in (0, 1))
        except Exception as exc:
            self._policy_violation = "[exclusive-use] Cannot verify COM document/window inventory. Quit refused; inspect Hancom manually."
            raise WindowGuardError(self._policy_violation) from exc
        if invalid:
            self._policy_violation = "[exclusive-use] Additional documents/windows or changed COM window. Quit refused; inspect Hancom manually."
            raise WindowGuardError(self._policy_violation)

    def authorize_close(self, hwp: object) -> None:
        """Cooperative close check; caller MUST be the creating COM thread."""
        if not self.exclusive_use:
            return
        if threading.get_ident() != getattr(self, "_com_thread", None):
            raise WindowGuardError("[exclusive-use] Close authorization requires the creating COM thread.")
        with self._identity_lock:
            try:
                self.ensure_healthy()
                identity = self._cooperative_identity
                if identity is None:
                    raise WindowGuardError("[exclusive-use] No held identity. Quit refused.")
                self._check_cooperative_native(identity)
                self._check_cooperative_com(hwp.hwp, identity.hwnd)
                # The exact object is now authorized to Quit. Its expected
                # window/process disappearance must not be latched by polling
                # as an unrelated policy violation during normal shutdown.
                self._closing = True
            except Exception as exc:
                if isinstance(exc, WindowGuardError):
                    self._policy_violation = str(exc)
                    raise
                self._policy_violation = f"[exclusive-use] Quit refused: {exc}. Inspect Hancom manually."
                raise WindowGuardError(self._policy_violation) from exc

    def record_cleanup_failure(self, exc: BaseException) -> None:
        """Latch text only: swallowed close errors must still veto publication.

        Do not retain exception tracebacks, which may hold raw COM references.
        """
        if self.exclusive_use and self._cleanup_failure is None:
            self._cleanup_failure = f"[exclusive-use] Session cleanup failed: {exc}. Inspect Hancom manually."

    def wait_for_normal_exit(self) -> None:
        with self._identity_lock:
            identity = self._cooperative_identity
            if not self._closing or identity is None:
                raise WindowGuardError("[exclusive-use] Normal shutdown was not authorized.")
        identity.wait_for_exit(10.0)

    def release_owner(self) -> None:
        """Release a strict lease/job or cooperative query handle exactly once."""
        self.owner_hwnd, self.owner_pid = None, None
        with self._identity_lock:
            identity, self._cooperative_identity = self._cooperative_identity, None
            self._closing = False
            if identity is not None:
                identity.close()
        if self._owner_job is not None:
            job, self._owner_job = self._owner_job, None
            try:
                self.backend.close_owner_job(job)
            finally:
                lease, self._owner_lease = getattr(self, "_owner_lease", None), None
                if lease is not None:
                    lease.close()

    def _consider_window(self, hwnd: int) -> None:
        if self.exclusive_use:
            with self._identity_lock:
                identity = self._cooperative_identity
                if identity is None or self._closing:
                    return
                try:
                    self._check_cooperative_native(identity)
                except Exception as exc:
                    self._policy_violation = str(exc)
                    return
                if hwnd == identity.hwnd:
                    self.backend.hide_window(hwnd)
                    self.hidden_windows.add(hwnd)
            return
        if hwnd != getattr(self, "owner_hwnd", None):
            return
        lease = getattr(self, "_owner_lease", None)
        if lease is None or lease.is_current() is not True:
            return
        pid = self.backend.window_pid(hwnd)
        if pid != getattr(self, "owner_pid", None) or pid in self.baseline_pids:
            return
        if not self.backend.is_hwp_process(pid):
            return
        self.backend.hide_window(hwnd)
        self.hidden_windows.add(hwnd)

    def _run(self) -> None:
        hook = None
        try:
            hook = self.backend.install_show_hook(self._consider_window)
            self._ready.set()
            while not self._stop.is_set():
                self.backend.pump_messages()
                if self.exclusive_use:
                    # Native checks only: a hidden owner may yield no visible
                    # windows, but concurrent-process detection must continue.
                    self._consider_window(0)
                for hwnd in self.backend.visible_top_windows():
                    self._consider_window(hwnd)
                self._stop.wait(self.poll_interval)
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            if hook is not None:
                try:
                    self.backend.uninstall_hook(hook)
                except BaseException as exc:
                    if self._error is None:
                        self._error = exc

    def arm(self) -> "HancomWindowGuard":
        if self._thread is not None:
            raise WindowGuardError("The Hanword window guard is already armed.")
        if self.backend is None:
            self.backend = WindowsWindowBackend()
        self.baseline_pids = set(self.backend.hwp_pids())
        if self.exclusive_use and self.baseline_pids:
            raise WindowGuardError("[exclusive-use] Hancom is already running. Save and close all Hancom documents, then retry; nothing was closed.")
        self._thread = threading.Thread(target=self._run, name="hancom-window-guard", daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0):
            self.close()
            raise WindowGuardError("The Hanword window guard did not arm in time.")
        self.ensure_healthy()
        return self

    def ensure_healthy(self) -> None:
        if self.exclusive_use and self._policy_violation:
            raise WindowGuardError(self._policy_violation)
        if self._error is not None:
            raise WindowGuardError(f"The Hanword window guard failed: {self._error}") from self._error
        if self._thread is None or not self._thread.is_alive():
            raise WindowGuardError("The Hanword window guard is not running.")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive() and self._error is None:
                self._error = WindowGuardError("The Hanword window guard did not stop cleanly.")
        try:
            self.release_owner()
        except BaseException as exc:
            if self._error is None:
                self._error = exc

    def __enter__(self) -> "HancomWindowGuard":
        return self.arm()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
        if _exc_type is None:
            if self.exclusive_use and (self._cleanup_failure or self._policy_violation):
                raise WindowGuardError(self._cleanup_failure or self._policy_violation)
            if self._error is not None:
                raise WindowGuardError(f"The Hanword window guard failed during cleanup: {self._error}") from self._error

