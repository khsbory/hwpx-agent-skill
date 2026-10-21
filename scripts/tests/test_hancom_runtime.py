from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hancom_runtime as runtime
import quick_fill_common as common


class RuntimeTests(unittest.TestCase):
    def test_explicit_python_is_authoritative(self):
        with patch.dict(os.environ, {"PYHWPX_PYTHON": "chosen-python.exe"}), patch.object(
            common, "_python_candidate_specs", return_value=[(["other.exe"], "other")]
        ), patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "PermissionError: cache denied")) as run:
            with self.assertRaisesRegex(runtime.RuntimeErrorDetail, "cache denied"):
                runtime.find_runtime()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "chosen-python.exe")

    def test_unusable_import_does_not_hide_a_usable_candidate(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            common, "_python_candidate_specs", return_value=[(["a.exe"], "a"), (["b.exe"], "b")]
        ), patch.object(runtime.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 1, "", "cache error"),
            subprocess.CompletedProcess([], 0, "C:/Python/python.exe\n", ""),
        ]):
            command, executable = runtime.find_runtime(("fitz",))
        self.assertEqual(command, ["b.exe"])
        self.assertEqual(executable, "C:/Python/python.exe")


if __name__ == "__main__":
    unittest.main()
