from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import export_document_to_pdf as exporter
import hancom_security as security


class FakeHwp:
    instances: list["FakeHwp"] = []
    register_results: list[bool] = [True]

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._owned_session = True
        self.hwp = Mock()
        result_index = len(FakeHwp.instances)
        result = FakeHwp.register_results[min(result_index, len(FakeHwp.register_results) - 1)]
        self.hwp.RegisterModule.return_value = result
        self.open = Mock(return_value=True)
        self.quit = Mock()
        def close_owned():
            if self._owned_session:
                self._owned_session = False
                self.quit()
        self.close = Mock(side_effect=close_owned)
        FakeHwp.instances.append(self)

    def save_as(self, path: str, format: str, arg: str) -> bool:
        import fitz
        with fitz.open() as document:
            document.new_page()
            document.save(path)
        return True


class ExportDocumentToPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeHwp.instances.clear()
        FakeHwp.register_results = [True]
        self.owner_guard = Mock()
        def fresh(factory, *, guard):
            instance = factory()
            guard.bind_owner(instance)
            return instance
        boundary = patch('hancom_runtime.create_fresh_hwp', side_effect=fresh)
        self.fresh_factory = boundary.start()
        self.addCleanup(boundary.stop)
        guard = patch.object(exporter, "HancomWindowGuard")
        guard.start()
        self.addCleanup(guard.stop)

    def test_export_passes_guard_and_quits_before_guard_exit_on_error(self):
        events = []
        class Guard:
            def __enter__(self):
                return self
            def ensure_healthy(self):
                pass
            def __exit__(self, *_args):
                events.append("guard-exit")
        guard = Guard()
        hwp = FakeHwp()
        hwp.open.side_effect = RuntimeError("synthetic open failure")
        hwp.quit.side_effect = lambda: events.append("quit")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(exporter, "load_hwp", return_value=Mock()), patch.object(exporter, "HancomWindowGuard", return_value=guard), patch.object(exporter, "create_registered_hwp", return_value=(hwp, None, [])) as factory:
                with self.assertRaisesRegex(exporter.ExportError, "document-open"):
                    exporter.export_document_to_pdf(root / "in.hwpx", root / "out.pdf")
            self.assertEqual(events, ["quit", "guard-exit"])
            hwp.close.assert_called_once_with()
            hwp.quit.assert_called_once_with()
            self.assertIs(factory.call_args.kwargs["guard"], guard)
            self.assertEqual(list(root.glob(".out-*.pdf")), [])

    def test_guard_failure_reports_distinct_stage_before_com_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(sys.modules, {"pyhwpx": types.SimpleNamespace(Hwp=FakeHwp)}), patch.object(exporter, "HancomWindowGuard") as guard, patch("builtins.print") as progress:
                guard.return_value.__enter__.side_effect = RuntimeError("guard unavailable")
                with self.assertRaisesRegex(exporter.ExportError, "guard-start.*guard unavailable"):
                    exporter.export_document_to_pdf(root / "in.hwpx", root / "out.pdf")
            self.assertEqual(FakeHwp.instances, [])
            progress.assert_any_call("STAGE: guard-start", flush=True)

    def test_invalid_pdf_releases_file_while_error_is_alive(self):
        # Keep the validation exception alive, as the exporter's finally block
        # does during cleanup. PyMuPDF must not retain a Windows file handle.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = Path(directory) / "invalid.pdf"
            path.write_bytes(b"not a PDF")
            try:
                exporter.validate_pdf(path)
            except exporter.ExportError as exc:
                self.assertIn("pdf-validation", str(exc))
                try:
                    path.unlink()
                except OSError as cleanup_error:
                    self.fail(f"PDF validation retained the file: {cleanup_error}")
                self.assertFalse(path.exists())
            else:
                self.fail("Invalid PDF was accepted")

    def test_invalid_pdf_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "input.hwpx", root / "out.pdf"
            source.write_bytes(b"input")
            output.write_bytes(b"existing")
            def invalid_save(_self, path, **_kwargs):
                Path(path).write_bytes(b"not a PDF")
                return True
            with patch.dict(sys.modules, {"pyhwpx": types.SimpleNamespace(Hwp=FakeHwp)}), patch.object(FakeHwp, "save_as", invalid_save):
                with self.assertRaisesRegex(exporter.ExportError, "pdf-validation"):
                    exporter.export_document_to_pdf(source, output)
            self.assertEqual(output.read_bytes(), b"existing")
            self.assertEqual(list(root.glob(".out-*.pdf")), [])

    def test_find_registered_security_module_requires_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module = Path(temp_dir) / exporter.SECURITY_DLL_NAME
            module.write_bytes(b"dll")
            self.assertEqual(
                exporter.find_registered_security_module([str(module)]),
                module.resolve(),
            )
            self.assertIsNone(
                exporter.find_registered_security_module([str(module) + ".missing"])
            )

    def test_backup_is_written_before_registry_repair_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            security,
            "read_registry_state",
            return_value=[
                {
                    "path": exporter.SECURITY_REGISTRY_PATHS[0],
                    "key_exists": True,
                    "value_exists": True,
                    "value": "old.dll",
                }
            ],
        ):
            temp = Path(temp_dir)
            module = temp / exporter.SECURITY_DLL_NAME
            module.write_bytes(b"MZ" + b"\0" * 62)
            backup = security.backup_registry_state(temp / "backups", module)
            data = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], exporter.BACKUP_SCHEMA)
            self.assertEqual(data["before"][0]["value"], "old.dll")
            self.assertEqual(data["planned_module_sha256"], exporter._sha256(module))
            self.assertIn("--restore-security-backup", data["restore_command"])
            self.assertIn(str(Path(security.__file__).resolve()), data["restore_command"])

    def test_default_backup_directory_uses_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"LOCALAPPDATA": temp_dir}
        ):
            self.assertEqual(
                security.default_backup_dir(),
                (Path(temp_dir) / "hwpx-skill" / "backups").resolve(),
            )

    def test_first_session_success_needs_no_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            hwp, backup, changes = exporter.create_registered_hwp(
                FakeHwp, guard=self.owner_guard,
                backup_dir=Path(temp_dir),
            )
            self.assertIs(hwp, FakeHwp.instances[0])
            self.assertIsNone(backup)
            self.assertEqual(changes, [])
            self.fresh_factory.assert_called_once_with(FakeHwp, guard=self.owner_guard)
            self.assertEqual(hwp.kwargs, {})

    def test_failure_repairs_once_then_uses_new_hidden_session(self) -> None:
        FakeHwp.register_results = [False, True]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            module = temp / exporter.SECURITY_DLL_NAME
            module.write_bytes(b"dll")
            backup = temp / "backup.json"
            with patch.object(
                security, "discover_trusted_pyhwpx_module", return_value=module
            ), patch.object(
                security,
                "repair_security_module_registration",
                return_value=(backup, ["changed"]),
            ) as repair, patch.object(security.time, "sleep") as wait:
                hwp, actual_backup, changes = exporter.create_registered_hwp(
                    FakeHwp, guard=self.owner_guard,
                    backup_dir=temp,
                    repair_security_module=True,
                )

            self.assertEqual(len(FakeHwp.instances), 2)
            self.assertIs(hwp, FakeHwp.instances[1])
            self.assertEqual(actual_backup, backup)
            self.assertEqual(changes, ["changed"])
            FakeHwp.instances[0].quit.assert_called_once()
            repair.assert_called_once_with(module, temp)
            wait.assert_called_once_with(1.0)
            for instance in FakeHwp.instances:
                self.assertEqual(
                    instance.kwargs,
                    {},
                )
            self.assertEqual(self.fresh_factory.call_count, 2)
            for call in self.fresh_factory.call_args_list:
                self.assertEqual(call.args, (FakeHwp,))
                self.assertIs(call.kwargs['guard'], self.owner_guard)
            FakeHwp.instances[0].close.assert_called_once_with()

    def test_second_registration_failure_stops_after_one_repair(self) -> None:
        FakeHwp.register_results = [False, False]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            module = temp / exporter.SECURITY_DLL_NAME
            module.write_bytes(b"dll")
            backup = temp / "backup.json"
            with patch.object(
                security, "discover_trusted_pyhwpx_module", return_value=module
            ), patch.object(
                security,
                "repair_security_module_registration",
                return_value=(backup, ["changed"]),
            ) as repair, patch.object(security.time, "sleep"):
                with self.assertRaisesRegex(exporter.ExportError, "one explicit HKCU"):
                    exporter.create_registered_hwp(
                        FakeHwp, guard=self.owner_guard,
                        backup_dir=temp,
                        repair_security_module=True,
                    )

            self.assertEqual(len(FakeHwp.instances), 2)
            repair.assert_called_once()
            FakeHwp.instances[0].quit.assert_called_once()
            FakeHwp.instances[1].quit.assert_called_once()
            FakeHwp.instances[0].close.assert_called_once_with()
            FakeHwp.instances[1].close.assert_called_once_with()

    def test_default_mode_stops_without_registry_repair(self) -> None:
        FakeHwp.register_results = [False]
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                security, "repair_security_module_registration"
            ) as repair, self.assertRaisesRegex(
                exporter.ExportError, "No registry changes were made"
            ):
                exporter.create_registered_hwp(
                    FakeHwp, guard=self.owner_guard,
                    backup_dir=Path(temp_dir),
                )
            repair.assert_not_called()
        self.assertEqual(len(FakeHwp.instances), 1)
        FakeHwp.instances[0].quit.assert_called_once()
        FakeHwp.instances[0].close.assert_called_once_with()

    def test_replacement_constructor_failure_reports_backup(self) -> None:
        class DisconnectingReplacement(FakeHwp):
            calls = 0

            def __init__(self, **kwargs):
                type(self).calls += 1
                if type(self).calls == 2:
                    raise RuntimeError("Object is not connected to server")
                super().__init__(**kwargs)

        FakeHwp.register_results = [False]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            module = temp / exporter.SECURITY_DLL_NAME
            module.write_bytes(b"dll")
            backup = temp / "backup.json"
            with patch.object(
                security, "discover_trusted_pyhwpx_module", return_value=module
            ), patch.object(
                security,
                "repair_security_module_registration",
                return_value=(backup, ["changed"]),
            ), patch.object(security.time, "sleep"):
                with self.assertRaisesRegex(
                    exporter.ExportError,
                    "session-start.*Restore from",
                ):
                    exporter.create_registered_hwp(
                        DisconnectingReplacement, guard=self.owner_guard,
                        backup_dir=temp,
                        repair_security_module=True,
                    )

    def test_export_opens_only_registered_second_session(self) -> None:
        FakeHwp.register_results = [False, True]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "source.hwpx"
            source.write_bytes(b"source")
            output = temp / "output.pdf"
            module = temp / exporter.SECURITY_DLL_NAME
            module.write_bytes(b"dll")
            backup = temp / "backup.json"
            fake_pyhwpx = types.SimpleNamespace(Hwp=FakeHwp)

            with patch.dict(sys.modules, {"pyhwpx": fake_pyhwpx}), patch.object(
                security, "discover_trusted_pyhwpx_module", return_value=module
            ), patch.object(
                security,
                "repair_security_module_registration",
                return_value=(backup, ["changed"]),
            ), patch.object(security.time, "sleep"), patch.object(exporter.time, "sleep"):
                actual_backup, changes = exporter.export_document_to_pdf(
                    source,
                    output,
                    backup_dir=temp,
                    repair_security_module=True,
                )

            self.assertEqual(actual_backup, backup)
            self.assertEqual(changes, ["changed"])
            FakeHwp.instances[0].open.assert_not_called()
            FakeHwp.instances[1].open.assert_called_once_with(str(source))
            FakeHwp.instances[1].quit.assert_called_once()
            self.assertGreater(output.stat().st_size, 0)
            FakeHwp.instances[1].close.assert_called_once_with()

    def test_partial_registry_write_failure_rolls_back(self) -> None:
        class FakeKey:
            def __init__(self, module, path):
                self.module = module
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeWinreg:
            HKEY_CURRENT_USER = object()
            KEY_READ = 1
            KEY_WRITE = 2
            REG_SZ = 1

            def __init__(self):
                self.state = {
                    exporter.SECURITY_REGISTRY_PATHS[0]: {
                        exporter.SECURITY_MODULE_ID: "old.dll"
                    }
                }

            def OpenKey(self, root, path, reserved=0, access=0):
                if path not in self.state:
                    raise FileNotFoundError(path)
                return FakeKey(self, path)

            def CreateKeyEx(self, root, path, reserved=0, access=0):
                self.state.setdefault(path, {})
                return FakeKey(self, path)

            def QueryValueEx(self, key, name):
                try:
                    return self.state[key.path][name], self.REG_SZ
                except KeyError as exc:
                    raise FileNotFoundError(name) from exc

            def SetValueEx(self, key, name, reserved, value_type, value):
                if key.path == exporter.SECURITY_REGISTRY_PATHS[1]:
                    raise OSError("simulated second write failure")
                self.state[key.path][name] = value

            def DeleteValue(self, key, name):
                try:
                    del self.state[key.path][name]
                except KeyError as exc:
                    raise FileNotFoundError(name) from exc

            def DeleteKey(self, root, path):
                if self.state.get(path):
                    raise OSError("key is not empty")
                if path not in self.state:
                    raise FileNotFoundError(path)
                del self.state[path]

        fake_winreg = FakeWinreg()
        before = {
            exporter.SECURITY_REGISTRY_PATHS[0]: {
                exporter.SECURITY_MODULE_ID: "old.dll"
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            module = temp / exporter.SECURITY_DLL_NAME
            module.write_bytes(b"dll")
            with patch.object(security, "os", types.SimpleNamespace(name="nt", path=os.path, environ=os.environ)), patch.dict(
                sys.modules, {"winreg": fake_winreg}
            ), self.assertRaisesRegex(exporter.ExportError, "rolled back automatically"):
                security.repair_security_module_registration(module, temp / "backups")
        self.assertEqual(fake_winreg.state, before)

    def test_restore_rejects_incomplete_registry_state(self) -> None:
        with patch.object(security.os, "name", "nt"), self.assertRaisesRegex(
            exporter.ExportError, "does not cover each"
        ):
            security.validate_registry_entries(
                [
                    {
                        "path": exporter.SECURITY_REGISTRY_PATHS[0],
                        "key_exists": False,
                        "value_exists": False,
                        "value": None,
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
