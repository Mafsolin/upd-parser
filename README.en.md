# UPD Parser

UPD Parser extracts item data from photos of Russian Universal Transfer Documents (UPD) through an OpenAI-compatible API and creates an Excel workbook.

[Русская версия](README.md) · [Releases](https://github.com/Mafsolin/upd-parser/releases)

## Features

- `jpg`, `png`, `webp`, `bmp`, and `tiff` photo processing;
- custom provider profiles with a name, Base URL, model, and API key;
- Excel-number normalization: decimal commas, no grouping spaces, and no minus signs;
- Russian and English interface;
- GitHub Releases update checks and one-file EXE updates;
- provider profiles remain intact during an EXE update.

## Getting started

1. Download `UPD_Parser.exe` from [Releases](https://github.com/Mafsolin/upd-parser/releases).
2. Open **Settings → Providers**.
3. Enter a provider name, Base URL or full endpoint, model, and API key, then select **Save provider**.
4. Add photos and select **Process**.

The portable build is published as `UPD_Parser_Portable.zip`.

## Updates

Use **Settings → Updates** to enable startup checks or check manually. The updater replaces only the executable and preserves `.env` and `upd_provider_profiles.json`.

## Development

```powershell
pip install -r requirements.txt
python -m unittest discover -s tests -v
python build_onefile_exe.py
python build_portable.py
```

## Security

API keys are stored locally in `upd_provider_profiles.json`. Never commit this file or `.env`.

License: [MIT](LICENSE). Developer: Mafsolin.
