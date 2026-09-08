#!/usr/bin/env python3
"""Update Matrixapi-imagegen from the fixed public GitHub repository."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


REPOSITORY_CONTENTS_API = (
    "https://api.github.com/repos/kritpp/Matrixapi-imagegen/contents"
)
PACKAGE_NAME_PATTERN = re.compile(
    r"^Matrixapi-imagegen-v(\d+)\.(\d+)\.(\d+)\.zip$"
)
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_EXTRACTED_BYTES = 120 * 1024 * 1024
MAX_ARCHIVE_FILES = 500
LIST_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_ATTEMPTS_PER_SOURCE = 3
SKILL_RELATIVE_PATH = ("skills", "Matrixapi-imagegen")
REQUIRED_FILES = (
    "SKILL.md",
    "scripts/generate.py",
    "scripts/hide_result.py",
    "scripts/postprocess.py",
    "scripts/update_skill.py",
    "agents/openai.yaml",
)
FIXED_BASE_URL = "https://matrixapii.com"
FIXED_BASE_HOST = "matrixapii.com"
SUPPORTED_MODELS = ("gpt-image-2", "gemini-3-pro-image")


class UpdateError(RuntimeError):
    pass


def _resolve_archive_urls() -> tuple[str, str]:
    request = urllib.request.Request(
        REPOSITORY_CONTENTS_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Matrixapi-imagegen-updater/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=LIST_TIMEOUT_SECONDS) as response:
                payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise UpdateError("The GitHub package listing is unexpectedly large")
            listing = json.loads(payload)
            candidates: list[tuple[tuple[int, int, int], str, str]] = []
            for item in listing:
                match = PACKAGE_NAME_PATTERN.fullmatch(str(item.get("name", "")))
                download_url = str(item.get("download_url", ""))
                if match and download_url.startswith("https://"):
                    candidates.append((tuple(map(int, match.groups())), match.group(0), download_url))
            if not candidates:
                raise UpdateError("The GitHub repository does not contain a release package")
            _, filename, raw_url = max(candidates, key=lambda item: item[0])
            api_url = (
                f"{REPOSITORY_CONTENTS_API}/{urllib.parse.quote(filename)}?ref=main"
            )
            return api_url, raw_url
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
            last_error = exc
            if attempt < 1:
                time.sleep(0.5)
    raise UpdateError(f"Unable to find the latest Skill after 2 attempts: {last_error}") from last_error


def _safe_member_path(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise UpdateError("The update archive contains an unsafe path")
    normalized = normalized.rstrip("/")
    parts = tuple(part for part in normalized.split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise UpdateError("The update archive contains an unsafe path")
    return parts


def _download_archive(urls: tuple[str, ...]) -> bytes:
    last_error: Exception | None = None
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        accept = (
            "application/vnd.github.raw+json"
            if parsed.netloc == "api.github.com"
            else "application/zip"
        )
        request = urllib.request.Request(
            url,
            headers={"Accept": accept, "User-Agent": "Matrixapi-imagegen-updater/1.0"},
        )
        for attempt in range(DOWNLOAD_ATTEMPTS_PER_SOURCE):
            try:
                with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                    data = response.read(MAX_ARCHIVE_BYTES + 1)
                if len(data) > MAX_ARCHIVE_BYTES:
                    raise UpdateError("The update archive is larger than the local safety limit")
                return data
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < DOWNLOAD_ATTEMPTS_PER_SOURCE:
                    time.sleep(0.75 * (attempt + 1))
    total_attempts = len(urls) * DOWNLOAD_ATTEMPTS_PER_SOURCE
    raise UpdateError(
        f"Unable to download the latest Skill after {total_attempts} attempts; "
        "the current installation was left unchanged: "
        f"{last_error}"
    ) from last_error


def _find_skill_prefix(archive: zipfile.ZipFile) -> tuple[str, ...]:
    candidates: list[tuple[str, ...]] = []
    for info in archive.infolist():
        parts = _safe_member_path(info.filename)
        for index, part in enumerate(parts):
            if part == SKILL_RELATIVE_PATH[-1]:
                candidates.append(parts[: index + 1])
    if not candidates:
        raise UpdateError("The update archive does not contain Matrixapi-imagegen")
    return min(
        set(candidates),
        key=lambda item: (0 if "skills" in item else 1, len(item)),
    )


def _extract_skill(archive_data: bytes, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_data))
    except zipfile.BadZipFile as exc:
        raise UpdateError("GitHub returned an invalid update archive") from exc

    with archive:
        prefix = _find_skill_prefix(archive)
        extracted_bytes = 0
        extracted_files = 0
        for info in archive.infolist():
            parts = _safe_member_path(info.filename)
            if parts[: len(prefix)] != prefix:
                continue
            relative = parts[len(prefix) :]
            if not relative:
                continue
            target = destination.joinpath(*relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            extracted_files += 1
            extracted_bytes += info.file_size
            if extracted_files > MAX_ARCHIVE_FILES or extracted_bytes > MAX_EXTRACTED_BYTES:
                raise UpdateError("The update archive exceeds the extraction safety limit")
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    for required in REQUIRED_FILES:
        if not (destination / required).is_file():
            raise UpdateError(f"The update archive is missing {required}")


def _archive_version(url: str) -> str:
    filename = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    match = PACKAGE_NAME_PATTERN.fullmatch(filename)
    if not match:
        raise UpdateError("The update package filename does not contain a valid version")
    return ".".join(match.groups())


def _validate_staged_skill(staged: Path, expected_version: str) -> None:
    for required in REQUIRED_FILES:
        if not (staged / required).is_file():
            raise UpdateError(f"The staged Skill is missing {required}")
    try:
        skill_text = (staged / "SKILL.md").read_text(encoding="utf-8")
        script_text = (staged / "scripts" / "generate.py").read_text(encoding="utf-8")
    except OSError as exc:
        raise UpdateError("The staged Skill could not be validated") from exc
    version_match = re.search(r'^SKILL_VERSION\s*=\s*["\']([^"\']+)["\']', script_text, re.MULTILINE)
    if not version_match or version_match.group(1) != expected_version:
        raise UpdateError("The package filename and Skill version do not match")
    required_markers = (
        "name: Matrixapi-imagegen",
        f'DEFAULT_BASE_URL = "{FIXED_BASE_URL}"',
        f'ALLOWED_BASE_HOST = "{FIXED_BASE_HOST}"',
    )
    if required_markers[0] not in skill_text or any(
        marker not in script_text for marker in required_markers[1:]
    ):
        raise UpdateError("The update package is not the fixed Matrixapi distribution")


def _replace_skill(target: Path, staged: Path, *, keep_backup: bool = False) -> Path | None:
    if not target.is_dir():
        raise UpdateError(f"Installed Skill directory not found: {target}")

    backup = target.parent / f".{target.name}.backup-{uuid.uuid4().hex[:10]}"
    moved_old = False
    try:
        os.replace(target, backup)
        moved_old = True
        os.replace(staged, target)
        if not all((target / required).is_file() for required in REQUIRED_FILES):
            raise UpdateError("The replacement Skill failed validation")
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if moved_old and backup.exists():
            os.replace(backup, target)
        raise
    if keep_backup:
        return backup
    try:
        shutil.rmtree(backup)
    except OSError:
        pass
    return None


def _rollback_skill(target: Path, backup: Path) -> None:
    failed = target.parent / f".{target.name}.failed-{uuid.uuid4().hex[:10]}"
    try:
        os.replace(target, failed)
        os.replace(backup, target)
    except OSError as exc:
        if not target.exists() and failed.exists():
            try:
                os.replace(failed, target)
            except OSError:
                pass
        raise UpdateError(
            f"The new Skill failed validation and automatic rollback failed; backup: {backup}"
        ) from exc
    try:
        shutil.rmtree(failed)
    except OSError:
        pass


def _remove_backup(backup: Path) -> None:
    try:
        shutil.rmtree(backup)
    except OSError:
        # Leaving a verified backup is safer than failing an otherwise valid update.
        pass


def _installed_check(target: Path) -> dict:
    command = [sys.executable, str(target / "scripts" / "generate.py"), "--check-config"]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise UpdateError("The installed Skill configuration check did not complete") from exc
    if completed.returncode != 0 or payload.get("ok") is not True:
        raise UpdateError("The installed Skill configuration check failed")
    version = installed_version = payload.get("skill_version") or payload.get("version")
    if not isinstance(installed_version, str) or not installed_version:
        raise UpdateError("The installed Skill did not report its version")
    payload["skill_version"] = version
    return payload


def update_skill(archive_url: str, target: Path) -> dict:
    lock_path = target.parent / f".{target.name}.update.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            stale = time.time() - lock_path.stat().st_mtime > 1800
        except OSError:
            stale = False
        if not stale:
            raise UpdateError("Another Skill update is already in progress") from exc
        try:
            lock_path.unlink()
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as retry_error:
            raise UpdateError("Another Skill update is already in progress") from retry_error
    try:
        with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(str(os.getpid()))
        archive_urls = (archive_url,) if archive_url else _resolve_archive_urls()
        expected_version = _archive_version(archive_urls[0])
        archive_data = _download_archive(archive_urls)
        with tempfile.TemporaryDirectory(
            prefix=".matrixapi-imagegen-update-", dir=str(target.parent)
        ) as temp_dir:
            staged = Path(temp_dir) / target.name
            staged.mkdir()
            _extract_skill(archive_data, staged)
            _validate_staged_skill(staged, expected_version)
            backup = _replace_skill(target, staged, keep_backup=True)
        if backup is None:
            raise UpdateError("The updater did not create a rollback backup")
        try:
            installed = _installed_check(target)
            if installed.get("skill_version") != expected_version:
                raise UpdateError("The installed Skill version does not match the package")
        except Exception:
            _rollback_skill(target, backup)
            raise
        _remove_backup(backup)
        return installed
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-url", default="", help=argparse.SUPPRESS)
    parser.add_argument("--target", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    target = args.target or Path(__file__).resolve().parents[1]
    try:
        installed = update_skill(args.archive_url, target)
        installed_version = installed.get("skill_version")
        current_model = installed.get("model")
        supported_models = installed.get("supported_models", list(SUPPORTED_MODELS))
        print(
            json.dumps(
                {
                    "ok": True,
                    "updated": True,
                    "installed_version": installed_version,
                    "current_model": current_model,
                    "selected_model": current_model,
                    "supported_models": supported_models,
                    "display_message": (
                        f"Matrixapi-imagegen {installed_version} 已更新成功；"
                        f"当前模型：{current_model}；"
                        f"支持模型：{', '.join(str(item) for item in supported_models)}；"
                        "请重启 Codex。"
                    ),
                    "restart_required": True,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except UpdateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
