"""
ai_parser.py — отправка изображений в OpenAI-compatible API и разбор JSON-ответа.
"""

import base64
import io
import json
import logging
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from PIL import Image, ImageOps

from data_normalizer import normalize_document
from config import (
    EXTRACTION_PROMPT,
    ProviderConfig,
    RETRY_COUNT,
    RETRY_DELAY,
    SUPPORTED_EXTENSIONS,
    get_api_key,
    get_model,
    get_provider,
    normalize_api_url,
)

logger = logging.getLogger(__name__)


def _image_to_base64(path: Path) -> tuple[str, str]:
    """Читает файл и возвращает (mime_type, base64_string)."""
    suffix = path.suffix.lower()
    output_format, mime = {
        ".jpg": ("JPEG", "image/jpeg"), ".jpeg": ("JPEG", "image/jpeg"),
        ".png": ("PNG", "image/png"), ".webp": ("WEBP", "image/webp"),
        ".bmp": ("PNG", "image/png"), ".tif": ("PNG", "image/png"),
        ".tiff": ("PNG", "image/png"),
    }.get(suffix, ("JPEG", "image/jpeg"))
    with Image.open(path) as source:
        source.seek(0)  # Multi-page TIFFs are represented safely by their first page.
        prepared = ImageOps.exif_transpose(source).copy()
    if output_format == "JPEG" and prepared.mode not in ("RGB", "L"):
        prepared = prepared.convert("RGB")
    buffer = io.BytesIO()
    prepared.save(buffer, format=output_format)
    return mime, base64.b64encode(buffer.getvalue()).decode("utf-8")


def _collect_images(folder: Path) -> list[Path]:
    def natural_key(path: Path):
        return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name))
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=natural_key,
    )


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
        if isinstance(data, dict):
            detail = data.get("error", data.get("message", ""))
            if isinstance(detail, dict):
                detail = detail.get("message", "")
        else:
            detail = response.text[:300]
    except ValueError:
        detail = response.text[:300]
    detail = str(detail).strip().replace("\n", " ")[:300]
    return f"HTTP {status}" + (f": {detail}" if detail else "")


def _response_content(response: requests.Response) -> str:
    """Validate the common OpenAI-compatible response shape in one place."""
    content_type = str(response.headers.get("Content-Type", "unknown")).split(";", 1)[0].strip() or "unknown"
    unexpected = f"Провайдер вернул ответ в неожиданном формате (Content-Type: {content_type})."
    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise ValueError(unexpected) from exc
    if not isinstance(data, dict):
        raise ValueError(unexpected)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError(unexpected)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError(unexpected)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"Провайдер вернул пустой content (Content-Type: {content_type}).")
    return content


def _retry_after_seconds(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After") if response.headers else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - parsedate_to_datetime(response.headers.get("Date"))).total_seconds()
            return max(0.0, delay)
        except (TypeError, ValueError, OverflowError):
            return None


class AIParser:
    """Отправляет страницы УПД в выбранный API и возвращает структурированные данные."""

    def __init__(self, provider_id: str | None = None, model: str | None = None, api_key: str | None = None,
                 progress_callback=None):
        self.provider = get_provider(provider_id)
        self.model = get_model(self.provider.id, model)
        self.api_key = (api_key if api_key is not None else get_api_key(self.provider.id)).strip()
        if not self.api_key:
            raise RuntimeError(f"API-ключ для {self.provider.label} не задан. Укажите его в настройках.")
        self.headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        self.progress_callback = progress_callback

    def _redact(self, value: object) -> str:
        text = str(value)
        return text.replace(self.api_key, "***") if self.api_key else text

    @classmethod
    def ping(cls, provider_id: str | None = None, model: str | None = None, api_key: str | None = None) -> str:
        """Проверяет доступность выбранных endpoint, ключа и модели без отправки изображения."""
        parser = cls(provider_id, model, api_key)
        return cls._ping_parser(parser)

    @classmethod
    def ping_connection(cls, label: str, base_url: str, model: str, api_key: str) -> str:
        """Check an unsaved provider draft without mutating the active configuration."""
        normalized_label = label.strip()
        normalized_model = model.strip()
        normalized_key = api_key.strip()
        if not all((normalized_label, normalized_model, normalized_key)):
            raise RuntimeError("Заполните название, API-ключ и модель.")
        parser = cls.__new__(cls)
        parser.provider = ProviderConfig(
            id="draft",
            label=normalized_label,
            api_url=normalize_api_url(base_url),
            key_env="",
            models=(normalized_model,),
            default_model=normalized_model,
            api_key=normalized_key,
        )
        parser.model = normalized_model
        parser.api_key = normalized_key
        parser.headers = {"Authorization": f"Bearer {normalized_key}", "Content-Type": "application/json"}
        parser.progress_callback = None
        return cls._ping_parser(parser)

    @staticmethod
    def _ping_parser(parser: "AIParser") -> str:
        payload = {
            "model": parser.model,
            "messages": [{"role": "user", "content": "Ответь одним словом: OK"}],
            "temperature": 0,
            "max_tokens": 8,
            "stream": False,
        }
        try:
            response = requests.post(parser.provider.api_url, headers=parser.headers, json=payload, timeout=30)
            if not response.ok:
                raise RuntimeError(parser._redact(_api_error_message(response)))
            _response_content(response)
        except requests.RequestException as exc:
            raise RuntimeError(f"Не удалось подключиться к {parser.provider.label}: {parser._redact(exc)}") from exc
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
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 8192,
            "stream": False,
        }
        last_error: Exception | None = None
        for attempt in range(1, RETRY_COUNT + 1):
            callback = getattr(self, "progress_callback", None)
            if callback:
                callback(attempt)
            retry_delay = RETRY_DELAY
            try:
                response = requests.post(self.provider.api_url, headers=self.headers, json=payload, timeout=120)
                if not response.ok:
                    error = RuntimeError(self._redact(_api_error_message(response)))
                    if response.status_code not in (408, 429) and not 500 <= response.status_code <= 599:
                        raise error
                    retry_delay = _retry_after_seconds(response) or RETRY_DELAY
                    last_error = error
                else:
                    return _response_content(response)
            except requests.RequestException as exc:
                last_error = RuntimeError(self._redact(exc))
            except ValueError as exc:
                # A successful but malformed response is deterministic and must not be retried.
                raise RuntimeError(self._redact(exc)) from exc
            except RuntimeError:
                raise
            if last_error is not None:
                logger.warning("[%s] Ошибка %d/%d: %s", doc_name, attempt, RETRY_COUNT, self._redact(last_error))
            if attempt < RETRY_COUNT:
                time.sleep(retry_delay)
        raise RuntimeError(f"[{doc_name}] {self.provider.label} не ответил корректно после {RETRY_COUNT} попыток. Последняя ошибка: {self._redact(last_error)}")
