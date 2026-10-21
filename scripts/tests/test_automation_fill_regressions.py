import sys, tempfile, unittest, zipfile, json
from pathlib import Path
from unittest.mock import patch
from lxml import etree
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import fill_fixed_form as f
import extract_fill_schema as e

NS='http://www.hancom.co.kr/hwpml/2011/paragraph'
def root(text): return etree.fromstring(f'<sec xmlns:hp="{NS}">{text}</sec>'.encode())
class FillTests(unittest.TestCase):
    def test_split_run_placeholder_replaced_without_changing_other_run(self):
        r=root('<hp:p><hp:run><hp:t>{{na</hp:t></hp:run><hp:run><hp:t>me}} suffix</hp:t></hp:run></hp:p>')
        self.assertEqual(f.apply_text_replacements(r,[{'find':'{{name}}','replace':'Alice'}]),{'{{name}}':1})
        self.assertEqual(r.xpath('.//hp:t/text()',namespaces=f.NS),['Alice',' suffix'])
    def test_empty_search_rejected(self):
        with self.assertRaises(SystemExit):f.apply_text_replacements(root('<hp:p><hp:run><hp:t>A</hp:t></hp:run></hp:p>'),[{'find':'','replace':'X'}])
    def test_duplicate_rule_names_rejected(self):
        with self.assertRaises(SystemExit):f.apply_text_replacements(root(''),[{'name':'x','find':'a'},{'name':'x','find':'b'}])
    def test_relative_update_does_not_cross_cell(self):
        r=root('<hp:tc><hp:p><hp:run><hp:t>name</hp:t></hp:run></hp:p></hp:tc><hp:tc><hp:p><hp:run><hp:t/></hp:run></hp:p></hp:tc>')
        result=f.apply_relative_updates(r,[{'anchor':{'value':'name'},'replace':'WRONG'}])
        self.assertEqual(sum(result.values()),0)
    def test_ambiguous_anchor_rejected(self):
        r=root('<hp:p><hp:run><hp:t>name</hp:t><hp:t/><hp:t>name</hp:t><hp:t/></hp:run></hp:p>')
        with self.assertRaises(SystemExit):f.apply_relative_updates(r,[{'anchor':{'value':'name'},'replace':'X'}])
    def test_ambiguous_relative_target_rejected_before_mutation(self):
        r=root('<hp:p><hp:run><hp:t>name</hp:t><hp:t/><hp:t/></hp:run></hp:p>')
        original=etree.tostring(r)
        with self.assertRaises(SystemExit):f.apply_relative_updates(r,[{'anchor':{'value':'name'},'replace':'X'}])
        self.assertEqual(etree.tostring(r),original)
    def test_duplicate_tail_names_rejected(self):
        with self.assertRaises(SystemExit): f.apply_tail_replacements(root(''),[{'name':'x','find':'a'},{'name':'x','find':'b'}])
    def test_empty_tail_search_rejected(self):
        with self.assertRaises(SystemExit): f.apply_tail_replacements(root(''),[{'find':''}])
    def test_schema_rejects_doctype(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input.hwpx'
            with zipfile.ZipFile(p,'w') as z:z.writestr('Contents/section0.xml',b'<!DOCTYPE sec [<!ENTITY x "expanded">]><sec>&x;</sec>')
            with self.assertRaises((ValueError,SystemExit)):
                e.load_section_root(p)
    def test_extraction_rejects_parent_path_before_writes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);source=p/'input.hwpx';out=p/'extract';out.mkdir()
            with zipfile.ZipFile(source,'w') as z:z.writestr('../escaped.txt','x')
            with self.assertRaises(ValueError): f._extract_hwpx(source,out)
            self.assertFalse((p/'escaped.txt').exists())
    def test_schema_finds_split_run_field(self):
        r=root('<hp:p><hp:run><hp:t>{{na</hp:t></hp:run><hp:run><hp:t>me}}</hp:t></hp:run></hp:p>')
        with patch.object(e,'load_section_root',return_value=r):
            self.assertEqual(e.extract_schema(Path('unused'),20)['placeholder_fields'],[{'name':'name','occurrences':1}])
    def test_validation_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); source=p/'in.hwpx'; output=p/'out.hwpx'; config=p/'config.json'
            with zipfile.ZipFile(source,'w') as z:z.writestr('Contents/section0.xml',etree.tostring(root('<hp:p/>')))
            config.write_text('{}');output.write_bytes(b'old')
            with patch.object(f,'pack_hwpx',side_effect=lambda src,dest:Path(dest).write_bytes(b'bad')),patch.object(f,'validate_hwpx',return_value=['invalid']):
                with self.assertRaises(SystemExit):f.quick_fill(input_path=source,output_path=output,config_path=config,page_guard_reference=None,report_path=None)
            self.assertEqual(output.read_bytes(),b'old')

if __name__=='__main__':unittest.main()
