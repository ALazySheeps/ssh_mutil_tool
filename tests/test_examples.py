import unittest
from pathlib import Path
from ssh_multi_tool.models import validate_profile
from ssh_multi_tool.storage import read_json


class ExampleTests(unittest.TestCase):
    def test_all_shipped_examples_are_importable(self):
        for path in Path("examples").glob("*.json"):
            with self.subTest(path=path): self.assertTrue(validate_profile(read_json(path))["windows"])
