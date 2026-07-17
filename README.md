# УПД Парсер

УПД Парсер извлекает товарные данные из фотографий универсальных передаточных документов (УПД) через совместимый с OpenAI API и формирует Excel-файл.

[English version](README.en.md) · [Релизы](https://github.com/Mafsolin/upd-parser/releases)

## Возможности

- обработка фотографий `jpg`, `png`, `webp`, `bmp`, `tiff`;
- пользовательские профили провайдеров: название, Base URL, модель и API-ключ;
- нормализация экспортируемых чисел: запятая вместо точки, без пробелов и минусов;
- русско-английский интерфейс;
- проверка GitHub Releases и установка обновлений one-file EXE;
- сохранение профилей провайдеров при обновлении.

## Запуск

1. Скачайте `UPD_Parser.exe` из [Releases](https://github.com/Mafsolin/upd-parser/releases) и запустите его.
2. Откройте **Настройки → Провайдеры**.
3. Введите имя, Base URL или полный endpoint, модель и API-ключ, затем нажмите **Добавить провайдера**.
4. Добавьте фотографии и нажмите **Обработать**.

Portable-версия доступна в архиве `UPD_Parser_Portable.zip`.

## Обновления

В **Настройки → Обновления** можно включить проверку при запуске или проверить релиз вручную. Обновляется только EXE-файл: `.env` и `upd_provider_profiles.json` с настройками и API-ключами не заменяются.

## Разработка

```powershell
pip install -r requirements.txt
python -m unittest discover -s tests -v
python build_onefile_exe.py
python build_portable.py
```

## Безопасность

API-ключи хранятся локально в `upd_provider_profiles.json`. Не добавляйте этот файл и `.env` в Git.

Лицензия: [MIT](LICENSE). Разработчик: Mafsolin.
