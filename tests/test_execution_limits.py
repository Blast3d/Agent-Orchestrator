import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from execution_limits import timeout_for_task


class TimeoutForTaskTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(timeout_for_task("tiny"), 120)
        self.assertEqual(timeout_for_task("small"), 450)
        self.assertEqual(timeout_for_task("medium"), 900)
        self.assertEqual(timeout_for_task("large"), 1350)

    def test_override_boundaries(self):
        self.assertEqual(timeout_for_task("tiny", 30), 30)
        self.assertEqual(timeout_for_task("large", 1800), 1800)

    def test_override_out_of_range(self):
        with self.assertRaises(ValueError):
            timeout_for_task("small", 29)
        with self.assertRaises(ValueError):
            timeout_for_task("small", 1801)

    def test_override_bool_rejected(self):
        with self.assertRaises(ValueError):
            timeout_for_task("small", True)
        with self.assertRaises(ValueError):
            timeout_for_task("small", False)

    def test_override_float_rejected(self):
        with self.assertRaises(ValueError):
            timeout_for_task("small", 100.0)
        with self.assertRaises(ValueError):
            timeout_for_task("small", float("inf"))
        with self.assertRaises(ValueError):
            timeout_for_task("small", float("nan"))

    def test_override_string_rejected(self):
        with self.assertRaises(ValueError):
            timeout_for_task("small", "120")

    def test_unknown_size(self):
        with self.assertRaises(ValueError):
            timeout_for_task("huge")
        with self.assertRaises(ValueError):
            timeout_for_task(None)
        with self.assertRaises(ValueError):
            timeout_for_task(123)

    def test_unknown_size_with_override(self):
        with self.assertRaises(ValueError):
            timeout_for_task("huge", 300)
        with self.assertRaises(ValueError):
            timeout_for_task("", 300)


if __name__ == "__main__":
    unittest.main()
