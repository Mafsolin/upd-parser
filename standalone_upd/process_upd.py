"""
Local UPD processor.

GUI mode:
    Double-click the one-file exe and select images in the window.

CLI mode:
    process_upd.py --cli
    standalone_upd/input -> standalone_upd/output

Frozen one-file exe GUI mode:
    User chooses where to save the Excel file.
"""

from __future__ import annotations

import logging
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import uuid
from builtins import input
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

ProgressCallback = Callable[[dict[str, Any]], None]
_USE_STDIN_PROMPT = object()
CUSTOM_PROFILES_FILE = "upd_provider_profiles.json"
DEFAULT_LANGUAGE = "ru"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_app_dir(script_path: Path) -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return script_path.resolve().parent


def resolve_project_root(script_path: Path) -> Path:
    script_dir = script_path.resolve().parent
    if (script_dir / "ai_parser.py").exists():
        return script_dir
    return script_dir.parent


APP_DIR = resolve_app_dir(Path(__file__))
PROJECT_ROOT = APP_DIR if is_frozen() else resolve_project_root(Path(__file__))
INPUT_DIR = APP_DIR / "input"
OUTPUT_DIR = APP_DIR / "output"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_version import APP_VERSION, GITHUB_URL  # noqa: E402
from i18n import LANGUAGES, tr  # noqa: E402
from update_manager import apply_downloaded_update, check_for_update, download_update  # noqa: E402
from credential_store import protect_secret, unprotect_secret  # noqa: E402

logger = logging.getLogger("standalone_upd")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def collect_input_images(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def build_output_path(output_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"upd_result_{stamp}.xlsx"


def default_gui_save_dir() -> Path:
    return APP_DIR if APP_DIR.exists() else Path.cwd()


def ask_excel_output_path(parent, initial_dir: Path | None = None, language: str | None = None) -> Path | None:
    from tkinter import filedialog

    language = language or load_language(APP_DIR)
    save_dir = Path(initial_dir) if initial_dir else default_gui_save_dir()
    default_path = build_output_path(save_dir)
    raw_path = filedialog.asksaveasfilename(
        parent=parent,
        title=tr(language, "save_excel"),
        initialdir=str(save_dir),
        initialfile=default_path.name,
        defaultextension=".xlsx",
        filetypes=[
            ("Excel", "*.xlsx"),
            (tr(language, "all_files"), "*.*"),
        ],
        confirmoverwrite=True,
    )
    if not raw_path:
        return None

    path = Path(raw_path)
    if not path.suffix:
        path = path.with_suffix(".xlsx")
    return path


def prepare_selected_excel_path(path: Path | str) -> Path:
    prepared = Path(path)
    if prepared.suffix.lower() != ".xlsx":
        prepared = prepared.with_suffix(".xlsx")
    prepared.parent.mkdir(parents=True, exist_ok=True)
    if prepared.exists() and not prepared.is_file():
        raise IsADirectoryError(f"Выбранный путь не является файлом: {prepared}")
    return prepared


def build_staging_excel_path(destination: Path | str) -> Path:
    """Return a unique sibling path so the final report can be replaced atomically."""
    destination = Path(destination)
    return destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.tmp.xlsx")


def commit_staged_excel(staging: Path | str, destination: Path | str) -> None:
    staging_path = Path(staging)
    destination_path = Path(destination)
    if not staging_path.is_file():
        raise FileNotFoundError(f"Временный Excel-файл не создан: {staging_path}")
    os.replace(staging_path, destination_path)


def discard_staged_excel(staging: Path | str) -> None:
    Path(staging).unlink(missing_ok=True)


def prepare_runtime_dirs() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _read_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_values(env_path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items() if value]
    temporary = env_path.with_name(f".{env_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(temporary, env_path)


def _env_candidates(app_dir: Path) -> list[Path]:
    candidates = [app_dir / ".env"]
    if app_dir == APP_DIR and PROJECT_ROOT != app_dir:
        candidates.append(PROJECT_ROOT / ".env")
    return candidates


def load_settings(app_dir: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for env_path in reversed(_env_candidates(app_dir)):
        values.update(_read_env_values(env_path))
    for key in ("UPD_PROVIDER", "UPD_MODEL", "UPD_CUSTOM_PROFILES_FILE", "UPD_LANGUAGE", "UPD_AUTO_UPDATE_CHECK"):
        if key not in values and os.getenv(key, "").strip():
            values[key] = os.environ[key].strip()
    return values


def load_language(app_dir: Path) -> str:
    language = load_settings(app_dir).get("UPD_LANGUAGE", DEFAULT_LANGUAGE).lower()
    return language if language in {"ru", "en"} else DEFAULT_LANGUAGE


def auto_update_enabled(app_dir: Path) -> bool:
    return load_settings(app_dir).get("UPD_AUTO_UPDATE_CHECK", "true").lower() not in {"0", "false", "no"}


def save_app_preferences(app_dir: Path, language: str, auto_update: bool) -> None:
    if language not in {"ru", "en"}:
        raise ValueError("Unsupported interface language.")
    env_path = app_dir / ".env"
    values = _read_env_values(env_path)
    values["UPD_LANGUAGE"] = language
    values["UPD_AUTO_UPDATE_CHECK"] = "true" if auto_update else "false"
    _write_env_values(env_path, values)
    os.environ.update({"UPD_LANGUAGE": language, "UPD_AUTO_UPDATE_CHECK": values["UPD_AUTO_UPDATE_CHECK"]})


def custom_profiles_path(app_dir: Path) -> Path:
    return app_dir / CUSTOM_PROFILES_FILE


def _read_custom_profiles(app_dir: Path) -> list[dict[str, str]]:
    path = custom_profiles_path(app_dir)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось прочитать профили провайдеров: {exc}") from exc
    profiles = payload.get("profiles", [])
    if not isinstance(profiles, list):
        raise RuntimeError("Файл профилей провайдеров имеет неверный формат.")
    return [dict(profile) for profile in profiles if isinstance(profile, dict)]


def _write_custom_profiles(app_dir: Path, profiles: list[dict[str, str]]) -> None:
    path = custom_profiles_path(app_dir)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps({"profiles": profiles}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def list_provider_profiles(app_dir: Path) -> list[dict[str, str]]:
    """User-created profiles, suitable for the settings UI."""
    profiles: list[dict[str, str]] = []
    for profile in _read_custom_profiles(app_dir):
        protected_key = str(profile.get("api_key_protected", ""))
        if protected_key:
            profile["api_key"] = unprotect_secret(protected_key)
        profile["id"] = f"custom:{profile.get('id', '').strip()}"
        profiles.append(profile)
    return profiles


def migrate_profile_secrets(app_dir: Path) -> None:
    profiles = _read_custom_profiles(app_dir)
    changed = False
    for profile in profiles:
        plain_key = str(profile.get("api_key", "")).strip()
        if plain_key and not profile.get("api_key_protected"):
            profile["api_key_protected"] = protect_secret(plain_key)
            profile.pop("api_key", None)
            changed = True
    if changed:
        _write_custom_profiles(app_dir, profiles)


def save_custom_profile(
    app_dir: Path, name: str, base_url: str, api_key: str, model: str, profile_id: str | None = None,
) -> str:
    from config import normalize_api_url

    normalized = {
        "id": (profile_id or uuid.uuid4().hex).removeprefix("custom:").strip(),
        "name": name.strip(),
        "base_url": base_url.strip(),
        "api_key_protected": protect_secret(api_key.strip()),
        "model": model.strip(),
    }
    if not all(normalized.values()):
        raise RuntimeError("Заполните название, базовый URL, API-ключ и модель.")
    normalize_api_url(normalized["base_url"])
    profiles = _read_custom_profiles(app_dir)
    if any(item.get("id") == normalized["id"] for item in profiles):
        profiles = [normalized if item.get("id") == normalized["id"] else item for item in profiles]
    else:
        profiles.append(normalized)
    _write_custom_profiles(app_dir, profiles)
    return f"custom:{normalized['id']}"


def delete_custom_profile(app_dir: Path, provider_id: str) -> None:
    profile_id = provider_id.removeprefix("custom:").strip()
    profiles = _read_custom_profiles(app_dir)
    filtered = [profile for profile in profiles if profile.get("id") != profile_id]
    if len(filtered) == len(profiles):
        raise RuntimeError("Пользовательский профиль не найден.")
    _write_custom_profiles(app_dir, filtered)
    values = _read_env_values(app_dir / ".env")
    if values.get("UPD_PROVIDER") == provider_id:
        values.pop("UPD_PROVIDER", None)
        values.pop("UPD_MODEL", None)
        _write_env_values(app_dir / ".env", values)
        os.environ.pop("UPD_PROVIDER", None)
        os.environ.pop("UPD_MODEL", None)


def activate_settings(app_dir: Path) -> dict[str, str]:
    migrate_profile_secrets(app_dir)
    values = load_settings(app_dir)
    values["UPD_CUSTOM_PROFILES_FILE"] = str(custom_profiles_path(app_dir).resolve())
    os.environ.update(values)
    return values


def save_settings(app_dir: Path, provider_id: str, model: str, api_key: str) -> None:
    from config import get_model, get_provider

    activate_settings(app_dir)
    provider = get_provider(provider_id)
    selected_model = get_model(provider.id, model)
    normalized_key = api_key.strip()
    if not normalized_key:
        raise RuntimeError(f"API-ключ для {provider.label} не задан.")
    env_path = app_dir / ".env"
    values = _read_env_values(env_path)
    values["UPD_PROVIDER"] = provider.id
    values["UPD_MODEL"] = selected_model
    if provider.key_env:
        values[provider.key_env] = normalized_key
    values["UPD_CUSTOM_PROFILES_FILE"] = str(custom_profiles_path(app_dir).resolve())
    _write_env_values(env_path, values)
    os.environ.update({"UPD_PROVIDER": provider.id, "UPD_MODEL": selected_model, "UPD_CUSTOM_PROFILES_FILE": values["UPD_CUSTOM_PROFILES_FILE"]})
    if provider.key_env:
        os.environ[provider.key_env] = normalized_key


def load_api_key(app_dir: Path) -> str | None:
    from config import get_api_key, get_provider

    values = activate_settings(app_dir)
    try:
        provider = get_provider(values.get("UPD_PROVIDER"))
    except ValueError:
        return None
    return values.get(provider.key_env) or get_api_key(provider.id) or None


def save_api_key(app_dir: Path, api_key: str) -> str:
    from config import get_model, get_provider

    values = activate_settings(app_dir)
    provider = get_provider(values.get("UPD_PROVIDER"))
    save_settings(app_dir, provider.id, values.get("UPD_MODEL") or get_model(provider.id), api_key)
    return api_key.strip()


def ensure_api_key(app_dir: Path, prompt_fn: Callable[[str], str] | None | object = _USE_STDIN_PROMPT) -> str:
    api_key = load_api_key(app_dir)
    if api_key:
        return api_key
    from config import get_provider
    try:
        get_provider(load_settings(app_dir).get("UPD_PROVIDER"))
    except ValueError:
        raise RuntimeError("Провайдер не настроен. Добавьте его в настройках.")
    if prompt_fn is None:
        raise RuntimeError("API-ключ выбранного провайдера не задан. Укажите его в настройках.")
    if prompt_fn is _USE_STDIN_PROMPT:
        prompt_fn = input
    assert callable(prompt_fn)
    api_key = prompt_fn("Введите API key: ").strip()
    if not api_key:
        raise RuntimeError("API-ключ не задан.")
    return save_api_key(app_dir, api_key)


def ensure_cli_provider(app_dir: Path, prompt_fn: Callable[[str], str] = input) -> str:
    """Configure the first custom provider in portable CLI mode."""
    existing_key = load_api_key(app_dir)
    if existing_key:
        return existing_key
    if list_provider_profiles(app_dir):
        return ensure_api_key(app_dir, prompt_fn=prompt_fn)
    name = prompt_fn("Название провайдера: ").strip()
    base_url = prompt_fn("Базовый URL или полный /chat/completions endpoint: ").strip()
    model = prompt_fn("Модель: ").strip()
    api_key = prompt_fn("API-ключ: ").strip()
    provider_id = save_custom_profile(app_dir, name, base_url, api_key, model)
    save_settings(app_dir, provider_id, model, api_key)
    return api_key


def install_text_context_menu(widget, menu_factory: Callable[[Any], Any] | None = None, language: str | None = None):
    import tkinter as tk

    language = language or load_language(APP_DIR)
    menu = menu_factory(widget) if menu_factory else tk.Menu(widget, tearoff=0)
    def paste_from_clipboard(_event=None):
        """Insert clipboard text directly; this also works for masked API-key fields."""
        try:
            text = widget.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        widget.insert("insert", text)
        return "break"

    def handle_control_key(event):
        # Tk maps Ctrl+V by the active keyboard layout. On a Russian layout the
        # keysym is not "v", but Windows still reports physical V as keycode 86.
        keycode = getattr(event, "keycode", None)
        if keycode == 86:
            return paste_from_clipboard(event)
        sequence = {65: "<<SelectAll>>", 67: "<<Copy>>", 88: "<<Cut>>"}.get(keycode)
        if sequence:
            widget.event_generate(sequence)
            return "break"
        return None

    commands = [(tr(language, "cut"), "<<Cut>>"), (tr(language, "copy"), "<<Copy>>"), (tr(language, "select_all"), "<<SelectAll>>")]
    for label, sequence in commands:
        menu.add_command(label=label, command=lambda seq=sequence: widget.event_generate(seq))
    menu.add_command(label=tr(language, "paste"), command=paste_from_clipboard)

    def show_menu(event) -> None:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    widget.bind("<Button-3>", show_menu)
    widget.bind("<Control-KeyPress>", handle_control_key)
    widget.bind("<Shift-Insert>", paste_from_clipboard)
    return menu


def _emit(on_progress: ProgressCallback | None, event: dict[str, Any]) -> None:
    if on_progress:
        on_progress(event)


def _default_parser_factory():
    from ai_parser import AIParser

    return AIParser()


def _default_writer_factory(output_path: Path):
    from excel_writer import ExcelWriter

    return ExcelWriter(file_path=output_path)


def process_image_sequence(
    images: list[Path],
    output_path: Path,
    parser_factory: Callable[[], Any] | None = None,
    writer_factory: Callable[[Path], Any] | None = None,
    on_progress: ProgressCallback | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict[str, Any]], int]:
    """
    Processes selected images one by one.

    Each image is sent to the parser as a separate document, so different UPDs
    selected in one batch do not get merged into one extraction request.
    """
    parser_factory = parser_factory or _default_parser_factory
    writer_factory = writer_factory or _default_writer_factory

    image_paths = [Path(path) for path in images]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = writer_factory(output_path)
    summaries: list[dict[str, Any]] = []
    rows_total = 0
    total = len(image_paths)

    for index, image_path in enumerate(image_paths, start=1):
        _emit(
            on_progress,
            {
                "type": "log",
                "message": tr(language, "processing_file", index=index, total=total, name=image_path.name),
            },
        )

        try:
            with tempfile.TemporaryDirectory(prefix="upd_single_") as tmp:
                doc_dir = Path(tmp)
                staged_image = doc_dir / f"page001{image_path.suffix.lower()}"
                shutil.copy2(image_path, staged_image)

                parser = parser_factory()
                data = parser.parse_document(doc_dir)
                rows_added = writer.write(data, image_path.stem)

            rows_total += rows_added
            summary = {
                "file": image_path.name,
                "rows": rows_added,
                "date": data.get("date", ""),
                "seller": data.get("seller", ""),
                "invoice_number": data.get("invoice_number", ""),
            }
            summaries.append(summary)
            _emit(
                on_progress,
                {
                    "type": "log",
                    "message": tr(language, "file_done", name=image_path.name, rows=rows_added),
                },
            )
        except Exception as exc:
            logger.exception("Не удалось обработать %s: %s", image_path.name, exc)
            summaries.append(
                {
                    "file": image_path.name,
                    "rows": 0,
                    "error": str(exc),
                }
            )
            _emit(
                on_progress,
                {
                    "type": "error",
                    "message": tr(language, "file_error", name=image_path.name, error=exc),
                },
            )

        _emit(
            on_progress,
            {
                "type": "progress",
                "done": index,
                "total": total,
            },
        )

    return summaries, rows_total


def process_images(images: list[Path], output_path: Path) -> tuple[dict[str, Any], int]:
    summaries, rows_added = process_image_sequence(images, output_path)
    return {"documents": summaries}, rows_added


def _log_cli_event(event: dict[str, Any]) -> None:
    event_type = event.get("type")
    message = event.get("message")
    if event_type == "error" and message:
        logger.error("%s", message)
    elif event_type == "log" and message:
        logger.info("%s", message)
    elif event_type == "progress":
        logger.info("Прогресс: %s/%s", event.get("done"), event.get("total"))


def run_cli() -> int:
    setup_logging()
    prepare_runtime_dirs()

    logger.info("Папка с фото: %s", INPUT_DIR)
    logger.info("Папка результата: %s", OUTPUT_DIR)

    images = collect_input_images(INPUT_DIR)
    if not images:
        logger.error(
            "В папке input нет изображений. Поддерживаемые форматы: %s",
            ", ".join(sorted(SUPPORTED_EXTENSIONS)),
        )
        return 2

    try:
        ensure_cli_provider(APP_DIR)
    except Exception as exc:
        logger.error("%s", exc)
        return 3

    output_path = build_output_path(OUTPUT_DIR)
    logger.info("Найдено изображений: %d", len(images))

    summaries, rows_added = process_image_sequence(
        images,
        output_path,
        on_progress=_log_cli_event,
        language=load_language(APP_DIR),
    )
    errors = [item for item in summaries if item.get("error")]

    logger.info("Готово.")
    logger.info("Строк добавлено: %s", rows_added)
    logger.info("Excel сохранен: %s", output_path)
    logger.info("Проверьте файл: ИИ может ошибаться.")

    if errors:
        logger.error("Файлов с ошибками: %d", len(errors))
        return 1
    return 0


class MinimalUpdApp:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        self.tk = tk
        self.ttk = ttk
        self.language = load_language(APP_DIR)
        self.root = tk.Tk()
        self.root.title(self.t("app_title"))
        self.root.geometry("760x560")
        self.root.minsize(680, 480)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.files: list[Path] = []
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.processing = False
        self.last_output_path: Path | None = None

        self.bg = "#f7f7f4"
        self.fg = "#1f2937"
        self.muted = "#6b7280"
        self.accent = "#111827"

        self.root.configure(bg=self.bg)
        self._configure_style()

        container = ttk.Frame(self.root, padding=24, style="App.TFrame")
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container, style="App.TFrame")
        header.pack(fill="x")
        self.title_label = ttk.Label(header, text=self.t("app_title"), style="Title.TLabel")
        self.title_label.pack(anchor="w")
        self.subtitle_label = ttk.Label(
            header,
            text=self.t("select_photos"),
            style="Muted.TLabel",
        )
        self.subtitle_label.pack(anchor="w", pady=(4, 0))

        toolbar = ttk.Frame(container, style="App.TFrame")
        toolbar.pack(fill="x", pady=(18, 12))
        self.add_button = ttk.Button(
            toolbar,
            text=self.t("add_photos"),
            command=self.add_files,
            style="Accent.TButton",
        )
        self.add_button.pack(side="left")
        self.clear_button = ttk.Button(
            toolbar,
            text=self.t("clear"),
            command=self.clear_files,
            style="Plain.TButton",
        )
        self.clear_button.pack(side="left", padx=(8, 0))
        self.settings_button = ttk.Button(
            toolbar,
            text=self.t("settings"),
            command=self.open_settings,
            style="Plain.TButton",
        )
        self.settings_button.pack(side="left", padx=(8, 0))
        self.process_button = ttk.Button(
            toolbar,
            text=self.t("process"),
            command=self.start_processing,
            style="Plain.TButton",
        )
        self.process_button.pack(side="right")

        self.file_count_var = tk.StringVar(value=self.t("no_files"))
        ttk.Label(container, textvariable=self.file_count_var, style="Small.TLabel").pack(
            anchor="w"
        )

        list_frame = tk.Frame(container, bg="#ffffff", highlightthickness=1, highlightbackground="#d9d9d6")
        list_frame.pack(fill="both", expand=True, pady=(6, 14))
        self.file_list = tk.Listbox(
            list_frame,
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            font=("Segoe UI", 10),
            foreground=self.fg,
            background="#ffffff",
            selectbackground="#e5e7eb",
            selectforeground=self.fg,
        )
        self.file_list.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.file_list.configure(yscrollcommand=scrollbar.set)

        self.status_var = tk.StringVar(value=self.t("ready"))
        ttk.Label(container, textvariable=self.status_var, style="Small.TLabel").pack(anchor="w")
        self.progress = ttk.Progressbar(container, mode="determinate", maximum=1)
        self.progress.pack(fill="x", pady=(6, 12))

        self.log = ScrolledText(
            container,
            height=8,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d9d9d6",
            font=("Consolas", 9),
            foreground=self.fg,
            background="#ffffff",
            wrap="word",
        )
        self.log.pack(fill="both")
        self.log.configure(state="disabled")
        self.append_log(self.t("add_photos_log"))

        self.root.after(120, self.poll_events)
        if auto_update_enabled(APP_DIR):
            self.root.after(1200, lambda: self.check_for_updates(silent=True))

    def t(self, key: str, **kwargs: object) -> str:
        return tr(self.language, key, **kwargs)

    def on_close(self) -> None:
        if self.processing:
            from tkinter import messagebox
            messagebox.showwarning(
                self.t("app_title"),
                self.t("processing_close_blocked"),
                parent=self.root,
            )
            return
        self.root.destroy()

    def apply_language(self, language: str) -> None:
        """Apply the selected language to the existing main window without restart."""
        self.language = language
        self.root.title(self.t("app_title"))
        self.title_label.configure(text=self.t("app_title"))
        self.subtitle_label.configure(text=self.t("select_photos"))
        self.add_button.configure(text=self.t("add_photos"))
        self.clear_button.configure(text=self.t("clear"))
        self.settings_button.configure(text=self.t("settings"))
        self.process_button.configure(text=self.t("process"))
        self.status_var.set(self.t("ready"))
        self.refresh_file_list()

    def _configure_style(self) -> None:
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass

        style.configure("App.TFrame", background=self.bg)
        style.configure(
            "Title.TLabel",
            background=self.bg,
            foreground=self.fg,
            font=("Segoe UI Semibold", 18),
        )
        style.configure(
            "Muted.TLabel",
            background=self.bg,
            foreground=self.muted,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Small.TLabel",
            background=self.bg,
            foreground=self.fg,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Accent.TButton",
            padding=(14, 8),
            borderwidth=0,
            background=self.accent,
            foreground="#ffffff",
            font=("Segoe UI Semibold", 10),
        )
        style.map("Accent.TButton", background=[("active", "#374151")])
        style.configure(
            "Plain.TButton",
            padding=(14, 8),
            borderwidth=1,
            background="#ffffff",
            foreground=self.fg,
            font=("Segoe UI", 10),
        )
        style.map("Plain.TButton", background=[("active", "#f3f4f6")])
        style.configure("Horizontal.TProgressbar", background=self.accent)

    def add_files(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askopenfilenames(
            parent=self.root,
            title=self.t("select_images"),
            filetypes=[
                (self.t("images"), "*.jpg *.jpeg *.png *.webp *.bmp *.tiff"),
                (self.t("all_files"), "*.*"),
            ],
        )
        if not selected:
            return

        known = {path.resolve() for path in self.files}
        added = 0
        for raw in selected:
            path = Path(raw)
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            resolved = path.resolve()
            if resolved in known:
                continue
            self.files.append(path)
            known.add(resolved)
            added += 1

        self.refresh_file_list()
        if added:
            self.append_log(self.t("added_files", count=added))

    def clear_files(self) -> None:
        if self.processing:
            return
        self.files.clear()
        self.refresh_file_list()
        self.progress.configure(value=0, maximum=1)
        self.status_var.set(self.t("ready"))

    def refresh_file_list(self) -> None:
        self.file_list.delete(0, self.tk.END)
        for index, path in enumerate(self.files, start=1):
            self.file_list.insert(self.tk.END, f"{index}. {path.name}")
        count = len(self.files)
        self.file_count_var.set(
            self.t("no_files") if count == 0 else self.t("files_selected", count=count)
        )

    def check_for_updates(self, silent: bool = False) -> None:
        """Check GitHub Releases without blocking the main window."""
        if getattr(self, "_update_check_in_progress", False):
            return
        self._update_check_in_progress = True

        def worker() -> None:
            try:
                release = check_for_update(APP_VERSION)
            except Exception as exc:
                error = str(exc)
                self.root.after(0, lambda: finish(None, error))
            else:
                self.root.after(0, lambda: finish(release, None))

        def finish(release, error: str | None) -> None:
            self._update_check_in_progress = False
            if error:
                self.append_log(self.t("update_error", error=error))
                if not silent:
                    from tkinter import messagebox
                    messagebox.showerror(self.t("updates"), self.t("update_error", error=error), parent=self.root)
                return
            if release is None:
                if not silent:
                    from tkinter import messagebox
                    messagebox.showinfo(self.t("updates"), self.t("latest_version"), parent=self.root)
                return
            self.available_release = release
            self.append_log(self.t("update_available", version=release.version))
            from tkinter import messagebox
            messagebox.showinfo(self.t("updates"), self.t("update_available", version=release.version), parent=self.root)

        threading.Thread(target=worker, daemon=True).start()

    def open_settings(self, draft: dict[str, Any] | None = None, selected_tab: int = 0) -> None:
        from tkinter import messagebox, ttk
        from config import get_provider

        window = self.tk.Toplevel(self.root)
        window.title(self.t("settings"))
        window.transient(self.root)
        window.resizable(False, False)
        window.configure(bg=self.bg)
        frame = ttk.Frame(window, padding=20, style="App.TFrame")
        frame.pack(fill="both", expand=True)

        language_bar = ttk.Frame(frame, style="App.TFrame")
        language_bar.pack(fill="x", pady=(0, 10))
        ttk.Label(language_bar, text=self.t("language"), style="Small.TLabel").pack(side="left")
        language_var = self.tk.StringVar(value=self.language)
        language_box = ttk.Combobox(
            language_bar, textvariable=language_var, state="readonly", width=16,
            values=[f"{code}: {label}" for code, label in LANGUAGES.items()],
        )
        language_box.set(f"{self.language}: {LANGUAGES[self.language]}")
        language_box.pack(side="right")

        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        providers_frame = ttk.Frame(notebook, padding=8, style="App.TFrame")
        updates_frame = ttk.Frame(notebook, padding=12, style="App.TFrame")
        about_frame = ttk.Frame(notebook, padding=12, style="App.TFrame")
        notebook.add(providers_frame, text=self.t("providers"))
        notebook.add(updates_frame, text=self.t("updates"))
        notebook.add(about_frame, text=self.t("about"))

        def change_language(*_args) -> None:
            selected_language = language_var.get().split(":", 1)[0]
            if selected_language not in LANGUAGES or selected_language == self.language:
                return
            try:
                draft_state = {
                    "provider_id": selected_id(),
                    "editing_custom_id": editing_custom_id,
                    "name": name_var.get(),
                    "base_url": url_var.get(),
                    "model": model_var.get(),
                    "api_key": key_var.get(),
                }
                current_tab = notebook.index(notebook.select())
                save_app_preferences(APP_DIR, selected_language, auto_update_var.get())
                self.apply_language(selected_language)
                window.destroy()
                self.open_settings(draft=draft_state, selected_tab=current_tab)
            except OSError as exc:
                messagebox.showerror(self.t("settings"), str(exc), parent=window)

        language_box.bind("<<ComboboxSelected>>", change_language)

        saved = activate_settings(APP_DIR)
        profiles = list_provider_profiles(APP_DIR)
        by_id = {profile["id"]: profile for profile in profiles}
        active_id = saved.get("UPD_PROVIDER", "") if saved.get("UPD_PROVIDER") in by_id else ""
        profile_var = self.tk.StringVar(value=active_id)
        name_var, url_var, model_var, key_var = (self.tk.StringVar() for _ in range(4))
        editing_custom_id: str | None = None

        ttk.Label(providers_frame, text=self.t("provider_profile"), style="Small.TLabel").pack(anchor="w")
        profile_box = ttk.Combobox(providers_frame, state="readonly", width=49)
        profile_box.pack(fill="x", pady=(6, 12))
        entries: dict[str, Any] = {}
        for label, variable in ((self.t("name"), name_var), (self.t("base_url"), url_var), (self.t("model"), model_var)):
            ttk.Label(providers_frame, text=label, style="Small.TLabel").pack(anchor="w")
            widget = ttk.Entry(providers_frame, textvariable=variable, width=52)
            widget.pack(fill="x", pady=(6, 12))
            install_text_context_menu(widget, language=self.language)
            entries[label] = widget
        ttk.Label(providers_frame, text=self.t("api_key"), style="Small.TLabel").pack(anchor="w")
        key_entry = ttk.Entry(providers_frame, textvariable=key_var, width=52, show="*")
        key_entry.pack(fill="x", pady=(6, 8))
        install_text_context_menu(key_entry, language=self.language)
        show_var = self.tk.BooleanVar(value=False)
        ttk.Checkbutton(providers_frame, text=self.t("show_key"), variable=show_var, command=lambda: key_entry.configure(show="" if show_var.get() else "*")).pack(anchor="w", pady=(0, 12))

        controls = ttk.Frame(providers_frame, style="App.TFrame")
        controls.pack(fill="x", pady=(0, 12))

        def selected_id() -> str:
            index = profile_box.current()
            return profiles[index]["id"] if index >= 0 else ""

        def set_custom_mode(is_custom: bool) -> None:
            state = self.tk.NORMAL if is_custom else self.tk.DISABLED
            for widget in entries.values():
                widget.configure(state=state)
            delete_button.configure(state=self.tk.NORMAL if is_custom and editing_custom_id else self.tk.DISABLED)

        def load_profile(*_args) -> None:
            nonlocal editing_custom_id
            provider_id = selected_id()
            profile = by_id[provider_id]
            profile_var.set(provider_id)
            is_custom = True
            editing_custom_id = provider_id.removeprefix("custom:")
            name_var.set(profile["name"])
            url_var.set(profile["base_url"])
            model_var.set(profile["model"])
            key_var.set(profile.get("api_key", ""))
            set_custom_mode(is_custom)

        def refresh_profiles(selected: str) -> None:
            nonlocal profiles, by_id
            profiles = list_provider_profiles(APP_DIR)
            by_id = {profile["id"]: profile for profile in profiles}
            profile_box.configure(values=[profile["name"] for profile in profiles])
            index = next(i for i, profile in enumerate(profiles) if profile["id"] == selected)
            profile_box.current(index)
            load_profile()

        def add_profile() -> None:
            nonlocal editing_custom_id
            editing_custom_id = None
            profile_box.set(self.t("new_provider"))
            name_var.set("")
            url_var.set("https://api.example.com/v1")
            model_var.set("")
            key_var.set("")
            set_custom_mode(True)

        def persist() -> str:
            provider_id = selected_id()
            if not provider_id or editing_custom_id is None and profile_box.current() < 0:
                provider_id = save_custom_profile(APP_DIR, name_var.get(), url_var.get(), key_var.get(), model_var.get())
            elif provider_id.startswith("custom:"):
                provider_id = save_custom_profile(APP_DIR, name_var.get(), url_var.get(), key_var.get(), model_var.get(), editing_custom_id)
            save_settings(APP_DIR, provider_id, model_var.get(), key_var.get())
            refresh_profiles(provider_id)
            return provider_id

        def remove_profile() -> None:
            provider_id = selected_id()
            if not provider_id or editing_custom_id is None:
                return
            if not messagebox.askyesno(self.t("delete"), f"{self.t('delete')} «{name_var.get()}»?", parent=window):
                return
            try:
                delete_custom_profile(APP_DIR, provider_id)
            except Exception as exc:
                messagebox.showerror(self.t("settings"), str(exc), parent=window)
                return
            if list_provider_profiles(APP_DIR):
                refresh_profiles(list_provider_profiles(APP_DIR)[0]["id"])
            else:
                add_profile()

        def save_profile() -> None:
            """Save the entered provider without clearing any field."""
            try:
                provider_id = persist()
            except Exception as exc:
                messagebox.showerror(self.t("settings"), str(exc), parent=window)
                return
            self.append_log(f"{self.t('provider_saved')} {get_provider(provider_id).label}")
            messagebox.showinfo(self.t("settings"), self.t("provider_saved"), parent=window)

        add_button = ttk.Button(controls, text=self.t("add_provider"), command=save_profile, style="Accent.TButton")
        add_button.pack(side="left")
        new_button = ttk.Button(controls, text=self.t("new_profile"), command=add_profile, style="Plain.TButton")
        new_button.pack(side="left", padx=(8, 0))
        delete_button = ttk.Button(controls, text=self.t("delete"), command=remove_profile, style="Plain.TButton")
        delete_button.pack(side="left", padx=(8, 0))

        buttons = ttk.Frame(providers_frame, style="App.TFrame")
        buttons.pack(fill="x")
        checking_var = self.tk.StringVar(value="")
        ttk.Label(providers_frame, textvariable=checking_var, style="Small.TLabel").pack(anchor="w", pady=(10, 0))
        checking_progress = ttk.Progressbar(providers_frame, mode="indeterminate")

        def set_checking(active: bool) -> None:
            state = self.tk.DISABLED if active else self.tk.NORMAL
            profile_box.configure(state="disabled" if active else "readonly")
            for widget in (*entries.values(), key_entry):
                widget.configure(state=state)
            for widget in (add_button, new_button, delete_button, check_button, save_button, cancel_button):
                widget.configure(state=state)
            if active:
                checking_var.set(self.t("checking_provider"))
                checking_progress.pack(fill="x", pady=(4, 0))
                checking_progress.start(12)
            else:
                checking_progress.stop()
                checking_progress.pack_forget()
                checking_var.set("")

        def check_provider() -> None:
            draft = (name_var.get(), url_var.get(), model_var.get(), key_var.get())
            set_checking(True)

            def run_check() -> None:
                from ai_parser import AIParser
                try:
                    result = AIParser.ping_connection(
                        *draft,
                    )
                except Exception as exc:
                    message = str(exc)
                    window.after(0, lambda message=message: finish_check(False, message))
                else:
                    window.after(0, lambda: finish_check(True, result))

            def finish_check(success: bool, message: str) -> None:
                set_checking(False)
                if success:
                    messagebox.showinfo(self.t("check_connection"), message, parent=window)
                else:
                    messagebox.showerror(self.t("check_connection"), message, parent=window)

            threading.Thread(target=run_check, daemon=True).start()

        def save_and_close() -> None:
            try:
                provider_id = persist()
                selected_language = language_var.get().split(":", 1)[0]
                save_app_preferences(APP_DIR, selected_language, auto_update_var.get())
            except Exception as exc:
                messagebox.showerror(self.t("settings"), str(exc), parent=window)
                return
            self.append_log(self.t("settings_saved", provider=get_provider(provider_id).label, model=model_var.get()))
            if selected_language != self.language:
                messagebox.showinfo(self.t("settings"), self.t("restart_required"), parent=window)
            window.destroy()

        check_button = ttk.Button(buttons, text=self.t("check_connection"), command=check_provider, style="Plain.TButton")
        check_button.pack(side="left")
        save_button = ttk.Button(buttons, text=self.t("save"), command=save_and_close, style="Accent.TButton")
        save_button.pack(side="right")
        cancel_button = ttk.Button(buttons, text=self.t("cancel"), command=window.destroy, style="Plain.TButton")
        cancel_button.pack(side="right", padx=(0, 8))

        # Updates tab: all network activity remains off the Tk main thread.
        ttk.Label(updates_frame, text=self.t("current_version", version=APP_VERSION), style="Title.TLabel").pack(anchor="w")
        auto_update_var = self.tk.BooleanVar(value=auto_update_enabled(APP_DIR))
        ttk.Checkbutton(updates_frame, text=self.t("auto_check"), variable=auto_update_var).pack(anchor="w", pady=(14, 8))
        update_status = self.tk.StringVar(value="")
        ttk.Label(updates_frame, textvariable=update_status, style="Small.TLabel").pack(anchor="w")
        update_progress = ttk.Progressbar(updates_frame, mode="indeterminate")
        release_holder: dict[str, Any] = {}

        def set_update_busy(active: bool, message: str = "") -> None:
            update_check_button.configure(state=self.tk.DISABLED if active else self.tk.NORMAL)
            update_install_button.configure(state=self.tk.DISABLED)
            update_status.set(message)
            if active:
                update_progress.pack(fill="x", pady=(6, 10))
                update_progress.start(12)
            else:
                update_progress.stop()
                update_progress.pack_forget()

        def finish_update_check(release, error: str | None) -> None:
            set_update_busy(False)
            if error:
                update_status.set(self.t("update_error", error=error))
                return
            if release is None:
                update_status.set(self.t("latest_version"))
                return
            release_holder["release"] = release
            update_status.set(self.t("update_available", version=release.version))
            update_install_button.configure(state=self.tk.NORMAL if is_frozen() else self.tk.DISABLED)

        def check_updates_from_settings() -> None:
            set_update_busy(True, self.t("checking_updates"))
            def worker() -> None:
                try:
                    release = check_for_update(APP_VERSION)
                except Exception as exc:
                    error = str(exc)
                    window.after(0, lambda: finish_update_check(None, error))
                else:
                    window.after(0, lambda: finish_update_check(release, None))
            threading.Thread(target=worker, daemon=True).start()

        def install_update() -> None:
            release = release_holder.get("release")
            if release is None:
                return
            set_update_busy(True, self.t("download_update"))
            def worker() -> None:
                try:
                    downloaded = download_update(release)
                    if not is_frozen():
                        raise RuntimeError(self.t("cannot_autoinstall"))
                    apply_downloaded_update(Path(sys.executable), downloaded)
                except Exception as exc:
                    error = str(exc)
                    window.after(0, lambda: finish_update_check(None, error))
                else:
                    window.after(0, lambda: window.destroy())
                    window.after(50, self.root.destroy)
            threading.Thread(target=worker, daemon=True).start()

        update_check_button = ttk.Button(updates_frame, text=self.t("check_updates"), command=check_updates_from_settings, style="Plain.TButton")
        update_check_button.pack(anchor="w", pady=(12, 0))
        update_install_button = ttk.Button(updates_frame, text=self.t("install_update"), command=install_update, style="Accent.TButton", state=self.tk.DISABLED)
        update_install_button.pack(anchor="w", pady=(8, 0))

        ttk.Label(about_frame, text=self.t("app_title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(about_frame, text=self.t("about_text"), style="Muted.TLabel", wraplength=430).pack(anchor="w", pady=(8, 0))
        ttk.Label(about_frame, text=self.t("developer"), style="Small.TLabel").pack(anchor="w", pady=(16, 0))
        ttk.Label(about_frame, text=self.t("license"), style="Small.TLabel").pack(anchor="w", pady=(4, 0))
        github_label = self.tk.Label(about_frame, text=self.t("github", url=GITHUB_URL), fg="#2563eb", bg=self.bg, cursor="hand2")
        github_label.pack(anchor="w", pady=(10, 0))
        github_label.bind("<Button-1>", lambda _event: __import__("webbrowser").open(GITHUB_URL))
        profile_box.bind("<<ComboboxSelected>>", load_profile)
        if draft is not None:
            draft_provider_id = str(draft.get("provider_id", ""))
            if draft_provider_id in by_id:
                refresh_profiles(draft_provider_id)
            else:
                add_profile()
            editing_custom_id = draft.get("editing_custom_id") or None
            name_var.set(str(draft.get("name", "")))
            url_var.set(str(draft.get("base_url", "")))
            model_var.set(str(draft.get("model", "")))
            key_var.set(str(draft.get("api_key", "")))
            delete_button.configure(state=self.tk.NORMAL if editing_custom_id else self.tk.DISABLED)
        elif profiles:
            refresh_profiles(active_id or profiles[0]["id"])
        else:
            add_profile()
        try:
            notebook.select(max(0, min(selected_tab, notebook.index("end") - 1)))
        except self.tk.TclError:
            pass
        window.grab_set()
        key_entry.focus_set()

    def start_processing(self) -> None:
        from tkinter import messagebox

        if self.processing:
            return
        if not self.files:
            messagebox.showinfo(self.t("app_title"), self.t("need_photo"), parent=self.root)
            return

        try:
            ensure_api_key(APP_DIR, prompt_fn=None)
        except Exception as exc:
            messagebox.showerror(self.t("app_title"), str(exc), parent=self.root)
            self.open_settings()
            return

        output_path = ask_excel_output_path(self.root, language=self.language)
        if output_path is None:
            return

        try:
            output_path = prepare_selected_excel_path(output_path)
        except Exception as exc:
            messagebox.showerror(self.t("app_title"), self.t("preparing_excel_failed", error=exc), parent=self.root)
            return

        self.processing = True
        self.set_controls_enabled(False)
        self.progress.configure(value=0, maximum=len(self.files))
        self.status_var.set(self.t("processing_started"))
        self.append_log(self.t("start_processing"))
        self.append_log(self.t("excel_target", path=output_path))

        self.last_output_path = output_path
        files_snapshot = list(self.files)

        staging_path = build_staging_excel_path(output_path)
        thread = threading.Thread(
            target=self.worker,
            args=(files_snapshot, output_path, staging_path),
            daemon=False,
        )
        self.worker_thread = thread
        thread.start()

    def worker(self, files: list[Path], output_path: Path, staging_path: Path) -> None:
        try:
            summaries, rows_added = process_image_sequence(
                files,
                staging_path,
                on_progress=self.events.put,
                language=self.language,
            )
            successful_documents = [item for item in summaries if not item.get("error")]
            output_saved = bool(successful_documents and staging_path.is_file())
            if output_saved:
                commit_staged_excel(staging_path, output_path)
            else:
                discard_staged_excel(staging_path)
            self.events.put(
                {
                    "type": "done",
                    "summaries": summaries,
                    "rows": rows_added,
                    "output_path": str(output_path),
                    "output_saved": output_saved,
                }
            )
        except Exception as exc:
            discard_staged_excel(staging_path)
            self.events.put({"type": "fatal", "message": str(exc)})

    def poll_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self.handle_event(event)
        self.root.after(120, self.poll_events)

    def handle_event(self, event: dict[str, Any]) -> None:
        from tkinter import messagebox

        event_type = event.get("type")
        if event_type in {"log", "error"}:
            self.append_log(str(event.get("message", "")))
            return

        if event_type == "progress":
            done = int(event.get("done", 0))
            total = int(event.get("total", 1))
            self.progress.configure(maximum=total, value=done)
            self.status_var.set(self.t("processed_progress", done=done, total=total))
            return

        if event_type == "done":
            self.processing = False
            self.set_controls_enabled(True)
            summaries = event.get("summaries", [])
            errors = [item for item in summaries if item.get("error")]
            rows = int(event.get("rows", 0))
            output_path = str(event.get("output_path", ""))
            output_saved = bool(event.get("output_saved"))

            if errors:
                self.status_var.set(self.t("done_errors_rows", rows=rows))
                self.append_log(self.t("files_with_errors", count=len(errors)))
                if not output_saved:
                    self.append_log(self.t("no_report_created"))
                messagebox.showwarning(
                    self.t("app_title"),
                    self.t("processing_complete_errors", rows=rows, path=output_path) if output_saved else self.t("no_report_created"),
                    parent=self.root,
                )
            else:
                self.status_var.set(self.t("done_rows", rows=rows))
                self.append_log(self.t("excel_saved", path=output_path))
                messagebox.showinfo(
                    self.t("app_title"),
                    self.t("processing_complete", rows=rows, path=output_path),
                    parent=self.root,
                )
            return

        if event_type == "fatal":
            self.processing = False
            self.set_controls_enabled(True)
            message = str(event.get("message", self.t("unknown_error")))
            self.status_var.set(self.t("processing_failed"))
            self.append_log(message)
            messagebox.showerror(self.t("app_title"), message, parent=self.root)

    def set_controls_enabled(self, enabled: bool) -> None:
        state = self.tk.NORMAL if enabled else self.tk.DISABLED
        self.add_button.configure(state=state)
        self.clear_button.configure(state=state)
        self.settings_button.configure(state=state)
        self.process_button.configure(state=state)

    def append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(self.tk.END, message + "\n")
        self.log.see(self.tk.END)
        self.log.configure(state="disabled")

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_gui() -> int:
    return MinimalUpdApp().run()


def self_test() -> int:
    import requests  # noqa: F401
    import openpyxl  # noqa: F401
    import dotenv  # noqa: F401
    import PIL  # noqa: F401
    import ai_parser  # noqa: F401
    import config  # noqa: F401
    import excel_writer  # noqa: F401

    print("self-test ok")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--cli" in sys.argv:
        return run_cli()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
