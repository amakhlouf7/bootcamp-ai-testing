import csv
import importlib.util
import os
import tempfile
import unittest


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODULE_PATH = os.path.join(
    ROOT_DIR,
    ".github",
    "skills",
    "manualTests",
    "scripts",
    "import_manual_tests.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("import_manual_tests", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportManualTestsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_build_manual_test_payload_has_empty_steps_array(self):
        payload = self.module.build_manual_test_payload(
            {
                "name": "My test",
                "description": "desc",
                "steps": [],
                "result": "ok",
            }
        )

        fields = payload["fields"]
        self.assertIn(self.module.MANUAL_TEST_STEPS_FIELD, fields)
        self.assertEqual(fields[self.module.MANUAL_TEST_STEPS_FIELD], {"steps": []})

    def test_build_manual_test_payload_maps_steps(self):
        payload = self.module.build_manual_test_payload(
            {
                "name": "My test",
                "description": "desc",
                "steps": [{"step": "click"}, {"step": "submit"}],
                "result": "Success",
            }
        )

        steps = payload["fields"][self.module.MANUAL_TEST_STEPS_FIELD]["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["index"], 1)
        self.assertEqual(steps[0]["fields"]["action"], "click")
        self.assertEqual(steps[0]["fields"]["expected result"], "Success")

    def test_load_csv_keeps_issue_key_per_test_identifier(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", suffix=".csv", delete=False) as tmp:
            writer = csv.writer(tmp, delimiter=";")
            writer.writerow(
                [
                    "Test Case Identifier",
                    "Type",
                    "Summary",
                    "Action",
                    "Data",
                    "Expected Result",
                    "Test plan",
                    "Issue key",
                    "Test Set",
                ]
            )
            writer.writerow(["TC-1", "Manual", "Alpha", "Do A", "", "A ok", "", "PROJ-1", ""])
            writer.writerow(["TC-1", "Manual", "Alpha", "Do B", "", "A ok", "", "", ""])
            writer.writerow(["TC-2", "Manual", "Beta", "Do C", "", "B ok", "", "PROJ-2", ""])
            file_name = tmp.name

        try:
            cases = self.module.load_csv(file_name)
        finally:
            os.remove(file_name)

        by_name = {case["name"]: case for case in cases}
        self.assertEqual(by_name["Alpha"]["story_key"], "PROJ-1")
        self.assertEqual(by_name["Beta"]["story_key"], "PROJ-2")
        self.assertEqual(len(by_name["Alpha"]["steps"]), 2)


if __name__ == "__main__":
    unittest.main()