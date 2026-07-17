import tempfile
import unittest
from pathlib import Path

import ai_parser
from config import EXTRACTION_PROMPT
from PIL import Image


class PromptContractTests(unittest.TestCase):
    def test_prompt_pins_quantity_and_vat_total_to_upd_table_columns(self):
        self.assertIn("qty = column 3", EXTRACTION_PROMPT)
        self.assertIn("total_with_vat = column 9", EXTRACTION_PROMPT)
        self.assertIn("Do not use column 1a", EXTRACTION_PROMPT)
        self.assertIn("Do not use column 5", EXTRACTION_PROMPT)
        self.assertIn("full_table", EXTRACTION_PROMPT)
        self.assertIn("используй запятую как десятичный разделитель", EXTRACTION_PROMPT)

    def test_parser_sends_extraction_prompt_as_system_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page001.jpg"
            Image.new("RGB", (2, 2), "white").save(image)

            messages = ai_parser._build_messages([image])

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], EXTRACTION_PROMPT)
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"][0]["type"], "text")
        self.assertIn("image_url", messages[1]["content"][1])


if __name__ == "__main__":
    unittest.main()
