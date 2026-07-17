import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "standalone_upd" / "process_upd.py"


class SettingsPersistenceTests(unittest.TestCase):
    @staticmethod
    def load_runner():
        spec = importlib.util.spec_from_file_location("provider_settings_runner", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_custom_profile_lifecycle_and_base_url_resolution(self):
        module = self.load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            provider_id = module.save_custom_profile(
                app_dir, "Мой API", "https://api.example.com/v1", "secret", "vision-model",
            )
            self.assertTrue(provider_id.startswith("custom:"))
            stored_profiles = module.custom_profiles_path(app_dir).read_text(encoding="utf-8")
            self.assertNotIn('"api_key": "secret"', stored_profiles)
            self.assertIn("api_key_protected", stored_profiles)
            module.save_settings(app_dir, provider_id, "vision-model", "secret")

            profiles = module.list_provider_profiles(app_dir)
            profile = next(item for item in profiles if item["id"] == provider_id)
            self.assertEqual(profile["name"], "Мой API")

            module.save_custom_profile(
                app_dir, "Новое имя", "https://api.example.com/v1/chat/completions", "new-secret",
                "new-model", provider_id,
            )
            values = module.activate_settings(app_dir)
            self.assertEqual(values["UPD_PROVIDER"], provider_id)
            from config import get_api_key, get_provider
            self.assertEqual(get_provider(provider_id).api_url, "https://api.example.com/v1/chat/completions")
            self.assertEqual(get_api_key(provider_id), "new-secret")

            module.delete_custom_profile(app_dir, provider_id)
            self.assertNotIn(provider_id, [item["id"] for item in module.list_provider_profiles(app_dir)])
            self.assertNotIn("UPD_PROVIDER", module.load_settings(app_dir))

    def test_custom_profile_requires_complete_valid_connection_data(self):
        module = self.load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "Заполните"):
                module.save_custom_profile(Path(tmp), "", "https://api.example.com/v1", "key", "model")
            with self.assertRaisesRegex(ValueError, "Базовый URL"):
                module.save_custom_profile(Path(tmp), "API", "api.example.com", "key", "model")

    def test_no_profile_requires_opening_settings(self):
        module = self.load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "Провайдер не настроен"):
                module.ensure_api_key(Path(tmp))

    def test_cli_wizard_creates_first_provider_profile(self):
        module = self.load_runner()
        answers = iter(("My API", "https://api.example.com/v1", "vision-model", "secret-key"))
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            cp1252_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
            with mock.patch.object(module.sys, "stdout", cp1252_stdout):
                api_key = module.ensure_cli_provider(app_dir, prompt_fn=lambda _prompt: next(answers))
            self.assertEqual(api_key, "secret-key")
            profiles = module.list_provider_profiles(app_dir)
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]["name"], "My API")
            self.assertEqual(module.load_settings(app_dir)["UPD_PROVIDER"], profiles[0]["id"])

    def test_legacy_plaintext_key_is_migrated_on_activation(self):
        module = self.load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            module.custom_profiles_path(app_dir).write_text(
                json.dumps({"profiles": [{
                    "id": "legacy", "name": "Legacy", "base_url": "https://api.example.com/v1",
                    "api_key": "plain-secret", "model": "vision-model",
                }]}),
                encoding="utf-8",
            )
            module.activate_settings(app_dir)
            stored = module.custom_profiles_path(app_dir).read_text(encoding="utf-8")
            self.assertNotIn('"api_key": "plain-secret"', stored)
            self.assertIn("api_key_protected", stored)


if __name__ == "__main__":
    unittest.main()
