import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseHardeningTests(unittest.TestCase):
    def test_runtime_dependencies_are_exactly_pinned(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        packages = [line for line in requirements if line and not line.startswith("#")]
        self.assertTrue(packages)
        self.assertTrue(all("==" in package for package in packages))

    def test_release_validates_tag_version_and_uses_split_permissions(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("Verify tag matches APP_VERSION", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        self.assertIn("actions/download-artifact@", workflow)
        self.assertIn("path: release-assets/*", workflow)
        self.assertNotIn("../UPD_Parser_OneFile/UPD_Parser.exe\n            UPD_Parser_Portable.zip", workflow)

    def test_regular_ci_runs_without_a_release_tag(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("push:", workflow)
        self.assertIn("unittest discover", workflow)

    def test_public_templates_do_not_reference_removed_bot(self):
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8").casefold()
        portable_builder = (ROOT / "build_portable.py").read_text(encoding="utf-8").casefold()
        self.assertNotIn("telegram", env_example)
        self.assertNotIn("routerai", portable_builder)
        self.assertIn("raw.githubusercontent.com/pypa/get-pip/", portable_builder)
        self.assertNotIn('GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"', portable_builder)


if __name__ == "__main__":
    unittest.main()
