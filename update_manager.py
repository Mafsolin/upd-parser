"""GitHub Releases update checks and one-file EXE replacement."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from app_version import GITHUB_REPOSITORY

RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
EXE_ASSET_NAME = "UPD_Parser.exe"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    asset_url: str
    notes: str


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise ValueError(f"Неверный формат версии: {value}")
    return tuple(int(part) for part in match.groups())


def check_for_update(current_version: str, request_get: Callable[..., Any] = requests.get) -> ReleaseInfo | None:
    try:
        response = request_get(RELEASE_API_URL, headers={"Accept": "application/vnd.github+json"}, timeout=15)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Ошибка сети: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise RuntimeError("GitHub вернул некорректный ответ.") from exc

    tag = str(payload.get("tag_name", ""))
    release_version = tag.removeprefix("v")
    if parse_version(release_version) <= parse_version(current_version):
        return None
    for asset in payload.get("assets", []):
        if isinstance(asset, dict) and asset.get("name") == EXE_ASSET_NAME and asset.get("browser_download_url"):
            return ReleaseInfo(release_version, str(asset["browser_download_url"]), str(payload.get("body", "")))
    raise RuntimeError("В новом релизе отсутствует файл UPD_Parser.exe.")


def download_update(release: ReleaseInfo, request_get: Callable[..., Any] = requests.get) -> Path:
    try:
        response = request_get(release.asset_url, stream=True, timeout=60)
        response.raise_for_status()
        target = Path(tempfile.gettempdir()) / f"UPD_Parser_{release.version}.exe"
        with target.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    file.write(chunk)
    except (OSError, requests.RequestException) as exc:
        raise RuntimeError(f"Не удалось скачать обновление: {exc}") from exc
    return target


def create_updater_script(current_exe: Path, downloaded_exe: Path) -> Path:
    """Create a batch file that replaces only the executable, not user config files."""
    script = Path(tempfile.gettempdir()) / "upd_parser_apply_update.bat"
    script.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'move /y "{downloaded_exe}" "{current_exe}" >nul\r\n'
        f'start "" "{current_exe}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    return script


def apply_downloaded_update(current_exe: Path, downloaded_exe: Path) -> None:
    script = create_updater_script(current_exe, downloaded_exe)
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
