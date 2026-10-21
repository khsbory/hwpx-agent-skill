"""Visible XML tails survive table extraction without exposing comments/PIs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
import zipfile
from unittest import mock

TABLES = Path(__file__).resolve().parents[1] / 'table_extract'
sys.path.insert(0, str(TABLES))
import hwpx_table_parser as parser


class TableVisibleTails(unittest.TestCase):
    def setUp(self):
        # Explicit Hermes scratch, not the system temporary directory or a fixture.
        self.scratch = (Path.home() / 'AppData/Local/hermes/cache/scratch'
                        / 'hwpx-fix-table-visible-tails')
        self.scratch.mkdir(parents=True, exist_ok=True)

    def package(self, text):
        path = self.scratch / ('visible-tails-' + uuid.uuid4().hex + '.hwpx')
        self.addCleanup(path.unlink, missing_ok=True)
        xml = ('<hp:sec xmlns:hp="urn:synthetic:hwpx"><hp:tbl rowCnt="1" colCnt="1">'
               '<hp:tr><hp:tc><hp:cellAddr rowAddr="0" colAddr="0"/>'
               '<hp:cellSpan rowSpan="1" colSpan="1"/><hp:subList><hp:p><hp:run>'
               '<hp:t>' + text + '</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
               '</hp:tr></hp:tbl></hp:sec>')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('mimetype', 'application/hwp+zip')
            archive.writestr('Contents/section0.xml', xml)
        return path

    def cases(self):
        return (
            ('A<!--COMMENT-->B<?review PRIVATE?>C', 'ABC'),
            ('A<!--COMMENT-->B<hp:lineBreak/>C<?review PRIVATE?>D'
             '<hp:tab/>E<hp:lineBreaker/>F<hp:span>G</hp:span>H',
             'AB\nCD\tE\nFGH'),
        )

    def assert_payload(self, payload, expected, backend):
        self.assertEqual(payload['lxml'], backend)
        self.assertEqual(payload['tableCount'], 1)
        cell = payload['tables'][0]['cells'][0]
        self.assertEqual(cell['text'], expected)
        self.assertEqual(cell['paragraphs'], [expected])
        serialized = json.dumps(payload)
        for hidden in ('COMMENT', 'PRIVATE', 'review'):
            self.assertNotIn(hidden, serialized)

    def test_actual_parser_preserves_visible_tails_both_backends(self):
        self.assertIsNotNone(parser._LXML_ET, 'lxml required to verify both backends')
        for text, expected in self.cases():
            source = self.package(text)
            for backend in (True, False):
                with self.subTest(expected=expected, lxml=backend):
                    with mock.patch.object(parser, '_HAVE_LXML', backend):
                        doc = parser.HwpxTableParser.load(source)
                        self.assert_payload(json.loads(doc.to_json()), expected, backend)
                        self.assertEqual(doc.tables[0].to_matrix(), [[expected]])
                        self.assertEqual(doc.validate(), [])

    def test_wrapper_cli_preserves_visible_tails_both_backends(self):
        # Run the real wrapper entry in a fresh interpreter; only select backend.
        bootstrap = (
            'import sys; sys.path.insert(0, sys.argv.pop(1)); '
            'import hwpx_table_parser as p; '
            'p._HAVE_LXML = sys.argv.pop(1) == "True"; '
            'import extract_tables; raise SystemExit(extract_tables.run())'
        )
        for text, expected in self.cases():
            source = self.package(text)
            for backend in (True, False):
                with self.subTest(expected=expected, lxml=backend):
                    result = subprocess.run(
                        [sys.executable, '-B', '-X', 'utf8', '-c', bootstrap,
                         str(TABLES), str(backend), str(source), '-f', 'json'],
                        capture_output=True, text=True, encoding='utf-8',
                        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assert_payload(json.loads(result.stdout), expected, backend)
                    for hidden in ('COMMENT', 'PRIVATE', 'review'):
                        self.assertNotIn(hidden, result.stderr)


if __name__ == '__main__':
    unittest.main()
