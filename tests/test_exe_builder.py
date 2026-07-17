import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "standalone_upd" / "process_upd.py"
EXE_BUILDER = ROOT / "build_onefile_exe.py"


class ExeBuilderTests(unittest.TestCase):
    def test_exe_builder_files_exist(self):
        self.assertTrue(EXE_BUILDER.is_file())
        self.assertTrue((ROOT / "build_onefile_exe.bat").is_file())

    def test_pyinstaller_command_builds_one_windowed_exe(self):
        module = self._load_builder_module()
        command = module.pyinstaller_command()

        self.assertIn("--onefile", command)
        self.assertEqual(command[command.index("credential_store") - 1], "--hidden-import")
        self.assertIn("--windowed", command)
        self.assertNotIn("--console", command)
        self.assertIn("--name", command)
        self.assertIn("UPD_Parser", command)
        self.assertIn("tkinter", command)
        self.assertIn(str(RUNNER), command)

    def test_runner_uses_executable_folder_when_frozen(self):
        module = self._load_runner_module()

        with mock.patch.object(module.sys, "frozen", True, create=True), mock.patch.object(
            module.sys,
            "executable",
            r"C:\Tools\UPD_Parser.exe",
        ):
            self.assertEqual(module.resolve_app_dir(Path("ignored.py")), Path(r"C:\Tools"))

    def test_runner_requires_provider_configuration_for_one_file_exe(self):
        module = self._load_runner_module()

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "Провайдер не настроен"):
                module.ensure_api_key(Path(tmp))

    def test_runner_self_test_imports_packaged_dependencies(self):
        module = self._load_runner_module()
        self.assertEqual(module.self_test(), 0)

    @staticmethod
    def _load_runner_module():
        spec = importlib.util.spec_from_file_location("standalone_process_upd", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _load_builder_module():
        spec = importlib.util.spec_from_file_location("build_onefile_exe", EXE_BUILDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


if __name__ == "__main__":
    unittest.main()
