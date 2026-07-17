"""
Build a Windows portable folder for local UPD processing.

The output folder contains an embedded Python runtime, dependencies, input and
output folders, and a .bat launcher. It does not require Python installed on
the target computer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT.parent / "UPD_Parser_Portable"
CACHE_DIR = ROOT / ".portable_cache"
PYTHON_VERSION = "3.12.10"
PYTHON_ZIP = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ZIP}"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
PYTHON_ZIP_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
GET_PIP_SHA256 = "106ae019e371c7d8cb3699c75607a9b7a4d31e2b95c575362c8bcfe3d41353fd"

APP_FILES = [
    "ai_parser.py",
    "app_version.py",
    "config.py",
    "credential_store.py",
    "data_normalizer.py",
    "excel_writer.py",
    "i18n.py",
    "update_manager.py",
    "standalone_upd/process_upd.py",
    "standalone_upd/input/README.txt",
    "standalone_upd/output/README.txt",
]

STANDALONE_PACKAGES = [
    "requests==2.33.0",
    "openpyxl==3.1.5",
    "python-dotenv==1.2.2",
    "Pillow==12.3.0",
]


def portable_batch_content() -> str:
    return r"""@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not exist "input" mkdir "input"
if not exist "output" mkdir "output"

echo.
echo ===============================================
echo  Локальная обработка УПД
echo ===============================================
echo.
echo Фото нужно положить сюда:
echo %CD%\input
echo.

if not exist "runtime\python.exe" (
    echo Не найден runtime\python.exe. Папка повреждена или скопирована не полностью.
    echo.
    pause
    exit /b 1
)

"%CD%\runtime\python.exe" "%CD%\process_upd.py" --cli
set EXIT_CODE=%ERRORLEVEL%

echo.
if "%EXIT_CODE%"=="0" (
    echo Готово. Excel лежит в папке:
    echo %CD%\output
) else (
    echo Обработка завершилась с ошибкой. Код: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%
"""


def env_example_content() -> str:
    return (
        "# Добавьте провайдера в окне «Настройки» приложения.\n"
        "# Профили и API-ключи хранятся в upd_provider_profiles.json.\n"
        "UPD_LANGUAGE=ru\n"
        "UPD_AUTO_UPDATE_CHECK=true\n"
    )


def read_routerai_key() -> str | None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("ROUTERAI_API_KEY="):
            value = stripped.split("=", 1)[1].strip()
            if value and value != "YOUR_ROUTERAI_KEY_HERE":
                return value
    return None


def copy_app_files(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "input").mkdir(exist_ok=True)
    (target / "output").mkdir(exist_ok=True)

    for relative in APP_FILES:
        src = ROOT / relative
        if relative.startswith("standalone_upd/"):
            dst = target / Path(relative).relative_to("standalone_upd")
        else:
            dst = target / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    (target / "run_upd_parser.bat").write_text(portable_batch_content(), encoding="utf-8")
    env_template = env_example_content()
    (target / ".env.example").write_text(env_template, encoding="utf-8")
    (target / ".env").write_text(env_template, encoding="utf-8")


def verify_sha256(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}")


def download_file(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_sha256(destination, expected_sha256)
        return
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, destination)
    try:
        verify_sha256(destination, expected_sha256)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def prepare_embedded_python(runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    python_exe = runtime_dir / "python.exe"
    if python_exe.exists():
        configure_pth(runtime_dir)
        return python_exe

    archive = CACHE_DIR / PYTHON_ZIP
    download_file(PYTHON_URL, archive, PYTHON_ZIP_SHA256)

    with zipfile.ZipFile(archive) as zip_file:
        zip_file.extractall(runtime_dir)

    configure_pth(runtime_dir)
    return python_exe


def configure_pth(runtime_dir: Path) -> None:
    pth_files = list(runtime_dir.glob("python*._pth"))
    if not pth_files:
        return

    pth = pth_files[0]
    lines = pth.read_text(encoding="utf-8").splitlines()
    normalized = []
    saw_site = False
    saw_packages = False
    for line in lines:
        stripped = line.strip()
        if stripped == "#import site":
            normalized.append("import site")
            saw_site = True
        else:
            normalized.append(line)
            saw_site = saw_site or stripped == "import site"
            saw_packages = saw_packages or stripped.replace("\\", "/") == "Lib/site-packages"

    if not saw_packages:
        normalized.append("Lib/site-packages")
    if not saw_site:
        normalized.append("import site")

    pth.write_text("\n".join(normalized) + "\n", encoding="utf-8")


def install_dependencies(python_exe: Path) -> None:
    get_pip = CACHE_DIR / "get-pip.py"
    download_file(GET_PIP_URL, get_pip, GET_PIP_SHA256)

    subprocess.run([str(python_exe), str(get_pip)], check=True)
    subprocess.run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--upgrade",
            *STANDALONE_PACKAGES,
        ],
        check=True,
    )


def build(output_root: Path = OUTPUT_ROOT) -> Path:
    if output_root.exists():
        shutil.rmtree(output_root)

    copy_app_files(output_root)
    python_exe = prepare_embedded_python(output_root / "runtime")
    install_dependencies(python_exe)
    return output_root


def main() -> int:
    if os.name != "nt":
        print("This portable build script is intended for Windows.")
        return 1

    output = build()
    print()
    print(f"Portable folder created: {output}")
    print(f"Run: {output / 'run_upd_parser.bat'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
