import json
import os
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from credential_store import unprotect_secret

load_dotenv()


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    label: str
    api_url: str
    key_env: str
    models: tuple[str, ...]
    default_model: str
    api_key: str = ""


# The application intentionally has no built-in providers. Users add their own
# OpenAI-compatible endpoints in the Settings window.
PROVIDERS: dict[str, ProviderConfig] = {}
DEFAULT_PROVIDER_ID = ""
CUSTOM_PROVIDER_PREFIX = "custom:"


def normalize_api_url(value: str) -> str:
    """Accept an OpenAI base URL or a full chat-completions endpoint."""
    raw_url = value.strip()
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Базовый URL должен начинаться с http:// или https://.")
    if parsed.username or parsed.password:
        raise ValueError("URL не должен содержать имя пользователя или пароль.")
    if parsed.fragment:
        raise ValueError("URL не должен содержать fragment (#...).")
    if parsed.scheme == "http":
        host = parsed.hostname.casefold()
        is_loopback = host == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback:
            raise ValueError("Для удалённого провайдера требуется HTTPS.")
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _custom_providers_path() -> Path | None:
    raw_path = os.getenv("UPD_CUSTOM_PROFILES_FILE", "").strip()
    if raw_path:
        return Path(raw_path)
    default_path = Path(__file__).with_name("upd_provider_profiles.json")
    return default_path if default_path.is_file() else None


def _load_custom_providers() -> dict[str, ProviderConfig]:
    path = _custom_providers_path()
    if not path or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        profiles = payload.get("profiles", [])
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не удалось прочитать пользовательские профили: {exc}") from exc

    result: dict[str, ProviderConfig] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id", "")).strip()
        name = str(profile.get("name", "")).strip()
        model = str(profile.get("model", "")).strip()
        protected_key = str(profile.get("api_key_protected", "")).strip()
        api_key = unprotect_secret(protected_key) if protected_key else str(profile.get("api_key", "")).strip()
        base_url = str(profile.get("base_url", "")).strip()
        if not all((profile_id, name, model, api_key, base_url)):
            continue
        provider_id = f"{CUSTOM_PROVIDER_PREFIX}{profile_id}"
        result[provider_id] = ProviderConfig(
            id=provider_id,
            label=name,
            api_url=normalize_api_url(base_url),
            key_env="",
            models=(model,),
            default_model=model,
            api_key=api_key,
        )
    return result


def list_providers() -> dict[str, ProviderConfig]:
    """Return persisted providers configured by the user."""
    return _load_custom_providers()


def get_provider(provider_id: str | None = None) -> ProviderConfig:
    normalized = (provider_id or os.getenv("UPD_PROVIDER", DEFAULT_PROVIDER_ID)).strip().lower()
    if not normalized:
        raise ValueError("Провайдер не выбран. Добавьте его в настройках.")
    try:
        return list_providers()[normalized]
    except KeyError as exc:
        raise ValueError(f"Неизвестный провайдер: {provider_id or normalized}") from exc


def get_model(provider_id: str | None = None, model: str | None = None) -> str:
    provider = get_provider(provider_id)
    selected = (model or os.getenv("UPD_MODEL", "")).strip()
    if not selected:
        return provider.default_model
    if selected not in provider.models:
        raise ValueError(f"Модель '{selected}' недоступна для {provider.label}.")
    return selected


def get_api_key(provider_id: str | None = None) -> str:
    provider = get_provider(provider_id)
    if provider.api_key:
        return provider.api_key
    return os.getenv(provider.key_env, "").strip()


# Backward-compatible constants used by legacy entry points. They remain empty
# until a user selects a provider in the desktop application.
API_KEY = ""
API_URL = ""
MODEL = ""

# Paths
EXCEL_FILE = "upd_data.xlsx"
DOCUMENTS_PATH = "documents"

# Retry
RETRY_COUNT = 3
RETRY_DELAY = 5

# Images
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

# Prompt for LLM
EXTRACTION_PROMPT = """
Ты — строгий OCR-парсер УПД. Твоя цель — извлечь данные только из видимой табличной формы УПД и вернуть один валидный JSON. Не рассуждай в ответе, не добавляй markdown, не добавляй пояснения.

Главное правило точности:
Сначала мысленно восстанови всю таблицу УПД как full_table по строкам и колонкам, сверяясь с заголовками таблицы и номерами колонок. Только после этого заполни нужные поля JSON. Не бери значения по визуальной близости. Если заголовок/номер колонки не читается уверенно, оставь соответствующее поле пустым.

Ориентир по стандартным колонкам УПД/счета-фактуры:
- column 1: наименование товара/работы/услуги.
- column 1a: код вида товара или иной код. Do not use column 1a for quantity, price, tax or total.
- column 2 / 2a: единица измерения, код и условное обозначение.
- column 3: количество/объем. qty = column 3. Это единственный основной источник для "qty".
- column 4: цена/тариф за единицу.
- column 5: стоимость без налога. Do not use column 5 for "total_with_vat".
- column 7: ставка НДС.
- column 8: сумма НДС/сумма налога, предъявляемая покупателю.
- column 9: стоимость с налогом всего. total_with_vat = column 9. Это сумма строки уже с НДС.

Строгая проверка типов и смысла:
- "qty" — это количество, обычно небольшое число или десятичное значение из колонки 3. Не бери его из кода товара, артикула, номера строки, колонки 1a, штрихкода, ОКЕИ или номера документа.
- "total_with_vat" — это денежная сумма строки с НДС из колонки 9. Она не должна быть просто любой суммой на странице, суммой НДС, ценой за единицу или стоимостью без НДС.
- "tax" — это сумма НДС из колонки 8, а не ставка НДС из колонки 7.
- "price" — это цена за единицу из колонки 4.
- "unit" — это единица измерения из колонок 2/2a, например "шт", "кг", "усл. ед."; не пиши туда код товара.
- Все числовые значения возвращай строками в оригинальном формате из документа.
- Для полей qty, price, total_with_vat и tax возвращай только значение из назначенной колонки.
  Не ставь знак минус, не используй разделители тысяч пробелом и используй запятую как десятичный разделитель.
- Если значение выглядит не тем типом данных для поля, оставь поле пустым.

Проверка строки перед выводом:
Для каждой товарной строки проверь соответствие:
1. name взят из колонки 1 и не содержит номер строки.
2. qty взят из column 3, не из column 1a.
3. price взят из column 4.
4. tax взят из column 8.
5. total_with_vat взят из column 9, не из column 5 и не из итогов документа.
Если не можешь уверенно сопоставить ячейку с нужной колонкой, верни "" для этого поля.

Поставщик/продавец:
- "seller" заполняй только если поставщик/продавец явно виден в реквизитах документа.
- Если на фото поставщика нет или виден только покупатель/грузополучатель — верни "".
- Не угадывай поставщика по печати, логотипу, названию файла или контексту.

Формат ответа строго такой:
{
  "date": "ДД.ММ.ГГГГ",
  "seller": "Наименование поставщика/продавца",
  "invoice_number": "Номер счета-фактуры",
  "items": [
    {
      "name": "Наименование товара/услуги",
      "unit": "Ед. изм.",
      "qty": "Количество из колонки 3",
      "price": "Цена за единицу из колонки 4",
      "total_with_vat": "Сумма с НДС из колонки 9",
      "tax": "Сумма НДС из колонки 8"
    }
  ]
}

Дополнительные правила:
- Если товарных строк не видно — верни "items": [].
- Если любое поле не найдено или вызывает сомнение — верни пустую строку "".
- Не добавляй ключи вне указанной JSON-схемы.
- Не объединяй разные товары в одну строку.
- Не используй итоговую строку "Всего к оплате"/"Итого" как товарную строку.
- Ответ должен быть только JSON.
""".strip()
