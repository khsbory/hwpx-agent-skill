import sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_insert_signature_pyhwpx as fixtures

class FixturePortabilityTests(unittest.TestCase):
    def test_mimetype_is_first_even_when_filesystem_yields_it_last(self):
        base=fixtures.SCRIPTS_DIR.parent/'templates/base'
        ordered=sorted(base.rglob('*'),key=lambda p:(p.name=='mimetype',str(p)))
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'synthetic.hwpx'
            with patch.object(Path,'rglob',return_value=iter(ordered)):
                fixtures.make_hwpx(target)
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist()[0],'mimetype')
                self.assertEqual(archive.getinfo('mimetype').compress_type,zipfile.ZIP_STORED)
            fixtures.signature._validate_saved_hwpx(target)

if __name__=='__main__':unittest.main()
