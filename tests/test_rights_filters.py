import unittest

from ug_experiment_calculator.repository import _experiment_users_query_filters


class RightsFilterDigitTest(unittest.TestCase):
    def test_each_rights_filter_uses_its_own_digit(self):
        expected_divisors = {"pro": 1, "edu": 10, "sing": 100, "practice": 1000, "book": 10000}
        for rights_type, divisor in expected_divisors.items():
            with self.subTest(rights_type=rights_type):
                _, having_filter = _experiment_users_query_filters({"id": 1}, {f"{rights_type}_rights": "free"})
                self.assertIn(f"toUInt32(rights / {divisor}) % 10 in (0, 4, 5)", having_filter)


if __name__ == "__main__":
    unittest.main()
