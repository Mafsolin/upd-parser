import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "build_portable.py"
RUNNER = ROOT / "standalone_upd" / "process_upd.py"


class PortableBuilderTests(unittest.TestCase):
    def test_builder_files_exist(self):
        self.assertTrue(BUILDER.is_file())
        self.assertTrue((ROOT / "build_portable.bat").is_file())

    def test_portable_batch_uses_bundled_python(self):
        module = self._load_builder_module()
        content = module.portable_batch_content()

        self.assertIn(r"runtime\python.exe", content)
        self.assertNotIn("where python", content.lower())
        self.assertIn("process_upd.py", content)
        self.assertIn("--cli", content)
        self.assertIn("pause", content.lower())

    def test_bootstrap_download_hash_is_verified(self):
        module = self._load_builder_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download.bin"
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                module.verify_sha256(path, "0" * 64)

    def test_copy_runtime_files_creates_expected_layout_without_runtime(self):
        module = self._load_builder_module()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "UPD_Parser_Portable"
            module.copy_app_files(target)

            expected = [
                "run_upd_parser.bat",
                "process_upd.py",
                "ai_parser.py",
                "config.py",
                "data_normalizer.py",
                "excel_writer.py",
                ".env.example",
                "input/README.txt",
                "output/README.txt",
            ]

            for relative in expected:
                with self.subTest(relative=relative):
                    self.assertTrue((target / relative).is_file())

    def test_runner_detects_project_root_when_copied_to_portable_root(self):
        runner_module = self._load_runner_module()

        with tempfile.TemporaryDirectory() as tmp:
            portable_root = Path(tmp)
            process_copy = portable_root / "process_upd.py"
            process_copy.write_text(RUNNER.read_text(encoding="utf-8"), encoding="utf-8")
            (portable_root / "ai_parser.py").write_text("", encoding="utf-8")

            self.assertEqual(
                runner_module.resolve_project_root(process_copy),
                portable_root.resolve(),
            )

    @staticmethod
    def _load_builder_module():
        spec = importlib.util.spec_from_file_location("build_portable", BUILDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _load_runner_module():
        spec = importlib.util.spec_from_file_location("standalone_process_upd", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


if __name__ == "__main__":
    unittest.main()
