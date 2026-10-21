import sys, tempfile, types, unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import convert_hwp_to_hwpx as c
import hancom_security as s

class ConversionTests(unittest.TestCase):
    def setUp(self):
        def fresh(factory, *, guard):
            h = factory()
            # Factory binds identity before returning an explicitly owned adapter.
            guard.bind_owner(h)
            h._owned_session = True
            def close_owned():
                if h._owned_session:
                    h._owned_session = False
                    h.quit()
            h.close.side_effect = close_owned
            return h
        boundary = patch('hancom_runtime.create_fresh_hwp', side_effect=fresh)
        self.fresh_factory = boundary.start()
        self.addCleanup(boundary.stop)

    def test_security_failure_never_opens_and_quits_under_lock(self):
        events=[]
        @contextmanager
        def lock():
            events.append('lock')
            try: yield
            finally: events.append('unlock')
        h=Mock(); h.hwp.RegisterModule.return_value=False
        h.quit.side_effect=lambda:events.append('quit')
        guard=Mock(); guard.__enter__=Mock(return_value=guard); guard.__exit__=Mock(return_value=False)
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'in.hwp'; source.write_bytes(b'synthetic')
            target=Path(d)/'out.hwpx'; target.write_bytes(b'old')
            factory=Mock(return_value=h)
            with patch.dict(sys.modules,{'pyhwpx':types.SimpleNamespace(Hwp=factory)}), patch.object(c,'hancom_automation_lock',lock), patch('hancom_window_guard.HancomWindowGuard',return_value=guard):
                with self.assertRaises(c.ConversionError): c.convert_hwp_to_hwpx(source,target)
            self.assertEqual(target.read_bytes(),b'old')
            h.open.assert_not_called()
            factory.assert_called_once_with()
            self.fresh_factory.assert_called_once_with(factory, guard=guard)
            h.close.assert_called_once_with()
            h.quit.assert_called_once_with()
            self.assertLess(events.index('quit'),events.index('unlock'))

    def test_conversion_helper_cannot_accept_stale_existing_output(self):
        import quick_fill_common as common
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'in.hwp';source.write_bytes(b'input')
            output=Path(d)/'out.hwpx';output.write_bytes(b'old')
            with patch.object(common,'_find_pyhwpx_python',return_value=(['fake.exe'],'fake')),patch.object(common.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='',stderr='')):
                with self.assertRaises(RuntimeError):common.convert_hwp_to_hwpx(source,output)
            self.assertEqual(output.read_bytes(),b'old')

    def test_converter_helper_marks_selected_runtime_without_nested_worker(self):
        import quick_fill_common as common
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'in.hwp';source.write_bytes(b'input');out=Path(d)/'out.hwpx';out.write_bytes(b'placeholder')
            with patch.object(common,'_find_pyhwpx_python',return_value=(['fake.exe'],'fake')),patch.object(common.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='',stderr='')) as run:
                common._convert_hwp_helper(source,out)
            self.assertEqual(run.call_args.kwargs.get('env',{}).get('HWPX_RUNTIME_SELECTED'),'1')

    def test_conversion_guard_cleanup_failure_preserves_output(self):
        import test_insert_signature_pyhwpx as fixture
        h=Mock();h.hwp.RegisterModule.return_value=True
        h.save_as.side_effect=lambda path,**kwargs:(fixture.make_hwpx(Path(path)) or True)
        guard=Mock();guard.__enter__=Mock(return_value=guard);guard.__exit__=Mock(side_effect=RuntimeError('cleanup failed'))
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'in.hwp';source.write_bytes(b'synthetic');out=Path(d)/'out.hwpx';out.write_bytes(b'old')
            with patch.dict(sys.modules,{'pyhwpx':types.SimpleNamespace(Hwp=Mock(return_value=h))}),patch('hancom_window_guard.HancomWindowGuard',return_value=guard):
                with self.assertRaises(c.ConversionError):c.convert_hwp_to_hwpx(source,out)
            self.assertEqual(out.read_bytes(),b'old')

    def test_converter_cli_uses_bounded_runtime_dispatch(self):
        import hancom_runtime
        args=['converter','--input','synthetic.hwp','--output','out.hwpx']
        with patch.object(sys,'argv',args),patch.object(hancom_runtime,'dispatch_cli',return_value=0) as dispatch,patch.object(c,'convert_hwp_to_hwpx') as convert:
            self.assertEqual(c.main(),0)
        dispatch.assert_called_once();convert.assert_not_called()

    def test_force_hidden_does_not_iterate_other_session_windows(self):
        h=Mock()
        h._owned_session = True
        s.force_hidden(h)
        h.hwp.XHwpWindows.Item.assert_not_called()
        self.assertIs(h.hwp.XHwpWindows.Active_XHwpWindow.Visible,False)

    def test_registered_session_requires_owner_guard_before_factory(self):
        factory=Mock()
        with self.assertRaises(s.HancomSecurityError): s.create_registered_hwp(factory)
        factory.assert_not_called()

    def test_unverified_session_not_hidden_registered_or_quit(self):
        h=Mock(); guard=Mock(); guard.bind_owner.side_effect=RuntimeError('not owned')
        with patch.object(s,'force_hidden') as hide:
            with self.assertRaisesRegex(Exception,'not owned'):
                s.create_registered_hwp(Mock(return_value=h),guard=guard)
        h.hwp.RegisterModule.assert_not_called(); h.quit.assert_not_called(); hide.assert_not_called()
        h.close.assert_not_called()

if __name__=='__main__':unittest.main()
