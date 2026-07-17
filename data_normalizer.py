"""Validation and Excel-friendly normalization of OCR results."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any

NUMERIC_FIELDS = ("qty", "price", "total_with_vat", "tax")
ITEM_FIELDS = ("name", "unit", *NUMERIC_FIELDS)
DOCUMENT_FIELDS = ("date", "seller", "invoice_number")
_MINUS_SIGNS = str.maketrans("", "", "-−–—")
_WITHOUT_VAT_RE = re.compile(r"^без\s+ндс$", re.IGNORECASE)


def normalize_numeric_value(value: Any, *, allow_without_vat: bool = False) -> str:
    """Return a locale-friendly textual number without sign or grouping spaces."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        raise ValueError("Числовое поле должно быть строкой или числом.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Числовое поле должно содержать конечное число.")
    text = str(value).strip()
    if allow_without_vat and _WITHOUT_VAT_RE.fullmatch(text):
        return "без НДС"
    text = re.sub(r"\s+", "", text).translate(_MINUS_SIGNS)
    if not text:
        return ""
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", text):
        raise ValueError(f"Некорректное числовое значение: {str(value)[:80]!r}.")

    separators = [(index, char) for index, char in enumerate(text) if char in ".,"]
    if not separators:
        normalized = text
    elif len({char for _, char in separators}) > 1:
        decimal_at = separators[-1][0]
        normalized = re.sub(r"[.,]", "", text[:decimal_at]) + "," + text[decimal_at + 1:]
    elif len(separators) > 1:
        parts = re.split(r"[.,]", text)
        if all(len(part) == 3 for part in parts[1:]):
            normalized = "".join(parts)
        else:
            normalized = "".join(parts[:-1]) + "," + parts[-1]
    else:
        normalized = text.replace(".", ",")

    try:
        number = Decimal(normalized.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Некорректное числовое значение.") from exc
    if not number.is_finite():
        raise ValueError("Числовое поле должно содержать конечное число.")
    return normalized


def excel_numeric_value(value: Any, *, allow_without_vat: bool = False) -> Decimal | str:
    """Convert a normalized numeric string to a true Excel number when possible."""
    normalized = normalize_numeric_value(value, allow_without_vat=allow_without_vat)
    if not normalized:
        return ""
    if normalized == "без НДС" and allow_without_vat:
        return normalized
    number = Decimal(normalized.replace(",", "."))
    if not number.is_finite():
        raise ValueError("Числовое поле должно содержать конечное число.")
    return number


def _text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        raise ValueError(f"Поле '{field_name}' должно быть строкой.")
    return str(value).strip()


def normalize_document(data: Any) -> dict[str, Any]:
    """Validate the expected schema and normalize only exported numeric fields."""
    if not isinstance(data, dict):
        raise ValueError("Ответ модели должен быть JSON-объектом.")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("Поле 'items' должно быть списком.")

    result: dict[str, Any] = {field: _text(data.get(field, ""), field) for field in DOCUMENT_FIELDS}
    result["items"] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Товарная строка {index} должна быть объектом.")
        normalized = {
            "name": _text(item.get("name", ""), "name"),
            "unit": _text(item.get("unit", ""), "unit"),
            "qty": normalize_numeric_value(item.get("qty", "")),
            "price": normalize_numeric_value(item.get("price", "")),
            "total_with_vat": normalize_numeric_value(item.get("total_with_vat", item.get("cost", ""))),
            "tax": normalize_numeric_value(item.get("tax", ""), allow_without_vat=True),
        }
        if not any(normalized.values()):
            raise ValueError(f"Товарная строка {index} полностью пуста.")
        result["items"].append(normalized)
    return result
