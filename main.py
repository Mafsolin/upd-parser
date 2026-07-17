"""
main.py — точка входа: сканирует папку documents, обрабатывает каждый УПД,
записывает результаты в Excel.
"""

import logging
import sys
from pathlib import Path

from ai_parser import AIParser
from config import DOCUMENTS_PATH, EXCEL_FILE
from excel_writer import ExcelWriter

# ────────────────────────────────────────────────────────────────────────────
# Настройка логгера
# ────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("upd_parser.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ────────────────────────────────────────────────────────────────────────────

def get_document_folders(base: Path) -> list[Path]:
    """
    Возвращает отсортированный список подпапок в `base`,
    каждая из которых считается отдельным УПД.
    """
    if not base.exists():
        logger.error("Папка документов не найдена: %s", base)
        sys.exit(1)

    folders = sorted(p for p in base.iterdir() if p.is_dir())
    if not folders:
        logger.warning("В папке '%s' не найдено подпапок с документами.", base)
    return folders


def print_separator(char: str = "─", width: int = 50) -> None:
    print(char * width)


# ────────────────────────────────────────────────────────────────────────────
# Основная логика
# ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    base_path = Path(DOCUMENTS_PATH)
    folders   = get_document_folders(base_path)

    if not folders:
        print("Нет документов для обработки. Добавьте подпапки в 'documents/'.")
        return

    parser = AIParser()
    writer = ExcelWriter()

    total_docs  = 0
    total_items = 0
    errors      = []

    print_separator("═")
    print(f"  УПД-Парсер   |   Найдено документов: {len(folders)}")
    print(f"  Excel-файл:  {EXCEL_FILE}")
    print_separator("═")

    for folder in folders:
        doc_name = folder.name
        print(f"\n▶  Обработка: {doc_name}")

        try:
            # 1. Извлекаем данные через LLM
            data = parser.parse_document(folder)

            # 2. Записываем в Excel
            items_count = writer.write(data, doc_name)

            total_docs  += 1
            total_items += items_count

            print(f"   ✔  Processed:   {doc_name}")
            print(f"   ✔  Items added: {items_count}")

        except FileNotFoundError as exc:
            msg = f"[{doc_name}] Файлы не найдены: {exc}"
            logger.error(msg)
            errors.append(msg)
            print(f"   ✘  {msg}")

        except ValueError as exc:
            msg = f"[{doc_name}] Ошибка разбора данных: {exc}"
            logger.error(msg)
            errors.append(msg)
            print(f"   ✘  {msg}")

        except RuntimeError as exc:
            msg = f"[{doc_name}] Ошибка API: {exc}"
            logger.error(msg)
            errors.append(msg)
            print(f"   ✘  {msg}")

        except Exception as exc:  # noqa: BLE001
            msg = f"[{doc_name}] Неожиданная ошибка: {exc}"
            logger.exception(msg)
            errors.append(msg)
            print(f"   ✘  {msg}")

    # ── Итог ────────────────────────────────────────────────────────────────
    print_separator("═")
    print("  Готово!")
    print(f"  Обработано документов : {total_docs} / {len(folders)}")
    print(f"  Добавлено строк       : {total_items}")
    print(f"  Ошибок                : {len(errors)}")
    if errors:
        print("\n  Список ошибок:")
        for e in errors:
            print(f"    • {e}")
    print_separator("═")
    print(f"  Файл Excel: {Path(EXCEL_FILE).resolve()}")
    print_separator("═")


# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
