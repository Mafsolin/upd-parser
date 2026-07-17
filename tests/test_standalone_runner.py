import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = ROOT / "standalone_upd"


class StandaloneRunnerTests(unittest.TestCase):
    def test_runner_folder_has_expected_user_files(self):
        self.assertTrue((RUNNER_DIR / "run_upd_parser.bat").is_file())
        self.assertTrue((RUNNER_DIR / "process_upd.py").is_file())
        self.assertTrue((RUNNER_DIR / "input" / "README.txt").is_file())
        self.assertTrue((RUNNER_DIR / "output" / "README.txt").is_file())

    def test_batch_file_runs_python_processor_and_keeps_console_open(self):
        content = (RUNNER_DIR / "run_upd_parser.bat").read_text(encoding="utf-8")

        self.assertIn("process_upd.py", content)
        self.assertIn("--cli", content)
        self.assertIn("requirements.txt", content)
        self.assertIn("pause", content.lower())
        self.assertIn("input", content)
        self.assertIn("output", content)

    def test_collect_input_images_filters_and_sorts_supported_files(self):
        module = self._load_runner_module()

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ["page2.jpg", "notes.txt", "page1.PNG", "scan.webp"]:
                (folder / name).write_text("x", encoding="utf-8")

            names = [path.name for path in module.collect_input_images(folder)]

        self.assertEqual(names, ["page1.PNG", "page2.jpg", "scan.webp"])

    def test_output_path_uses_output_folder_and_xlsx_extension(self):
        module = self._load_runner_module()
        output_path = module.build_output_path(RUNNER_DIR / "output")

        self.assertEqual(output_path.parent, RUNNER_DIR / "output")
        self.assertEqual(output_path.suffix, ".xlsx")
        self.assertTrue(output_path.name.startswith("upd_result_"))

    @staticmethod
    def _load_runner_module():
        spec = importlib.util.spec_from_file_location(
            "standalone_process_upd",
            RUNNER_DIR / "process_upd.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


if __name__ == "__main__":
    unittest.main()
