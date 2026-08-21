import unittest

from ug_experiment_calculator.repository import parse_configuration_project


class ParseConfigurationProjectTests(unittest.TestCase):
    def test_project_key_is_case_insensitive(self) -> None:
        configuration = (
            "https://jira.example/browse/UGP-123\n"
            "Project: https://alice.mu.se/pages/viewpage.action?pageId=7856"
        )

        self.assertEqual(
            parse_configuration_project(configuration),
            "https://alice.mu.se/pages/viewpage.action?pageId=7856",
        )

    def test_lowercase_project_key_still_works(self) -> None:
        self.assertEqual(
            parse_configuration_project(
                "project: https://alice.mu.se/pages/viewpage.action?pageId=123"
            ),
            "https://alice.mu.se/pages/viewpage.action?pageId=123",
        )


if __name__ == "__main__":
    unittest.main()
