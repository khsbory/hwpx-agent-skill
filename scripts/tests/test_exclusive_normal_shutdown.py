import sys,unittest,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import test_exclusive_use as fixtures
from hancom_window_guard import HancomWindowGuard,WindowGuardError,WindowsWindowBackend
from hancom_security import quit_safely
from document_safety import atomic_output_path

class NormalShutdownTests(unittest.TestCase):
    def test_authorized_quit_exit_is_not_latched_as_policy_violation(self):
        b=fixtures.Backend();b.identity.wait_for_exit=Mock(return_value=True)
        f=fixtures.ExclusiveUseTests()
        with HancomWindowGuard(b,exclusive_use=True) as guard:
            raw,com,client=f.factory(b,guard);session=f.create(guard,com,client)
            def normal_exit():
                b.current=False;b.pids=set();guard._consider_window(0)
            raw.Quit.side_effect=normal_exit
            session.close()
            self.assertIsNone(guard._policy_violation)
        raw.Quit.assert_called_once_with();com.CoUninitialize.assert_called_once_with()
        b.identity.wait_for_exit.assert_called_once_with(10.0)

    def test_exit_timeout_vetoes_atomic_publication_without_forced_kill(self):
        b=fixtures.Backend();b.identity.wait_for_exit=Mock(side_effect=WindowGuardError('normal shutdown timed out'))
        f=fixtures.ExclusiveUseTests()
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'out.hwpx';dest.write_bytes(b'previous')
            with self.assertRaisesRegex(WindowGuardError,'timed out'):
                with atomic_output_path(dest) as stage:
                    with HancomWindowGuard(b,exclusive_use=True) as guard:
                        raw,com,client=f.factory(b,guard);session=f.create(guard,com,client)
                        stage.write_bytes(b'replacement');quit_safely(session)
            self.assertEqual(dest.read_bytes(),b'previous')
            self.assertEqual(list(Path(folder).iterdir()),[dest])
        raw.Quit.assert_called_once_with();com.CoUninitialize.assert_called_once_with()
        b.identity.close.assert_called_once_with();b.create_owner_job.assert_not_called()

    def test_native_exit_wait_uses_original_query_handle(self):
        b=object.__new__(WindowsWindowBackend)
        def image(handle,flags,buffer,size):buffer.value='C:/Hancom/Hwp.exe';return True
        b.kernel32=NS(OpenProcess=Mock(return_value=123),QueryFullProcessImageNameW=Mock(side_effect=image),WaitForSingleObject=Mock(return_value=258),CloseHandle=Mock(return_value=True))
        b.window_pid=Mock(return_value=11)
        identity=b.hold_process_identity(11,42)
        b.kernel32.WaitForSingleObject.return_value=0
        self.assertTrue(identity.wait_for_exit(10.0))
        b.kernel32.WaitForSingleObject.assert_called_with(123,10000)
        b.kernel32.WaitForSingleObject.return_value=258
        with self.assertRaisesRegex(WindowGuardError,'shutdown'):identity.wait_for_exit(0.01)
        identity.close();b.kernel32.CloseHandle.assert_called_once_with(123)

if __name__=='__main__':unittest.main()
