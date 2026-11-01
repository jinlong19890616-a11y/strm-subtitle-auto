import importlib.util
import unittest
from pathlib import Path

p = Path(__file__).parents[1] / 'strmsubtitleauto' / 'config' / 'config_schema.py'
# Full plugin imports MoviePilot runtime; syntax/import smoke is tested separately.
class TestSource(unittest.TestCase):
    def test_source_has_strm_defaults(self):
        s = p.read_text()
        self.assertIn('"auto_search_on_strm_transfer": True', s)
        self.assertIn('"strm_roots": ["/mnt/strm"]', s)
        self.assertIn('"strm_wait_seconds": 180', s)

if __name__ == '__main__': unittest.main()
