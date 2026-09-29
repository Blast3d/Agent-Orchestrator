"""The documented backend contract stays in step with the installed SQLite store."""
import inspect
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brain_interface import MemoryBackend
from brain_store import BrainStore


class ContractTests(unittest.TestCase):
    def test_protocol_methods_match_brain_store_parameters(self):
        for name in ('propose', 'approve', 'search', 'forget', 'supersede', 'export', 'changes'):
            with self.subTest(method=name):
                expected = list(inspect.signature(getattr(BrainStore, name)).parameters)
                self.assertEqual(list(inspect.signature(getattr(MemoryBackend, name)).parameters), expected)


if __name__ == '__main__':
    unittest.main()
