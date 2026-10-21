"""Windows subprocess lifetime containment using documented Win32 Job APIs.

The selected command is unchanged (including venv redirectors). Windows 10+
JOB_LIST assigns a fresh non-breakaway kill-on-close job during creation; verify
membership while suspended, then resume. Fail closed if unavailable. Only the
newly created process tree is controlled.
No native application cleanup or shared-session ownership is implied.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import math
import os
import subprocess
import tempfile
import time


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _BasicLimit(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', W.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', W.DWORD), ('Affinity', ctypes.c_size_t),
                ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimit), ('IoInfo', _IOCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _Startup(ctypes.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR),
                ('lpTitle', W.LPWSTR), ('dwX', W.DWORD), ('dwY', W.DWORD),
                ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
                ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD),
                ('dwFillAttribute', W.DWORD), ('dwFlags', W.DWORD),
                ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', ctypes.POINTER(ctypes.c_byte)),
                ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class _StartupEx(ctypes.Structure):
    _fields_ = [('StartupInfo', _Startup), ('lpAttributeList', W.LPVOID)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE),
                ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


def run_windows_owned(cmd, timeout, *, cwd=None, env=None):
    """CompletedProcess, or fail after terminating/waiting the owned job.

    Output goes to temporary files so a descendant cannot hold a captured pipe
    open. Cleanup waits at most five seconds for the job to empty and reports a
    cleanup failure rather than claiming successful bounded termination.
    """
    if os.name != 'nt':
        raise RuntimeError('Windows owned worker requested on another platform')
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError('timeout must be finite and positive')
    import msvcrt
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([W.LPVOID, W.LPCWSTR], W.HANDLE),
        'SetInformationJobObject': ([W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD], W.BOOL),
        'QueryInformationJobObject': ([W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD, W.LPVOID], W.BOOL),
        'InitializeProcThreadAttributeList': ([W.LPVOID, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)], W.BOOL),
        'UpdateProcThreadAttribute': ([W.LPVOID, W.DWORD, ctypes.c_size_t, W.LPVOID, ctypes.c_size_t, W.LPVOID, W.LPVOID], W.BOOL),
        'DeleteProcThreadAttributeList': ([W.LPVOID], None),
        'CreateProcessW': ([W.LPCWSTR, W.LPWSTR, W.LPVOID, W.LPVOID, W.BOOL, W.DWORD, W.LPVOID, W.LPCWSTR, W.LPVOID, ctypes.POINTER(_ProcessInfo)], W.BOOL),
        'IsProcessInJob': ([W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)], W.BOOL),
        'ResumeThread': ([W.HANDLE], W.DWORD),
        'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD),
        'GetExitCodeProcess': ([W.HANDLE, ctypes.POINTER(W.DWORD)], W.BOOL),
        'TerminateJobObject': ([W.HANDLE, W.UINT], W.BOOL),
        'TerminateProcess': ([W.HANDLE, W.UINT], W.BOOL),
        'CloseHandle': ([W.HANDLE], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(k, name)
        function.argtypes, function.restype = args, result

    def checked(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    job = checked(k.CreateJobObjectW(None, None))
    info = _ProcessInfo()
    assigned = False
    attributes = None
    initialized = False
    started = time.monotonic()
    try:
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        checked(k.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr, open(os.devnull, 'rb') as stdin:
            handles = (W.HANDLE * 3)(*[msvcrt.get_osfhandle(f.fileno()) for f in (stdin, stdout, stderr)])
            size = ctypes.c_size_t()
            k.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            checked(k.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)))
            initialized = True
            checked(k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, ctypes.sizeof(handles), None, None))
            # Windows 10+ documented JOB_LIST: assignment occurs inside creation,
            # so supervisor death cannot orphan a not-yet-assigned suspended child.
            jobs = (W.HANDLE * 1)(job)
            checked(k.UpdateProcThreadAttribute(attributes, 0, 0x2000D, jobs, ctypes.sizeof(jobs), None, None))
            startup = _StartupEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
            startup.lpAttributeList = ctypes.cast(attributes, W.LPVOID)
            environment = None
            if env is not None:
                environment = ctypes.create_unicode_buffer('\0'.join(
                    f'{key}={value}' for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + '\0')
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(cmd)))
            # The handle-list attribute prevents inheritance of unrelated handles.
            try:
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                checked(k.CreateProcessW(None, command, None, None, True,
                    0x4 | 0x80000 | 0x400,  # SUSPENDED | EXTENDED_STARTUPINFO | UNICODE_ENVIRONMENT
                    environment, os.fspath(cwd) if cwd else None, ctypes.byref(startup), ctypes.byref(info)))
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
            assigned = True  # CreateProcess succeeded with the JOB_LIST attribute.
            member = W.BOOL()
            checked(k.IsProcessInJob(info.hProcess, job, ctypes.byref(member)))
            if not member.value:
                raise RuntimeError('owned worker job membership was not established')
            if k.ResumeThread(info.hThread) == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            remaining = max(0, timeout - (time.monotonic() - started))
            wait = k.WaitForSingleObject(info.hProcess, min(0xfffffffe, math.ceil(remaining * 1000)))
            if wait == 258:
                raise RuntimeError(f'Subprocess timed out after {timeout:g}s')
            if wait != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            code = W.DWORD()
            checked(k.GetExitCodeProcess(info.hProcess, ctypes.byref(code)))
            # Even successful/crashed parents must not leave descendants alive.
            checked(k.TerminateJobObject(job, 1))
            _wait_empty(k, job)
            stdout.seek(0)
            stderr.seek(0)
            completed = subprocess.CompletedProcess(cmd, code.value,
                stdout.read().decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n'),
                stderr.read().decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n'))
            completed.owned_root_pid = info.dwProcessId
            return completed
    finally:
        try:
            if info.hProcess:
                if assigned:
                    checked(k.TerminateJobObject(job, 1))
                    _wait_empty(k, job)
                else:
                    # Still suspended: assignment failure must never run user code.
                    checked(k.TerminateProcess(info.hProcess, 1))
                if k.WaitForSingleObject(info.hProcess, 5000) != 0:
                    raise RuntimeError('owned worker cleanup did not complete')
        finally:
            if info.hThread:
                k.CloseHandle(info.hThread)
            if info.hProcess:
                k.CloseHandle(info.hProcess)
            k.CloseHandle(job)
            if initialized:
                k.DeleteProcThreadAttributeList(attributes)


def _wait_empty(kernel, job):
    # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: four LARGE_INTEGERs, four DWORDs.
    accounting = (ctypes.c_ulonglong * 6)()
    deadline = time.monotonic() + 5
    while True:
        if not kernel.QueryInformationJobObject(job, 1, accounting, ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        active = ctypes.cast(accounting, ctypes.POINTER(W.DWORD))[10]
        if active == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('owned worker job cleanup did not complete within 5s')
        time.sleep(.01)
