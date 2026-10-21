import sys, unittest, types
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hancom_security as s

class RegistryTests(unittest.TestCase):
    def setUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(patch.object(s, 'os', types.SimpleNamespace(name='nt', environ=os.environ, path=os.path)))
    def fake(self):
        key=Mock(); key.__enter__=Mock(return_value=key); key.__exit__=Mock(return_value=False)
        w=types.SimpleNamespace(HKEY_CURRENT_USER=1,KEY_READ=2,KEY_WRITE=4,REG_SZ=1,OpenKey=Mock(return_value=key),CreateKeyEx=Mock(return_value=key),SetValueEx=Mock(),QueryValueEx=Mock(return_value=(b'\x00\xff',3)))
        return w
    def test_binary_state_json_serializable_and_losslessly_restored(self):
        import json
        w=self.fake()
        with patch.dict(sys.modules,{'winreg':w}):
            state=s.read_registry_state()
            self.assertTrue(state[0]['value_exists'])
            self.assertEqual(state[0]['value_type'],3)
            state=json.loads(json.dumps(state))
            s.restore_registry_entries(state)
        self.assertEqual(w.SetValueEx.call_args.args[3:],(3,b'\x00\xff'))
    def test_repair_refuses_unrestorable_type_before_writes(self):
        import tempfile
        w=self.fake()
        state=[{'path':p,'key_exists':True,'value_exists':True,'value_type':5,'value':42} for p in s.SECURITY_REGISTRY_PATHS]
        with tempfile.TemporaryDirectory() as d:
            module=Path(d)/'module.dll';module.write_bytes(b'synthetic')
            with patch.dict(sys.modules,{'winreg':w}),patch.object(s,'read_registry_state',return_value=state),patch.object(s,'backup_registry_state'):
                with self.assertRaises(s.HancomSecurityError):s.repair_security_module_registration(module,Path(d))
        w.SetValueEx.assert_not_called()

    def test_repair_retry_keeps_owner_binding_before_registration(self):
        import hancom_security as security
        h1=Mock();h2=Mock();h1.hwp.RegisterModule.return_value=False;h2.hwp.RegisterModule.return_value=True
        events = []
        for index, h in enumerate((h1, h2), 1):
            h._owned_session = True
            def close_owned(h=h, index=index):
                events.append(f'close{index}')
                if h._owned_session:
                    h._owned_session = False
                    h.quit()
            h.close.side_effect = close_owned
            def register(*args, index=index, **kwargs):
                events.append(f'register{index}')
                return index == 2
            h.hwp.RegisterModule.side_effect = register
        guard=Mock()
        guard.release_owner.side_effect = lambda: events.append('release')
        factory = Mock()
        sessions = iter((h1, h2))
        def fresh(factory_arg, *, guard):
            self.assertIs(factory_arg, factory)
            h = next(sessions)
            guard.bind_owner(h)
            events.append('bind1' if h is h1 else 'bind2')
            return h
        with patch('hancom_runtime.create_fresh_hwp', side_effect=fresh) as boundary, patch.object(security,'discover_trusted_pyhwpx_module',return_value=Path('trusted.dll')),patch.object(security,'repair_security_module_registration',return_value=(Path('backup.json'),[])),patch.object(security.time,'sleep'):
            actual=security.create_registered_hwp(factory,guard=guard,repair_security_module=True)
        self.assertIs(actual[0],h2)
        self.assertEqual(guard.bind_owner.call_count,2)
        guard.release_owner.assert_called_once()
        self.assertEqual(boundary.call_count, 2)
        factory.assert_not_called()
        h1.close.assert_called_once_with()
        h1.quit.assert_called_once_with()
        h2.close.assert_not_called()
        self.assertEqual(events, ['bind1', 'register1', 'close1', 'release', 'bind2', 'register2'])

    def test_restore_prevalidates_all_types_before_any_write(self):
        w=self.fake()
        state=[{'path':p,'key_exists':True,'value_exists':True,'value_type':2,'value':'ok'} for p in s.SECURITY_REGISTRY_PATHS]
        state[-1]['value_type']=999
        with patch.dict(sys.modules,{'winreg':w}):
            with self.assertRaises(s.HancomSecurityError):s.restore_registry_entries(state)
        w.SetValueEx.assert_not_called()
    def test_expand_string_restores_original_type(self):
        w=self.fake(); w.QueryValueEx.return_value=('%HOME%/file',2)
        with patch.dict(sys.modules,{'winreg':w}):
            state=s.read_registry_state(); s.restore_registry_entries(state)
        self.assertEqual(w.SetValueEx.call_args.args[3:],(2,'%HOME%/file'))

if __name__=='__main__':unittest.main()
