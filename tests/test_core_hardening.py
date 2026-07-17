import base64
import io
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from openpyxl import load_workbook
from PIL import Image

import ai_parser
from data_normalizer import excel_numeric_value, normalize_document, normalize_numeric_value
from excel_writer import ExcelWriter


class StrictNormalizationTests(unittest.TestCase):
    def test_numeric_grammar_accepts_locale_decimals_and_grouping(self):
        self.assertEqual(normalize_numeric_value("-1 234,50"), "1234,50")
        self.assertEqual(normalize_numeric_value("1.234.567,89"), "1234567,89")
        self.assertEqual(normalize_numeric_value("12.50"), "12,50")

    def test_numeric_grammar_rejects_non_finite_and_junk(self):
        for value in ("NaN", "Infinity", "12kg", math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_numeric_value(value)

    def test_without_vat_is_preserved_only_in_tax(self):
        result = normalize_document({"items": [{"name": "x", "tax": "без НДС"}]})
        self.assertEqual(result["items"][0]["tax"], "без НДС")
        for field in ("qty", "price", "total_with_vat"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                normalize_document({"items": [{"name": "x", field: "без НДС"}]})

    def test_empty_values_are_allowed_but_fully_empty_item_is_rejected(self):
        self.assertEqual(normalize_document({"items": [{"name": "x", "qty": ""}]})["items"][0]["qty"], "")
        with self.assertRaisesRegex(ValueError, "пуст"):
            normalize_document({"items": [{}]})

    def test_excel_conversion_never_returns_non_finite_decimal(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                excel_numeric_value(value)


class ExcelSafetyTests(unittest.TestCase):
    def test_ocr_text_is_escaped_from_formulas_while_numbers_stay_numeric(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "safe.xlsx"
            ExcelWriter(path).write({
                "date": "=1+1", "seller": "+SUM(A1:A2)", "invoice_number": "@cmd",
                "items": [{"name": "-2+3", "unit": "=X", "qty": "2", "price": "3.5", "total_with_vat": "7"}],
            }, "doc")
            workbook = load_workbook(path, data_only=False)
            row = workbook.active[2]
            values = [cell.value for cell in row]
            types = [cell.data_type for cell in row]
            workbook.close()
        for column in (1, 2, 3, 8, 9):
            self.assertTrue(values[column].startswith("'"), values[column])
            self.assertEqual(types[column], "s")
        self.assertEqual(values[4:7], [2, 3.5, 7])


class ImagePreparationTests(unittest.TestCase):
    def test_pages_use_natural_sort(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ("page10.jpg", "page2.jpg", "page1.jpg"):
                (folder / name).write_bytes(b"x")
            self.assertEqual([p.name for p in ai_parser._collect_images(folder)], ["page1.jpg", "page2.jpg", "page10.jpg"])

    def test_bmp_is_transcoded_to_supported_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.bmp"
            Image.new("RGB", (3, 2), "red").save(path)
            mime, encoded = ai_parser._image_to_base64(path)
        self.assertEqual(mime, "image/png")
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG"))

    def test_exif_orientation_is_applied_before_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oriented.jpg"
            image = Image.new("RGB", (4, 2), "blue")
            exif = image.getexif()
            exif[274] = 6
            image.save(path, exif=exif)
            mime, encoded = ai_parser._image_to_base64(path)
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as prepared:
                size = prepared.size
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(size, (2, 4))

    def test_tiff_uses_safe_first_frame_as_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "multi.tiff"
            frames = [Image.new("RGB", (3, 2), color) for color in ("red", "blue")]
            frames[0].save(path, save_all=True, append_images=frames[1:])
            mime, encoded = ai_parser._image_to_base64(path)
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as prepared:
                size = prepared.size
        self.assertEqual((mime, size), ("image/png", (3, 2)))


class ApiHardeningTests(unittest.TestCase):
    @staticmethod
    def parser(callback=None):
        parser = ai_parser.AIParser.__new__(ai_parser.AIParser)
        parser.provider = type("Provider", (), {"api_url": "https://example.test", "label": "Test"})()
        parser.model = "model"
        parser.api_key = "secret-token"
        parser.headers = {"Authorization": "Bearer secret-token"}
        parser.progress_callback = callback
        return parser

    def test_response_schema_rejects_null_or_array_without_type_errors(self):
        for payload in ({"choices": None}, [], {"choices": []}, {"choices": [None]}):
            response = Mock(ok=True)
            response.json.return_value = payload
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "формат"):
                ai_parser._response_content(response)

    def test_malformed_error_payload_still_produces_safe_http_error(self):
        response = Mock(status_code=500, text="fallback", headers={})
        response.json.return_value = ["not", "an", "object"]
        self.assertEqual(ai_parser._api_error_message(response), "HTTP 500: fallback")

    @patch("ai_parser.time.sleep")
    @patch("ai_parser.requests.post")
    def test_does_not_retry_client_errors_and_redacts_echoed_key(self, post, _sleep):
        response = Mock(ok=False, status_code=401, headers={}, text="secret-token")
        response.json.return_value = {"error": {"message": "bad secret-token"}}
        post.return_value = response
        with self.assertRaises(RuntimeError) as caught:
            self.parser()._call_api_with_retry([], "doc")
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("secret-token", str(caught.exception))

    @patch("ai_parser.time.sleep")
    @patch("ai_parser.requests.post")
    def test_retries_network_and_retryable_http_with_retry_after(self, post, sleep):
        busy = Mock(ok=False, status_code=429, headers={"Retry-After": "3"}, text="busy")
        busy.json.return_value = {"error": "busy"}
        good = Mock(ok=True)
        good.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        post.side_effect = [requests.ConnectionError("offline"), busy, good]
        progress = []
        result = self.parser(progress.append)._call_api_with_retry([], "doc")
        self.assertEqual(result, "OK")
        self.assertEqual(post.call_count, 3)
        self.assertIn(3.0, [call.args[0] for call in sleep.call_args_list])
        self.assertEqual(progress, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
