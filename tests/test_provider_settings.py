import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
