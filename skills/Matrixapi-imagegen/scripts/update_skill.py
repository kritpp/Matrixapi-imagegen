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
import tempfile
import time
import urllib.error
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
SKILL_RELATIVE_PATH = ("skills", "Matrixapi-imagegen")
REQUIRED_FILES = (
    "SKILL.md",
    "scripts/generate.py",
    "agents/openai.yaml",
)


class UpdateError(RuntimeError):
    pass


def _resolve_archive_url() -> str:
    request = urllib.request.Request(
        REPOSITORY_CONTENTS_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Matrixapi-imagegen-updater/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise UpdateError("The GitHub package listing is unexpectedly large")
            listing = json.loads(payload)
            candidates: list[tuple[tuple[int, int, int], str]] = []
            for item in listing:
                match = PACKAGE_NAME_PATTERN.fullmatch(str(item.get("name", "")))
                download_url = str(item.get("download_url", ""))
                if match and download_url.startswith("https://"):
                    candidates.append((tuple(map(int, match.groups())), download_url))
            if not candidates:
                raise UpdateError("The GitHub repository does not contain a release package")
            return max(candidates)[1]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1 << attempt)
    raise UpdateError(f"Unable to find the latest Skill after 3 attempts: {last_error}") from last_error


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


def _download_archive(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/zip", "User-Agent": "Matrixapi-imagegen-updater/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1 << attempt)
    else:
        raise UpdateError(f"Unable to download the latest Skill after 3 attempts: {last_error}") from last_error
    if len(data) > MAX_ARCHIVE_BYTES:
        raise UpdateError("The update archive is larger than the local safety limit")
    return data


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


def _replace_skill(target: Path, staged: Path) -> None:
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
    try:
        shutil.rmtree(backup)
    except OSError:
        # A locked backup is recoverable and is safer than deleting an active file.
        pass


def update_skill(archive_url: str, target: Path) -> None:
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
        archive_data = _download_archive(archive_url or _resolve_archive_url())
        with tempfile.TemporaryDirectory(
            prefix=".matrixapi-imagegen-update-", dir=str(target.parent)
        ) as temp_dir:
            staged = Path(temp_dir) / target.name
            staged.mkdir()
            _extract_skill(archive_data, staged)
            _replace_skill(target, staged)
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
    args = parse_args()
    target = args.target or Path(__file__).resolve().parents[1]
    try:
        update_skill(args.archive_url, target)
        print(json.dumps({"ok": True, "updated": True}, ensure_ascii=False))
        return 0
    except UpdateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
