import tempfile
import unittest
from pathlib import Path

from i18n import tr
from standalone_upd import process_upd


class LocalizationTests(unittest.TestCase):
    def test_russian_and_english_strings_are_available(self):
        self.assertEqual(tr("ru", "settings"), "Настройки")
        self.assertEqual(tr("en", "settings"), "Settings")
        self.assertEqual(tr("en", "files_selected", count=2), "Files selected: 2")

    def test_language_preference_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            process_upd.save_app_preferences(app_dir, "en", False)
            self.assertEqual(process_upd.load_language(app_dir), "en")
            self.assertFalse(process_upd.auto_update_enabled(app_dir))


if __name__ == "__main__":
    unittest.main()
