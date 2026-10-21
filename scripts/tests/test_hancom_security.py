from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hancom_security as security


class HancomSecurityTests(unittest.TestCase):
    def test_new_hidden_hwp_uses_fresh_no_auto_register_arguments(self) -> None:
        instance = Mock()
        instance._owned_session = True
        instance.hwp = Mock()
        factory = Mock(return_value=instance)
        guard = Mock()
        with patch('hancom_runtime.create_fresh_hwp', return_value=instance) as fresh:
            actual = security.new_hidden_hwp(factory, guard=guard)
        fresh.assert_called_once_with(factory, guard=guard)
        self.assertIs(actual, instance)
        factory.assert_not_called()
        self.assertFalse(instance.visible)
        self.assertFalse(instance.hwp.Visible)

    def test_restore_command_uses_shared_tool_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backup = Path(temp_dir) / "backup.json"
            command = security.restore_command(backup)
        self.assertIn(str(Path(security.__file__).resolve()), command)
        self.assertIn(str(Path(sys.executable).resolve()), command)
        self.assertIn("--restore-security-backup", command)

    def test_quit_safely_only_calls_supplied_session(self) -> None:
        owned_session = Mock()
        owned_session._owned_session = True
        unrelated_session = Mock()
        security.quit_safely(owned_session)
        owned_session.close.assert_called_once_with()
        owned_session.quit.assert_not_called()
        security.quit_safely(unrelated_session)
        unrelated_session.close.assert_not_called()
        unrelated_session.quit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
