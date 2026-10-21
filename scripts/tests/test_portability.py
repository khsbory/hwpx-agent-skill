"""Source-release portability checks; never import the native vendor package."""
import ast
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
OWNED = (
    'test_core_regressions.py', 'test_core_review_fixes.py',
    'test_fresh_factory_regressions.py', 'test_signature_source_preservation.py',
    'test_table_cli_regressions.py', 'test_review_io_limits.py',
)


class SourcePortabilityTests(unittest.TestCase):
    def test_scratch_paths_are_not_personal_or_global_overrides(self):
        for name in OWNED:
            with self.subTest(file=name):
                tree = ast.parse((TESTS / name).read_text(encoding='utf-8'))
                literals = [n.value for n in ast.walk(tree)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                self.assertFalse(any('Users/khsbo' in s.replace('\\', '/') for s in literals))
                self.assertNotIn('tempfile.tempdir =', (TESTS / name).read_text(encoding='utf-8'))


class VendorSkipScopeTests(unittest.TestCase):
    def test_absent_vendor_skips_only_three_vendor_methods(self):
        import portability_support as support
        import test_fresh_factory_regressions as fresh
        import test_signature_source_preservation as signature
        suite = unittest.TestSuite([
            unittest.defaultTestLoader.loadTestsFromTestCase(fresh.VendorCompatibilityTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(signature.SourcePreservationTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(fresh.FreshFactoryTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(fresh.OwnershipProofTests),
        ])
        count = suite.countTestCases()
        result = unittest.TestResult()
        with patch.object(support.metadata, 'distribution', side_effect=support.metadata.PackageNotFoundError('pyhwpx')):
            suite.run(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])
        self.assertEqual(result.testsRun, count)
        self.assertEqual(len(result.skipped), 3)
        self.assertTrue(all('not installed' in reason for _, reason in result.skipped))


class ScratchLookupTests(unittest.TestCase):
    def test_explicit_tmpdir_and_unique_per_test_directories(self):
        from portability_support import scratch_directory
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or tempfile.gettempdir()) as root:
            with patch.dict(os.environ, {'TMPDIR': root}), patch('tempfile.gettempdir', side_effect=AssertionError('ignored TMPDIR')):
                scratch = scratch_directory('isolated-suite')
            self.assertEqual(scratch, Path(root) / 'isolated-suite')
            with tempfile.TemporaryDirectory(dir=scratch) as first, tempfile.TemporaryDirectory(dir=scratch) as second:
                self.assertNotEqual(first, second)
                self.assertEqual(Path(first).parent, scratch)
            self.assertFalse(Path(first).exists())
            self.assertFalse(Path(second).exists())

    def test_fallback_without_tmpdir(self):
        from portability_support import scratch_directory
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or tempfile.gettempdir()) as root:
            with patch.dict(os.environ, {'TMPDIR': ''}), patch('tempfile.gettempdir', return_value=root):
                self.assertEqual(scratch_directory('fallback'), Path(root) / 'fallback')


class VendorLookupTests(unittest.TestCase):
    def lookup(self, version='1.7.2', path=None, required=('core.py',)):
        import portability_support as support
        from types import SimpleNamespace
        dist = SimpleNamespace(version=version, locate_file=lambda name: path or TESTS / 'missing-vendor' / name)
        with patch.object(support.metadata, 'distribution', return_value=dist) as discover:
            source = support.vendor_source(required=required)
        discover.assert_called_once_with('pyhwpx')
        return source

    def test_installed_metadata_location_without_native_import(self):
        import builtins
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split('.')[0] in {'pyhwpx', 'pythoncom', 'win32com'}:
                raise AssertionError('native vendor import: ' + name)
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded):
            # Existing test source is only a path probe, never a fabricated vendor body.
            self.assertEqual(self.lookup(path=Path(__file__)), Path(__file__))

    def test_absent_distribution_is_explicit_skip(self):
        import portability_support as support
        with patch.object(support.metadata, 'distribution', side_effect=support.metadata.PackageNotFoundError('pyhwpx')):
            with self.assertRaisesRegex(unittest.SkipTest, 'pyhwpx 1.7.2.*not installed'):
                support.vendor_source()

    def test_unsupported_version_is_explicit_skip(self):
        with self.assertRaisesRegex(unittest.SkipTest, 'unsupported.*1.8.0'):
            self.lookup(version='1.8.0', path=Path(__file__))

    def test_missing_source_is_explicit_skip(self):
        with self.assertRaisesRegex(unittest.SkipTest, 'source.*missing'):
            self.lookup()

    def test_missing_helper_source_is_explicit_skip(self):
        with self.assertRaisesRegex(unittest.SkipTest, 'param_helpers.py'):
            self.lookup(path=Path(__file__), required=('core.py', 'param_helpers.py'))

    def test_directory_is_not_source_file(self):
        with self.assertRaisesRegex(unittest.SkipTest, 'source.*missing'):
            self.lookup(path=TESTS)

    def test_unexpected_metadata_error_is_not_skipped(self):
        import portability_support as support
        with patch.object(support.metadata, 'distribution', side_effect=RuntimeError('broken metadata')):
            with self.assertRaisesRegex(RuntimeError, 'broken metadata'):
                support.vendor_source()


if __name__ == '__main__':
    unittest.main()
