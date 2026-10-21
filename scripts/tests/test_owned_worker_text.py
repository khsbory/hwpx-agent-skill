import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from owned_worker import run_windows_owned

@unittest.skipUnless(os.name == 'nt', 'Windows owned worker only')
class OwnedWorkerTextTests(unittest.TestCase):
    def test_stdout_stderr_preserve_universal_newline_contract(self):
        program = "import os;os.write(1,b'a\\r\\nb\\rc\\n');os.write(2,b'd\\r\\ne\\rf\\n')"
        result = run_windows_owned([sys.executable, '-B', '-c', program], 10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'a\nb\nc\n')
        self.assertEqual(result.stderr, 'd\ne\nf\n')

if __name__ == '__main__':
    unittest.main()
