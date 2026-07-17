"""Validation and Excel-friendly normalization of OCR results."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

NUMERIC_FIELDS = ("qty", "price", "total_with_vat", "tax")
ITEM_FIELDS = ("name", "unit", *NUMERIC_FIELDS)
DOCUMENT_FIELDS = ("date", "seller", "invoice_number")
_MINUS_SIGNS = str.maketrans("", "", "-−–—")


def normalize_numeric_value(value: Any) -> str:
    """Return a locale-friendly textual number without sign or grouping spaces."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        raise ValueError("Числовое поле должно быть строкой или числом.")
    return str(value).replace("\u00a0", "").replace(" ", "").translate(_MINUS_SIGNS).replace(".", ",").strip()


def excel_numeric_value(value: Any) -> Decimal | str:
    """Convert a normalized numeric string to a true Excel number when possible."""
    normalized = normalize_numeric_value(value)
    if not normalized:
        return ""
    try:
        return Decimal(normalized.replace(",", "."))
    except InvalidOperation:
        # Values such as "без НДС" remain text instead of being corrupted.
        return normalized


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
            "tax": normalize_numeric_value(item.get("tax", "")),
        }
        result["items"].append(normalized)
    return result
