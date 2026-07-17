"""
ai_parser.py — отправка изображений в OpenAI-compatible API и разбор JSON-ответа.
"""

import base64
import json
import logging
import re
import time
from pathlib import Path

import requests

from data_normalizer import normalize_document
from config import (
    EXTRACTION_PROMPT,
    RETRY_COUNT,
    RETRY_DELAY,
    SUPPORTED_EXTENSIONS,
    get_api_key,
    get_model,
    get_provider,
)

logger = logging.getLogger(__name__)


def _image_to_base64(path: Path) -> tuple[str, str]:
    """Читает файл и возвращает (mime_type, base64_string)."""
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".bmp": "image/bmp", ".tiff": "image/tiff",
    }
    with open(path, "rb") as file:
        return mime_map.get(path.suffix.lower(), "image/jpeg"), base64.b64encode(file.read()).decode("utf-8")


def _collect_images(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def _build_messages(images: list[Path]) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": "Извлеки данные из приложенных изображений УПД по системным правилам."}]
    for image_path in images:
        mime, encoded = _image_to_base64(image_path)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    return [{"role": "system", "content": EXTRACTION_PROMPT}, {"role": "user", "content": content}]


def _extract_json(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    candidate = _extract_first_balanced_object(cleaned)
    if not candidate:
        raise ValueError(f"JSON-объект не найден в ответе модели:\n{raw[:500]}")
    for variant in _json_variants(candidate):
        try:
            return json.loads(variant)
        except json.JSONDecodeError:
            continue
    raise ValueError("Не удалось разобрать JSON из ответа модели после локального repair.")


def _extract_first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return text[start:]


def _json_variants(candidate: str) -> list[str]:
    variants = [candidate]
    sanitized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", candidate)
    if sanitized != candidate:
        variants.append(sanitized)
    no_trailing_commas = re.sub(r",\s*([}\]])", r"\1", sanitized)
    if no_trailing_commas != sanitized:
        variants.append(no_trailing_commas)
    missing_braces = no_trailing_commas.count("{") - no_trailing_commas.count("}")
    if missing_braces > 0:
        variants.append(no_trailing_commas + ("}" * missing_braces))
    return list(dict.fromkeys(variants))


def _api_error_message(response: requests.Response) -> str:
    status = response.status_code
    try:
        data = response.json()
        detail = data.get("error", data.get("message", ""))
        if isinstance(detail, dict):
            detail = detail.get("message", "")
    except ValueError:
        detail = response.text[:300]
    detail = str(detail).strip().replace("\n", " ")[:300]
    return f"HTTP {status}" + (f": {detail}" if detail else "")


class AIParser:
    """Отправляет страницы УПД в выбранный API и возвращает структурированные данные."""

    def __init__(self, provider_id: str | None = None, model: str | None = None, api_key: str | None = None):
        self.provider = get_provider(provider_id)
        self.model = get_model(self.provider.id, model)
        self.api_key = (api_key if api_key is not None else get_api_key(self.provider.id)).strip()
        if not self.api_key:
            raise RuntimeError(f"API-ключ для {self.provider.label} не задан. Укажите его в настройках.")
        self.headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @classmethod
    def ping(cls, provider_id: str | None = None, model: str | None = None, api_key: str | None = None) -> str:
        """Проверяет доступность выбранных endpoint, ключа и модели без отправки изображения."""
        parser = cls(provider_id, model, api_key)
        payload = {
            "model": parser.model,
            "messages": [{"role": "user", "content": "Ответь одним словом: OK"}],
            "temperature": 0,
            "max_tokens": 8,
        }
        try:
            response = requests.post(parser.provider.api_url, headers=parser.headers, json=payload, timeout=30)
            if not response.ok:
                raise RuntimeError(_api_error_message(response))
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                raise RuntimeError("Провайдер вернул пустой ответ.")
        except requests.RequestException as exc:
            raise RuntimeError(f"Не удалось подключиться к {parser.provider.label}: {exc}") from exc
        except (ValueError, IndexError, KeyError) as exc:
            raise RuntimeError("Провайдер вернул ответ в неожиданном формате.") from exc
        return f"{parser.provider.label}: модель {parser.model} доступна."

    def parse_document(self, folder: Path) -> dict:
        images = _collect_images(folder)
        if not images:
            raise FileNotFoundError(f"В папке '{folder}' не найдено изображений (поддерживаемые форматы: {SUPPORTED_EXTENSIONS}).")
        logger.info("[%s / %s] Найдено страниц: %d в '%s'", self.provider.label, self.model, len(images), folder.name)
        raw_response = self._call_api_with_retry(_build_messages(images), folder.name)
        try:
            return normalize_document(_extract_json(raw_response))
        except ValueError as exc:
            logger.warning("[%s] Не удалось разобрать JSON: %s", folder.name, exc)
            raise ValueError("Модель вернула невалидный JSON. Обработка остановлена, чтобы избежать выдуманных данных. Попробуйте отправить фото повторно.") from exc

    def _call_api_with_retry(self, messages: list[dict], doc_name: str) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 8192}
        last_error: Exception | None = None
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                response = requests.post(self.provider.api_url, headers=self.headers, json=payload, timeout=120)
                if not response.ok:
                    raise RuntimeError(_api_error_message(response))
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if not content:
                    raise ValueError("Пустой content в ответе API.")
                return content
            except (requests.RequestException, RuntimeError, ValueError, KeyError, IndexError) as exc:
                logger.warning("[%s] Ошибка %d/%d: %s", doc_name, attempt, RETRY_COUNT, exc)
                last_error = exc
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
        raise RuntimeError(f"[{doc_name}] {self.provider.label} не ответил корректно после {RETRY_COUNT} попыток. Последняя ошибка: {last_error}")
