"""Test-only scratch and vendor-source discovery, without importing COM."""
from importlib import metadata
import os
from pathlib import Path
import tempfile
import unittest


def scratch_directory(name):
    """Use an explicit TMPDIR first; keep suite fixtures in a named subdir."""
    root = Path(os.environ.get('TMPDIR') or tempfile.gettempdir()) / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def vendor_source(required=('core.py',)):
    """Find supported installed source using metadata only, never package import.

    Skip only callers that need vendor ASTs. Missing dependencies/source and
    unsupported versions are optional; corrupt metadata or ASTs remain failures.
    """
    try:
        dist = metadata.distribution('pyhwpx')
    except metadata.PackageNotFoundError:
        raise unittest.SkipTest('vendor AST requires pyhwpx 1.7.2: not installed')
    if dist.version != '1.7.2':
        raise unittest.SkipTest(
            f'vendor AST requires pyhwpx 1.7.2: unsupported version {dist.version}')
    source = Path(dist.locate_file('pyhwpx/core.py'))
    for name in dict.fromkeys(('core.py', *required)):
        path = source if name == 'core.py' else source.parent / name
        if not path.is_file():
            raise unittest.SkipTest(f'pyhwpx 1.7.2 vendor source missing: {path}')
    return source
