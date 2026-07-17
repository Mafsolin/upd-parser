import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ai_parser
import config


class ProviderConfigTests(unittest.TestCase):
    def _profile_file(self, directory: Path) -> tuple[Path, str]:
        path = directory / "profiles.json"
        profile_id = "custom:test-provider"
        path.write_text(json.dumps({"profiles": [{
            "id": "test-provider", "name": "Тестовый API", "base_url": "https://api.example.com/v1",
            "api_key": "secret", "model": "vision-model",
        }]}), encoding="utf-8")
        return path, profile_id

    def test_no_built_in_providers_are_available(self):
        self.assertEqual(config.PROVIDERS, {})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Добавьте"):
                config.get_provider()

    def test_custom_provider_uses_base_url_and_its_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, provider_id = self._profile_file(Path(tmp))
            with patch.dict(os.environ, {"UPD_CUSTOM_PROFILES_FILE": str(path)}, clear=True):
                provider = config.get_provider(provider_id)
                self.assertEqual(provider.api_url, "https://api.example.com/v1/chat/completions")
                self.assertEqual(config.get_model(provider_id), "vision-model")
                self.assertEqual(config.get_api_key(provider_id), "secret")


class ProviderPingTests(unittest.TestCase):
    @patch("ai_parser.requests.post")
    def test_ping_sends_selected_model_and_auth_header(self, post):
        response = Mock(ok=True)
        response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        post.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{
                "id": "test", "name": "Тест", "base_url": "https://api.example.com/v1",
                "api_key": "secret-token", "model": "vision-model",
            }]}), encoding="utf-8")
            with patch.dict(os.environ, {"UPD_CUSTOM_PROFILES_FILE": str(path)}, clear=True):
                result = ai_parser.AIParser.ping("custom:test", "vision-model", "secret-token")
        self.assertIn("доступна", result)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "vision-model")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(post.call_args.args[0], "https://api.example.com/v1/chat/completions")

    @patch("ai_parser.requests.post")
    def test_ping_error_does_not_expose_key(self, post):
        response = Mock(ok=False, status_code=401, text="Unauthorized")
        response.json.return_value = {"error": {"message": "Invalid API key"}}
        post.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{
                "id": "test", "name": "Тест", "base_url": "https://api.example.com/v1",
                "api_key": "secret-token", "model": "vision-model",
            }]}), encoding="utf-8")
            with patch.dict(os.environ, {"UPD_CUSTOM_PROFILES_FILE": str(path)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "HTTP 401") as context:
                    ai_parser.AIParser.ping("custom:test", "vision-model", "secret-token")
        self.assertNotIn("secret-token", str(context.exception))

    @patch("ai_parser.requests.post")
    def test_ping_redacts_key_echoed_by_provider(self, post):
        response = Mock(ok=False, status_code=401, text="secret-token")
        response.json.return_value = {"error": {"message": "rejected secret-token"}}
        post.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{
                "id": "test", "name": "Тест", "base_url": "https://api.example.com/v1",
                "api_key": "secret-token", "model": "vision-model",
            }]}), encoding="utf-8")
            with patch.dict(os.environ, {"UPD_CUSTOM_PROFILES_FILE": str(path)}, clear=True):
                with self.assertRaises(RuntimeError) as context:
                    ai_parser.AIParser.ping("custom:test", "vision-model", "secret-token")
        self.assertNotIn("secret-token", str(context.exception))

    @patch("ai_parser.requests.post", side_effect=ai_parser.requests.ConnectionError("failed secret-token"))
    def test_ping_redacts_key_from_network_error(self, _post):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{
                "id": "test", "name": "Тест", "base_url": "https://api.example.com/v1",
                "api_key": "secret-token", "model": "vision-model",
            }]}), encoding="utf-8")
            with patch.dict(os.environ, {"UPD_CUSTOM_PROFILES_FILE": str(path)}, clear=True):
                with self.assertRaises(RuntimeError) as context:
                    ai_parser.AIParser.ping("custom:test", "vision-model", "secret-token")
        self.assertNotIn("secret-token", str(context.exception))


if __name__ == "__main__":
    unittest.main()
