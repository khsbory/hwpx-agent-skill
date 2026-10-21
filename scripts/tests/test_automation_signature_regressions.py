import sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import insert_signature_pyhwpx as s
class SignatureBoundaryTests(unittest.TestCase):
    def test_generic_zip_is_not_hwpx(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.hwpx'
            with zipfile.ZipFile(p,'w') as z:z.writestr('not-a-document.txt','x')
            with self.assertRaises(s.SignatureError):s._validate_saved_hwpx(p)
    def test_signature_image_must_be_embedded_and_referenced(self):
        from build_hwpx import pack_hwpx
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'valid.hwpx'
            pack_hwpx(Path(__file__).resolve().parents[2]/'templates'/'base',p)
            with self.assertRaises(s.SignatureError):
                s._validate_signature_images(p, minimum_count=1)

    def test_existing_signature_cannot_satisfy_new_insertion_receipt(self):
        import test_insert_signature_pyhwpx as old
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);source=p/'in.hwpx';output=p/'out.hwpx';image=p/'sig.png'
            Image.new('RGB',(5,5)).save(image)
            before=old.FakeHwp([],[]);before.insert_picture(str(image));before.save_as(source)
            output.write_bytes(b'old')
            h=old.FakeHwp([],[True])
            with patch.object(s,'load_hwp',return_value=Mock()),patch.object(s,'create_registered_hwp',return_value=(h,None,[])):
                with self.assertRaises(s.SignatureError):s.insert_signatures(source,output,image,['signed'],8,8,1,guard_factory=lambda:old.FakeGuard([]))
            self.assertEqual(output.read_bytes(),b'old')

    def test_cleanup_happens_before_guard_and_lock_exit(self):
        import test_insert_signature_pyhwpx as old
        from PIL import Image
        from contextlib import contextmanager
        events=[]
        @contextmanager
        def lock():
            events.append('lock')
            try:yield
            finally:events.append('unlock')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);source=p/'in.hwpx';output=p/'out.hwpx';image=p/'sig.png'
            old.make_hwpx(source);Image.new('RGB',(5,5)).save(image)
            h=old.FakeHwp(events,[True]);h.open=Mock(side_effect=RuntimeError('open failed'));h.quit=lambda:events.append('quit')
            with patch.object(s,'load_hwp',return_value=Mock()),patch.object(s,'create_registered_hwp',return_value=(h,None,[])),patch.object(s,'hancom_automation_lock',lock):
                with self.assertRaises(s.SignatureError):s.insert_signatures(source,output,image,['(인)'],8,8,1,guard_factory=lambda:old.FakeGuard(events))
            self.assertLess(events.index('quit'),events.index('guard-close'))
            self.assertLess(events.index('quit'),events.index('unlock'))
if __name__=='__main__':unittest.main()
