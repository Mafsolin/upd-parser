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

    def test_processing_events_follow_selected_language(self):
        class Parser:
            def parse_document(self, _folder):
                return {"items": [{"name": "x", "qty": "1"}]}

        class Writer:
            def __init__(self, _path):
                pass

            def write(self, _data, _name):
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page.jpg"
            image.write_bytes(b"x")
            events = []
            process_upd.process_image_sequence(
                [image],
                Path(tmp) / "out.xlsx",
                parser_factory=Parser,
                writer_factory=Writer,
                on_progress=events.append,
                language="en",
            )
        messages = [event["message"] for event in events if "message" in event]
        self.assertTrue(any("Processing" in message for message in messages))
        self.assertFalse(any("Обрабатываю" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
