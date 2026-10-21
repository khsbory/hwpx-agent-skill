"""Shared Python selection and read-only Hancom diagnostics (no installation)."""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys


class RuntimeErrorDetail(RuntimeError):
    pass


def find_runtime(required: tuple[str, ...] = ()) -> tuple[list[str], str]:
    from quick_fill_common import _python_candidate_specs
    specs = _python_candidate_specs()
    explicit = os.environ.get("PYHWPX_PYTHON")
    if explicit:
        specs = [([explicit], explicit)]
    failures = []
    code = (
        "import importlib,sys; "
        + "; ".join(f"importlib.import_module({name!r})" for name in ("pyhwpx", *required))
        + "; print(sys.executable)"
    )
    for command, label in specs:
        print(f"STAGE: runtime-probe | {label}", flush=True)
        try:
            probe = subprocess.run([*command, "-c", code], capture_output=True,
                                   text=True, errors="replace", timeout=30)
            if probe.returncode == 0:
                return command, probe.stdout.strip().splitlines()[-1]
            detail = (probe.stderr or probe.stdout).strip().splitlines()
            failures.append(f"{label}: {detail[-1] if detail else 'import failed'}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{label}: {exc}")
    raise RuntimeErrorDetail(
        "[runtime] No usable Python environment. Set PYHWPX_PYTHON to a Python "
        "executable with pyhwpx and the required dependencies. " + " | ".join(failures)
    )


def dispatch_cli(script: str, required: tuple[str, ...] = ()) -> int | None:
    """Select once; forward argv without a shell, and preserve child exit status."""
    if os.environ.get("HWPX_RUNTIME_SELECTED") == "1":
        return None
    print("STAGE: runtime-selection", flush=True)
    command, executable = find_runtime(required)
    # Even an already compatible interpreter gets a bounded worker process.
    # The selected marker prevents recursive redispatch inside that worker.
    env = dict(os.environ, HWPX_RUNTIME_SELECTED="1")
    print(f"RUNTIME: {executable}", flush=True)
    try:
        return subprocess.run([*command, str(Path(script).resolve()), *sys.argv[1:]], env=env, timeout=900).returncode
    except subprocess.TimeoutExpired as exc:
        raise RuntimeErrorDetail("[runtime-timeout] Helper exceeded 900 seconds; no unrelated Hancom process was terminated. Inspect owned session cleanup before retrying.") from exc


def create_fresh_hwp(Hwp, *, guard, pythoncom_module=None, client=None,
                     version=None, fonts=None):
    """Version-pinned pyhwpx facade over explicit COM fresh activation.

    Never invoke the vendor initializer/finalizer. DispatchEx selects the COM
    create path, not an assertion of dedicated OS-process ownership. The guard
    must independently authorize strict isolation or approved cooperative
    exclusive-use policy before any application mutation.
    Dependency parameters exist for offline contract tests, not CLI overrides.
    """
    import threading
    from types import SimpleNamespace

    if guard is None:
        raise RuntimeErrorDetail("[session-ownership] An armed guard is required.")
    guard.ensure_healthy()
    preflight = getattr(guard, "ensure_activation_supported", None)
    if preflight is not None:
        preflight()
    if version is None:
        version = importlib.metadata.version("pyhwpx")
    if version != "1.7.2":
        raise RuntimeErrorDetail("[runtime-version] Fresh adapter requires pyhwpx == 1.7.2.")
    if pythoncom_module is None:
        import pythoncom as pythoncom_module
    if client is None:
        import win32com.client as client
    if fonts is None:
        from pyhwpx.core import fonts

    class FreshHwp(Hwp):
        # These override both vendor lifecycle methods, including partial init.
        def __init__(self):
            self.hwp = None
            self.on_quit = False
            self.htf_fonts = fonts
            self._apartment_thread = threading.get_ident()
            self._apartment_active = False
            self._owned_session = False
            self._session_closed = False

        def quit(self, save=False):
            if save:
                raise RuntimeErrorDetail("[lifecycle] Saving during close is not supported; save explicitly first.")
            return self.close()

        def close(self):
            if threading.get_ident() != self._apartment_thread:
                raise RuntimeErrorDetail("[apartment] Release must run on the creating thread.")
            if self._session_closed:
                return
            self._session_closed = True
            try:
                if self._owned_session and self.hwp is not None:
                    if getattr(guard, "exclusive_use", False) is True:
                        try:
                            guard.authorize_close(self)
                            self.hwp.Quit()  # exact confirmed COM object, no vendor Clear
                            guard.wait_for_normal_exit()
                        except Exception as exc:
                            guard.record_cleanup_failure(exc)
                            # quit_safely intentionally swallows errors; never
                            # silently leave a refused cooperative close.
                            print(f"WARNING: {exc}", file=sys.stderr, flush=True)
                            import traceback
                            chain = exc
                            while chain is not None:
                                traceback.clear_frames(chain.__traceback__)
                                chain = chain.__cause__ or chain.__context__
                            raise
                    else:
                        Hwp.quit(self)
            finally:
                self._owned_session = False
                self.hwp = None
                try:
                    if getattr(guard, "exclusive_use", False) is True:
                        guard.release_owner()
                finally:
                    if self._apartment_active:
                        self._apartment_active = False
                        pythoncom_module.CoUninitialize()

        def __del__(self):
            # Never run vendor __del__, and never release another thread's COM
            # apartment. Explicit same-thread close is the required lifecycle.
            if getattr(self, "_apartment_thread", None) == threading.get_ident():
                try:
                    self.close()
                except Exception:
                    pass

    session = FreshHwp()
    raw = wrapped = holder = None
    try:
        pythoncom_module.CoInitializeEx(pythoncom_module.COINIT_APARTMENTTHREADED)
        session._apartment_active = True
        if getattr(guard, "exclusive_use", False) is True:
            # Imports and apartment initialization can take time: recheck at the
            # last possible point, not only at initial runtime preflight.
            guard.ensure_activation_supported()
        raw = client.DispatchEx("HWPFrame.HwpObject", clsctx=pythoncom_module.CLSCTX_LOCAL_SERVER)
        holder = SimpleNamespace(hwp=raw)
        guard.bind_owner(holder)
        session._owned_session = True
        session.hwp = raw
        guard.ensure_healthy()
        wrapped = client.gencache.EnsureDispatch(raw)  # object, NEVER ProgID
        session.hwp = wrapped
        holder.hwp = None
        raw = wrapped = holder = None
        return session
    except BaseException as exc:
        # Clear completed vendor frames that may retain dispatch arguments.
        # Also neutralize the holder retained by a bind failure traceback.
        import traceback
        traceback.clear_frames(exc.__traceback__)
        if holder is not None:
            holder.hwp = None
        raw = wrapped = holder = None
        session.close()
        raise


def load_hwp():
    try:
        from pyhwpx import Hwp
        return Hwp
    except Exception as exc:
        raise RuntimeErrorDetail(
            f"[runtime-import] pyhwpx could not initialize in {sys.executable}: {exc}. "
            "Check package dependencies and writable win32com generated-cache permissions."
        ) from exc


def runtime_inspection_lines() -> list[str]:
    """Read registration only; COM activation is reserved for an actual operation."""
    lines = [f"PYTHON: {sys.executable}", f"PYTHON_VERSION: {sys.version.split()[0]}"]
    for package in ("pyhwpx", "pywin32", "PyMuPDF", "Pillow"):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "not installed"
        lines.append(f"PACKAGE: {package}={version}")
        if package == "pyhwpx" and version != "not installed":
            parts = tuple(int(part) for part in version.split(".")[:3] if part.isdigit())
            if parts < (1, 7, 2):
                lines.append("WARNING: pyhwpx is older than the declared minimum 1.7.2; no package was changed. Validate this installation before reuse.")
    if os.name != "nt":
        raise RuntimeErrorDetail("[platform] Hancom automation requires Windows.")
    import winreg
    server = None
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"HWPFrame.HwpObject\CLSID", 0, winreg.KEY_READ | view) as key:
                clsid = winreg.QueryValueEx(key, "")[0]
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32", 0, winreg.KEY_READ | view) as key:
                server = winreg.QueryValueEx(key, "")[0]
            break
        except OSError:
            continue
    if not server:
        raise RuntimeErrorDetail(
            "[com-registration] HWPFrame.HwpObject is not registered in either registry view. "
            "Start the installed desktop Hanword once, or repair its automation registration using the vendor installer."
        )
    lines.append(f"HANCOM_COM_SERVER: {server}")
    # Vendor registrations may contain an unquoted executable path with spaces.
    import re
    match = re.match(r'^"?(.+?\.exe)"?(?:\s|$)', server, flags=re.IGNORECASE)
    if not match or not Path(match.group(1)).is_file():
        raise RuntimeErrorDetail("[com-registration] Registered Hancom executable does not exist.")
    from hancom_security import pe_machine, find_registered_security_module
    machine = pe_machine(Path(match.group(1)))
    lines.append(f"HANCOM_MACHINE: {machine}")
    module = find_registered_security_module()
    if module and machine in {"x86", "x64", "arm64"} and pe_machine(module) != machine:
        raise RuntimeErrorDetail(
            f"[security-architecture] Hancom={machine}, registered DLL={pe_machine(module)}. "
            "Use a vendor-compatible FilePathChecker DLL; Python bitness alone is not the criterion."
        )
    return lines
