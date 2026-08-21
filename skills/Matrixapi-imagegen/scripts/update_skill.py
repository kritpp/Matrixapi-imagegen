#!/usr/bin/env python3
"""Update Matrixapi-imagegen from the fixed public GitHub repository."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile


REPOSITORY_ARCHIVE = (
    "https://github.com/kritpp/Matrixapi-imagegen/archive/refs/heads/main.zip"
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


def _safe_member_path(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/").strip("/")
    parts = tuple(part for part in normalized.split("/") if part)
    if not parts or any(part in {".", ".."} for part in parts):
        raise UpdateError("The update archive contains an unsafe path")
    return parts


def _download_archive(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/zip", "User-Agent": "Matrixapi-imagegen-updater/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(MAX_ARCHIVE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Unable to download the latest Skill: {exc}") from exc
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
    archive_data = _download_archive(archive_url)
    with tempfile.TemporaryDirectory(
        prefix=".matrixapi-imagegen-update-", dir=str(target.parent)
    ) as temp_dir:
        staged = Path(temp_dir) / target.name
        staged.mkdir()
        _extract_skill(archive_data, staged)
        _replace_skill(target, staged)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-url", default=REPOSITORY_ARCHIVE, help=argparse.SUPPRESS)
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
