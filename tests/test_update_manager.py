import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from update_manager import ReleaseInfo, check_for_update, create_updater_script, download_update, parse_version


class UpdateManagerTests(unittest.TestCase):
    def response(self, payload, status=200):
        response = Mock(status_code=status)
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_version_comparison_and_exe_asset(self):
        payload = {
            "tag_name": "v1.1.0", "body": "Changes",
            "assets": [{
                "name": "UPD_Parser.exe",
                "browser_download_url": "https://github.com/Mafsolin/upd-parser/releases/download/v1.1.0/UPD_Parser.exe",
                "digest": "sha256:" + "a" * 64,
                "size": 123,
            }],
        }
        release = check_for_update("1.0.0", request_get=lambda _url, **_kwargs: self.response(payload))
        self.assertEqual(release.digest, "a" * 64)
        self.assertEqual(release.size, 123)

    def test_missing_or_equal_release_is_not_an_update(self):
        self.assertIsNone(check_for_update("1.0.0", request_get=lambda _url, **_kwargs: self.response({}, status=404)))
        payload = {"tag_name": "v1.0.0", "assets": []}
        self.assertIsNone(check_for_update("1.0.0", request_get=lambda _url, **_kwargs: self.response(payload)))

    def test_new_release_without_exe_is_rejected(self):
        payload = {"tag_name": "v1.0.1", "assets": []}
        with self.assertRaisesRegex(RuntimeError, "UPD_Parser.exe"):
            check_for_update("1.0.0", request_get=lambda _url, **_kwargs: self.response(payload))

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_version("newest")

    def test_malformed_release_payload_is_reported_cleanly(self):
        for payload in ([], {"tag_name": "v2.0.0", "assets": None}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                check_for_update("1.0.0", request_get=lambda _url, _payload=payload, **_kwargs: self.response(_payload))

    def test_download_rejects_digest_mismatch_and_non_executable(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.iter_content.return_value = [b"not-an-exe"]
        release = ReleaseInfo(
            "1.1.0",
            "https://github.com/Mafsolin/upd-parser/releases/download/v1.1.0/UPD_Parser.exe",
            "",
            "0" * 64,
            10,
        )
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            download_update(release, request_get=lambda _url, **_kwargs: response)

    def test_updater_replaces_only_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "UPD_Parser.exe"
            update = root / "new.exe"
            profiles = root / "upd_provider_profiles.json"
            profiles.write_text("{}", encoding="utf-8")
            script = create_updater_script(exe, update)
            content = script.read_text(encoding="utf-8-sig")
        self.assertIn(str(exe), content)
        self.assertNotIn(profiles.name, content)
        self.assertIn("Backup", content)
        self.assertIn("Start-Process", content)
        self.assertIn("Get-Process", content)
        self.assertNotIn("Wait-Process", content)


if __name__ == "__main__":
    unittest.main()
