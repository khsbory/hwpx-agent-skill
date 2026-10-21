import sys,unittest,tempfile,types
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import quick_fill as q
import hancom_runtime as r
class RuntimeIntegrationTests(unittest.TestCase):
    def test_skip_guard_clears_low_level_cli_reference(self):
        args=['quick_fill','--input','in.hwpx','--output','out.hwpx','--data','data.json','--rules','rules.json','--page-guard-reference','ref.hwpx','--skip-page-guard']
        with patch.object(sys,'argv',args),patch.object(q,'load_json_file',return_value={}),patch.object(q,'compile_config',return_value={}),patch.object(q,'run_fixed_form_quick_fill',return_value={}) as fill,patch.object(q,'run_signature_step',return_value=None):
            self.assertEqual(q.main(),0)
        self.assertIsNone(fill.call_args.kwargs['page_guard_reference'])
    def test_signature_dispatch_requires_structural_validator_dependency(self):
        import insert_signature_pyhwpx as signature
        with patch.object(sys,'argv',['signature','--input','in.hwpx','--output','out.hwpx','--signature','sig.png']),patch.object(signature,'dispatch_cli',return_value=0) as dispatch:
            self.assertEqual(signature.main(),0)
        self.assertIn('lxml',dispatch.call_args.args[1])
    def test_dispatch_same_runtime_still_uses_bounded_child(self):
        with patch.dict(r.os.environ,{'HWPX_RUNTIME_SELECTED':'0'}),patch.object(r,'find_runtime',return_value=([sys.executable],sys.executable)),patch.object(r.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertEqual(r.dispatch_cli('worker.py'),0)
        self.assertEqual(run.call_args.kwargs['timeout'],900)
    def test_dispatch_helper_has_finite_timeout(self):
        with patch.dict(r.os.environ,{'HWPX_RUNTIME_SELECTED':'0'}),patch.object(r,'find_runtime',return_value=(['other.exe'],'other.exe')),patch.object(r.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
            r.dispatch_cli('worker.py')
        self.assertGreater(run.call_args.kwargs.get('timeout',0),0)
    def test_integrated_signature_uses_selected_runtime_helper(self):
        with patch.object(q,'count_markers',return_value={'(인)':1}),patch.object(q,'load_markers',return_value=['(인)']),patch.object(q,'insert_signatures',return_value=1) as direct,patch('quick_fill_common.run_signature_helper',return_value=1,create=True) as helper,patch.object(q,'validate_hwpx',return_value=[]):
            q.run_signature_step(filled_output_path=Path('in.hwpx'),data_payload={'signature':{'enabled':True,'image_path':'sig.png'}},rules_path=Path('rules.json'),report={},signature_output_override=None,signature_dry_run_override=False,page_guard_reference=None,skip_page_guard=True,page_guard_config={})
        direct.assert_not_called();helper.assert_called_once()
if __name__=='__main__':unittest.main()
