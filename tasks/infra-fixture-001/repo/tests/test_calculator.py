import unittest

from calculator import normalize_and_sum


class PublicTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(normalize_and_sum([1, 2.5, 3]), 6.5)

    def test_numeric_strings(self):
        self.assertEqual(normalize_and_sum([" 2 ", "3.5"]), 5.5)


if __name__ == "__main__":
    unittest.main()
