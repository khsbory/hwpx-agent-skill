"""Run the installed vendor's real insert_picture body with fake native objects."""
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import insert_signature_pyhwpx as signature

from portability_support import scratch_directory, vendor_source

SCRATCH = scratch_directory('hwpx-review-fix-native')


def vendor_insert():
    source = vendor_source()
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Hwp')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'insert_picture')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
    env = {'os': os, 'Ctrl': lambda ctrl: ctrl}
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), env)
    return env['insert_picture']


class SourcePreservationTests(unittest.TestCase):
    def check_vendor(self, fail):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='source-regression-', dir=SCRATCH) as folder:
            source = Path(folder) / 'tmp_original_signature.png'
            original = b'synthetic-user-image-bytes'
            source.write_bytes(original)
            paths = []
            def insert(**kwargs):
                paths.append(Path(kwargs['Path']))
                self.assertEqual(paths[-1].read_bytes(), original)
                if fail: raise RuntimeError('vendor InsertPicture failure')
                return NS(Properties=NS(Item=lambda key: 1, SetItem=lambda *args: None))
            page = NS(PaperWidth=100, LeftMargin=0, RightMargin=0, GutterLen=0,
                      PaperHeight=100, TopMargin=0, BottomMargin=0, HeaderLen=0, FooterLen=0)
            class Fake:
                insert_picture = vendor_insert()
                hwp = NS(InsertPicture=staticmethod(insert))
                HParameterSet = NS(HSecDef=NS(HSet=None, PageDef=page))
                HAction = NS(GetDefault=lambda *args: None)
            if fail:
                with self.assertRaisesRegex(RuntimeError, 'vendor InsertPicture failure'):
                    signature.insert_image_at_current_position(Fake(), str(source), 8, 8)
            else:
                signature.insert_image_at_current_position(Fake(), str(source), 8, 8)
            self.assertTrue(source.exists(), 'vendor deleted caller source')
            self.assertEqual(source.read_bytes(), original)
            self.assertNotEqual(paths, [source])
            self.assertFalse(paths[0].name.startswith('tmp'))
            self.assertFalse(paths[0].exists(), 'private staged image was not cleaned up')
            self.assertFalse(paths[0].parent.exists(), 'private staging directory was not cleaned up')

    def test_vendor_tmp_source_preserved_after_failure(self): self.check_vendor(True)
    def test_vendor_tmp_source_preserved_after_success(self): self.check_vendor(False)


if __name__ == '__main__': unittest.main()
