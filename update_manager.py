"""GitHub Releases update checks and one-file EXE replacement."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from app_version import GITHUB_REPOSITORY

RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
EXE_ASSET_NAME = "UPD_Parser.exe"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    asset_url: str
    notes: str
    digest: str
    size: int


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

    if not isinstance(payload, dict):
        raise RuntimeError("GitHub вернул некорректный ответ.")
    tag = str(payload.get("tag_name", ""))
    release_version = tag.removeprefix("v")
    if parse_version(release_version) <= parse_version(current_version):
        return None
    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError("GitHub вернул некорректный список файлов релиза.")
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == EXE_ASSET_NAME and asset.get("browser_download_url"):
            asset_url = str(asset["browser_download_url"])
            parsed = urlsplit(asset_url)
            expected_prefix = f"/{GITHUB_REPOSITORY}/releases/download/"
            if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.startswith(expected_prefix):
                raise RuntimeError("GitHub вернул недопустимый URL файла обновления.")
            digest_value = str(asset.get("digest", ""))
            if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest_value):
                raise RuntimeError("Для файла обновления отсутствует корректный SHA-256 digest.")
            try:
                size = int(asset.get("size", 0))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("GitHub вернул неверный размер файла обновления.") from exc
            if size <= 0:
                raise RuntimeError("GitHub вернул неверный размер файла обновления.")
            return ReleaseInfo(
                release_version,
                asset_url,
                str(payload.get("body", "")),
                digest_value.split(":", 1)[1].lower(),
                size,
            )
    raise RuntimeError("В новом релизе отсутствует файл UPD_Parser.exe.")


def download_update(release: ReleaseInfo, request_get: Callable[..., Any] = requests.get) -> Path:
    target = Path(tempfile.gettempdir()) / f"UPD_Parser_{release.version}_{uuid.uuid4().hex}.exe"
    try:
        response = request_get(release.asset_url, stream=True, timeout=60)
        response.raise_for_status()
        digest = hashlib.sha256()
        downloaded_size = 0
        with target.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    file.write(chunk)
                    digest.update(chunk)
                    downloaded_size += len(chunk)
        if downloaded_size != release.size:
            raise RuntimeError(f"Размер обновления не совпадает: ожидалось {release.size}, получено {downloaded_size}.")
        if digest.hexdigest().lower() != release.digest.lower():
            raise RuntimeError("SHA-256 обновления не совпадает с опубликованным digest.")
        with target.open("rb") as file:
            if file.read(2) != b"MZ":
                raise RuntimeError("Скачанный файл не является Windows EXE.")
    except (OSError, requests.RequestException, RuntimeError) as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Не удалось скачать обновление: {exc}") from exc
    return target


def create_updater_script(current_exe: Path, downloaded_exe: Path) -> Path:
    """Create a Unicode-safe PowerShell updater with retry, backup and rollback."""
    script = Path(tempfile.gettempdir()) / f"upd_parser_apply_update_{uuid.uuid4().hex}.ps1"

    def ps_literal(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    backup = current_exe.with_suffix(current_exe.suffix + ".bak")
    script.write_text(
        "$ErrorActionPreference = 'Stop'\r\n"
        f"$Source = {ps_literal(downloaded_exe)}\r\n"
        f"$Target = {ps_literal(current_exe)}\r\n"
        f"$Backup = {ps_literal(backup)}\r\n"
        f"$ParentPid = {os.getpid()}\r\n"
        "Wait-Process -Id $ParentPid -Timeout 30 -ErrorAction SilentlyContinue\r\n"
        "$Applied = $false\r\n"
        "for ($Attempt = 1; $Attempt -le 20; $Attempt++) {\r\n"
        "  try {\r\n"
        "    if (Test-Path -LiteralPath $Target) { Copy-Item -LiteralPath $Target -Destination $Backup -Force }\r\n"
        "    Move-Item -LiteralPath $Source -Destination $Target -Force\r\n"
        "    $Applied = $true\r\n"
        "    break\r\n"
        "  } catch { Start-Sleep -Milliseconds 500 }\r\n"
        "}\r\n"
        "if (-not $Applied) {\r\n"
        "  if ((Test-Path -LiteralPath $Backup) -and -not (Test-Path -LiteralPath $Target)) { Move-Item -LiteralPath $Backup -Destination $Target -Force }\r\n"
        "  throw 'Unable to replace UPD_Parser.exe after 20 attempts.'\r\n"
        "}\r\n"
        "if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Force }\r\n"
        "Start-Process -FilePath $Target\r\n"
        "Remove-Item -LiteralPath $PSCommandPath -Force\r\n",
        encoding="utf-8-sig",
    )
    return script


def apply_downloaded_update(current_exe: Path, downloaded_exe: Path) -> None:
    script = create_updater_script(current_exe, downloaded_exe)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
