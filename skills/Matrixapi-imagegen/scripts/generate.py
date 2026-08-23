#!/usr/bin/env python3
"""Generate or edit images through the recipient's configured Images API."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request
import uuid


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_BASE_URL = "https://eos.manyuvip.com"
ALLOWED_BASE_HOST = "eos.manyuvip.com"
MAX_RESPONSE_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_EDGE = 3840
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
MAX_INPUT_IMAGES = 15
SUPPORTED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class ImageGenError(RuntimeError):
    pass


def _environment_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
            if isinstance(value, str):
                return value.strip()
        except (FileNotFoundError, OSError):
            pass
    return ""


def _nested_strings(value: Any, key_name: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == key_name and isinstance(child, str):
                found.append(child.strip())
            else:
                found.extend(_nested_strings(child, key_name))
    elif isinstance(value, list):
        for child in value:
            found.extend(_nested_strings(child, key_name))
    return [item for item in found if item]


def _provider_values(raw: str) -> tuple[str, str]:
    try:
        settings = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "", ""
    if not isinstance(settings, dict):
        return "", ""

    config = settings.get("config", {})
    urls = _nested_strings(config, "base_url")
    if isinstance(config, str):
        urls.extend(re.findall(r"base_url\s*=\s*[\"']([^\"']+)[\"']", config))

    auth = settings.get("auth", {})
    if isinstance(auth, str):
        try:
            auth = json.loads(auth)
        except json.JSONDecodeError:
            match = re.search(
                r"OPENAI_API_KEY\s*[=:]\s*[\"']?([^\s\"']+)", auth
            )
            auth = {"OPENAI_API_KEY": match.group(1)} if match else {}
    keys = _nested_strings(auth, "OPENAI_API_KEY")
    return (urls[0].strip() if urls else "", keys[0].strip() if keys else "")


def _discover_current_cc_switch_provider() -> tuple[str, str, str] | None:
    db_path = Path.home() / ".cc-switch" / "cc-switch.db"
    if not db_path.is_file():
        return None

    try:
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT settings_config FROM providers "
                "WHERE app_type = 'codex' AND is_current = 1 LIMIT 1"
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        raise ImageGenError("Unable to read the current CC Switch configuration") from exc

    if not row:
        return None
    base_url, key = _provider_values(row[0] or "")
    if base_url and key:
        return base_url, key, "CC Switch current Codex provider"
    return None


def _paired_environment(
    prefix: str, default_base_url: str = ""
) -> tuple[str, str] | None:
    base_url = _environment_value(f"{prefix}_BASE_URL")
    key = _environment_value(f"{prefix}_API_KEY")
    if not base_url and key and default_base_url:
        base_url = default_base_url
    if bool(base_url) != bool(key):
        raise ImageGenError(
            f"{prefix}_BASE_URL and {prefix}_API_KEY must be configured together"
        )
    return (base_url, key) if base_url and key else None


def discover_credentials() -> tuple[str, str, str, str]:
    configured = _paired_environment("IMAGEGEN", DEFAULT_BASE_URL)
    if configured:
        base_url, key = configured
        source = "environment"
    else:
        configured = _paired_environment("OPENAI")
        if configured:
            base_url, key = configured
            source = "environment"
        else:
            current = _discover_current_cc_switch_provider()
            if not current:
                raise ImageGenError(
                    "No image API configuration found. Set IMAGEGEN_BASE_URL and "
                    "IMAGEGEN_API_KEY, set OPENAI_BASE_URL and OPENAI_API_KEY, or "
                    "select a compatible Codex provider in CC Switch"
                )
            base_url, key, source = current

    model = _environment_value("IMAGEGEN_MODEL") or DEFAULT_MODEL
    return base_url, key, model, source


def _validate_base_url(base_url: str) -> tuple[urllib.parse.ParseResult, str]:
    try:
        parsed = urllib.parse.urlparse(base_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ImageGenError("The image API base URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageGenError("The image API base URL must be an HTTP or HTTPS URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ImageGenError("Non-local image API connections must use HTTPS")
    if parsed.username or parsed.password:
        raise ImageGenError("The image API base URL must not contain credentials")
    normalized_host = (parsed.hostname or "").rstrip(".").lower()
    if normalized_host != ALLOWED_BASE_HOST:
        raise ImageGenError(
            f"This Skill only supports the configured image relay: {ALLOWED_BASE_HOST}"
        )

    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return parsed._replace(netloc=netloc), parsed.path.rstrip("/")


def image_endpoint(base_url: str, operation: str) -> str:
    if operation not in {"generations", "edits"}:
        raise ImageGenError("Unsupported image operation")
    parsed, path = _validate_base_url(base_url)
    operation_path = f"/images/{operation}"
    if path.endswith(operation_path):
        endpoint_path = path
    elif path.endswith("/v1"):
        endpoint_path = path + operation_path
    else:
        endpoint_path = path + "/v1" + operation_path
    return urllib.parse.urlunparse(
        parsed._replace(path=endpoint_path, params="", query="", fragment="")
    )


def generation_endpoint(base_url: str) -> str:
    """Return the generations endpoint (kept for callers of the original script)."""
    return image_endpoint(base_url, "generations")


def edit_endpoint(base_url: str) -> str:
    return image_endpoint(base_url, "edits")


def validate_size(size: str) -> str:
    match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", size.strip().lower())
    if not match:
        raise ImageGenError("Size must use WIDTHxHEIGHT, for example 1024x1024")
    width, height = int(match.group(1)), int(match.group(2))
    if width > MAX_EDGE or height > MAX_EDGE:
        raise ImageGenError("Image size must not exceed 3840px on either edge")
    if width % 16 or height % 16:
        raise ImageGenError("Image width and height must both be multiples of 16px")
    long_edge, short_edge = max(width, height), min(width, height)
    if long_edge > short_edge * 3:
        raise ImageGenError("Long edge to short edge ratio must not exceed 3:1")
    pixels = width * height
    if pixels < MIN_PIXELS or pixels > MAX_PIXELS:
        raise ImageGenError(
            f"Total pixels must be between {MIN_PIXELS:,} and {MAX_PIXELS:,}"
        )
    return f"{width}x{height}"


def edit_working_size(size: str) -> str:
    """Preserve the requested edit size; the provider decides its own limits."""
    return validate_size(size)


def _read_limited(response: Any, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ImageGenError("The API response exceeded the local safety limit")
    return data


def _safe_http_detail(exc: urllib.error.HTTPError, key: str) -> str:
    detail = exc.read(8192).decode("utf-8", errors="replace")
    if key:
        detail = detail.replace(key, "[redacted]")
    return detail[:1500]


def _parse_api_response(raw: bytes) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImageGenError("Image API returned invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise ImageGenError("Image API returned no image data")
    if not result["data"]:
        raise ImageGenError("Image API returned no image data")
    return result


def _post_image_request(
    endpoint: str,
    key: str,
    body: bytes,
    content_type: str,
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "api-imagegen-skill/1.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = _read_limited(response, MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        detail = _safe_http_detail(exc, key)
        raise ImageGenError(f"Image API returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ImageGenError(f"Image API request failed: {exc}") from exc
    return _parse_api_response(raw)


def _option_fields(
    quality: str = "",
    background: str = "",
    input_fidelity: str = "",
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if quality:
        fields["quality"] = quality
    if background:
        fields["background"] = background
    if input_fidelity:
        fields["input_fidelity"] = input_fidelity
    return fields


def call_api(
    endpoint: str,
    key: str,
    model: str,
    prompt: str,
    size: str,
    count: int,
    timeout: int,
    options: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call the JSON generations endpoint."""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": count,
    }
    if options:
        payload.update(options)
    return _post_image_request(
        endpoint,
        key,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json",
        timeout,
    )


def _safe_filename(path: Path) -> str:
    name = path.name.replace("\r", "").replace("\n", "")
    name = name.replace('"', "'")
    return name or "image"


def _input_image(path_value: str, label: str) -> tuple[str, str, bytes]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise ImageGenError(f"{label} file does not exist: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ImageGenError(f"Unable to inspect {label} file: {path}") from exc
    if size <= 0:
        raise ImageGenError(f"{label} file is empty: {path}")
    if size > MAX_IMAGE_BYTES:
        raise ImageGenError(f"{label} file exceeds the 50 MB safety limit: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ImageGenError(f"Unable to read {label} file: {path}") from exc
    mime = _input_mime(data, path)
    return _safe_filename(path), mime, data


def _input_mime(data: bytes, path: Path) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed in SUPPORTED_IMAGE_MIME:
        return guessed
    raise ImageGenError(
        f"{path} is not a supported image; use PNG, JPEG, WEBP, or GIF"
    )


def _multipart_body(
    fields: Iterable[tuple[str, str]],
    files: Iterable[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = f"----api-imagegen-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    for name, filename, mime, data in files:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def call_edit_api(
    endpoint: str,
    key: str,
    model: str,
    prompt: str,
    size: str,
    count: int,
    image_paths: list[str],
    mask_path: str | None,
    timeout: int,
    options: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not image_paths:
        raise ImageGenError("At least one --image file is required for editing")
    if len(image_paths) > MAX_INPUT_IMAGES:
        raise ImageGenError(f"At most {MAX_INPUT_IMAGES} input images are supported")

    fields: list[tuple[str, str]] = [
        ("model", model),
        ("prompt", prompt),
        ("size", size),
        ("n", str(count)),
    ]
    if options:
        fields.extend(options.items())

    files: list[tuple[str, str, str, bytes]] = []
    for path in image_paths:
        filename, mime, data = _input_image(path, "Input image")
        files.append(("image", filename, mime, data))
    if mask_path:
        filename, mime, data = _input_image(mask_path, "Mask image")
        files.append(("mask", filename, mime, data))

    body, content_type = _multipart_body(fields, files)
    return _post_image_request(endpoint, key, body, content_type, timeout)


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        port = None
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _image_extension(data: bytes, content_type: str = "") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    normalized = content_type.lower().split(";", 1)[0].strip()
    if normalized in SUPPORTED_IMAGE_MIME:
        return {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }[normalized]
    raise ImageGenError("The API returned data that is not a supported image")


def _image_metadata(data: bytes, content_type: str = "") -> dict[str, int | str]:
    """Read the actual dimensions and format from a supported raster image."""
    image_format = ""
    width = height = 0

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        image_format = "PNG"
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
    elif data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        image_format = "GIF"
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        image_format = "WEBP"
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
        elif chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
        elif chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
    elif data.startswith(b"\xff\xd8\xff"):
        image_format = "JPEG"
        offset = 2
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        while offset + 9 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                break
            if marker in sof_markers and segment_length >= 7:
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                break
            offset += segment_length

    if not image_format:
        _image_extension(data, content_type)
    if width <= 0 or height <= 0:
        raise ImageGenError("Unable to read the generated image dimensions")

    longest_edge = max(width, height)
    resolution = "4K" if longest_edge >= 3840 else "2K" if longest_edge >= 2048 else "1K"
    return {
        "width": width,
        "height": height,
        "format": image_format,
        "resolution": resolution,
    }


def _decode_data_url(url: str) -> tuple[bytes, str]:
    header, encoded = url.split(",", 1)
    try:
        return base64.b64decode(encoded, validate=True), header[5:].split(";", 1)[0]
    except (binascii.Error, ValueError) as exc:
        raise ImageGenError("Image API returned invalid base64 image data") from exc


def _download_image(url: str, endpoint: str, key: str, timeout: int) -> tuple[bytes, str]:
    if url.startswith("data:") and ";base64," in url:
        return _decode_data_url(url)

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageGenError("Image API returned an unsupported image URL")
    headers = {"User-Agent": "api-imagegen-skill/1.1"}
    if _origin(url) == _origin(endpoint):
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            return _read_limited(response, MAX_IMAGE_BYTES), content_type
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ImageGenError(f"Unable to download the generated image: {exc}") from exc


def save_images(
    result: dict[str, Any], endpoint: str, key: str, output_dir: Path, timeout: int
) -> tuple[list[str], list[dict[str, int | str]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = uuid.uuid4().hex[:8]
    paths: list[str] = []
    image_info: list[dict[str, int | str]] = []

    for index, item in enumerate(result["data"], start=1):
        if not isinstance(item, dict):
            raise ImageGenError("Image API returned an invalid image item")
        content_type = ""
        if item.get("b64_json"):
            try:
                data = base64.b64decode(item["b64_json"], validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise ImageGenError("Image API returned invalid base64 image data") from exc
        elif item.get("url"):
            data, content_type = _download_image(
                str(item["url"]), endpoint, key, timeout
            )
        else:
            raise ImageGenError("Image item has neither url nor b64_json")

        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ImageGenError("Generated image is empty or too large")
        suffix = _image_extension(data, content_type)
        metadata = _image_metadata(data, content_type)
        final_path = output_dir / f"image-{stamp}-{run_id}-{index}{suffix}"
        temp_path = final_path.with_suffix(final_path.suffix + ".part")
        temp_path.write_bytes(data)
        temp_path.replace(final_path)
        paths.append(str(final_path.resolve()))
        image_info.append(metadata)
    return paths, image_info


def preview_paths(paths: Iterable[str]) -> list[str]:
    """Return absolute paths normalized for the chat renderer's Markdown URLs."""
    return [Path(path).resolve().as_posix() for path in paths]


def _result_file(output_dir: Path, request_id: str) -> Path:
    return output_dir / f".result-{request_id}.json"


def _completion_marker(output_dir: Path, request_id: str) -> Path:
    return output_dir / f".completed-{request_id}.json"


def _ready_marker(output_dir: Path, request_id: str) -> Path:
    return output_dir / f".ready-{request_id}.json"


def _write_sidecar(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".part")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    temp_path.replace(path)


def _hide_sidecar(path: Path) -> None:
    """Hide task sidecars in Windows Explorer without changing their paths."""
    if os.name != "nt":
        return
    try:
        import ctypes

        get_attributes = ctypes.windll.kernel32.GetFileAttributesW
        set_attributes = ctypes.windll.kernel32.SetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attributes.restype = ctypes.c_int
        attributes = get_attributes(str(path))
        if attributes == 0xFFFFFFFF:
            return
        set_attributes(str(path), attributes | 0x2)
    except (AttributeError, OSError):
        # The sidecar remains usable if the platform cannot set Explorer flags.
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt")
    parser.add_argument(
        "--image",
        "--reference-image",
        dest="images",
        action="append",
        metavar="PATH",
        help="Local input/reference image; repeat for multiple images and enable edit mode",
    )
    parser.add_argument(
        "--mask",
        metavar="PATH",
        help="Optional local PNG/JPEG mask for the edit request",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "generate", "edit"),
        default="auto",
        help="Request mode; auto selects edit when --image is supplied",
    )
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--quality")
    parser.add_argument("--background")
    parser.add_argument("--input-fidelity")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--request-id",
        help="Caller-owned task identifier used to bind output to this request",
    )
    parser.add_argument(
        "--allow-repeat",
        action="store_true",
        help="Allow an explicit retry of a completed request ID",
    )
    parser.add_argument("--check-config", action="store_true")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        request_id = (args.request_id or uuid.uuid4().hex).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", request_id):
            raise ImageGenError(
                "Request ID must contain only letters, numbers, dots, underscores, or hyphens"
            )
        if args.n < 1 or args.n > 4:
            raise ImageGenError("Image count must be between 1 and 4")
        if args.timeout < 10 or args.timeout > 600:
            raise ImageGenError("Timeout must be between 10 and 600 seconds")
        requested_size = validate_size(args.size)
        base_url, key, model, source = discover_credentials()
        generation_url = generation_endpoint(base_url)
        edit_url = edit_endpoint(base_url)
        if args.check_config:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "credential_source": source,
                        "model": model,
                        "supported_modes": ["generate", "edit"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        prompt = (args.prompt or "").strip()
        if not prompt:
            raise ImageGenError("Prompt must not be empty")

        image_paths = list(args.images or [])
        if args.mode == "generate" and (image_paths or args.mask):
            raise ImageGenError("--mode generate cannot be combined with --image or --mask")
        if args.mode == "edit" and not image_paths:
            raise ImageGenError("--mode edit requires at least one --image file")
        if args.mask and not image_paths:
            raise ImageGenError("--mask requires at least one --image file")
        if len(image_paths) > MAX_INPUT_IMAGES:
            raise ImageGenError(f"At most {MAX_INPUT_IMAGES} input images are supported")

        mode = "edit" if image_paths else "generate"
        size = edit_working_size(requested_size) if mode == "edit" else requested_size
        output_dir = args.out_dir or (
            Path.home() / ".codex" / "generated_images" / "api-imagegen"
        )
        completed = _completion_marker(output_dir, request_id)
        ready = _ready_marker(output_dir, request_id)
        if (completed.is_file() or ready.is_file()) and not args.allow_repeat:
            raise ImageGenError(
                "This task ID has already completed; ask explicitly to retry before generating again"
            )
        options = _option_fields(args.quality, args.background, args.input_fidelity)
        if mode == "edit":
            result = call_edit_api(
                edit_url,
                key,
                model,
                prompt,
                size,
                args.n,
                image_paths,
                args.mask,
                args.timeout,
                options,
            )
        else:
            result = call_api(
                generation_url,
                key,
                model,
                prompt,
                size,
                args.n,
                args.timeout,
                options,
            )
        files, image_info = save_images(
            result,
            edit_url if mode == "edit" else generation_url,
            key,
            output_dir,
            args.timeout,
        )
        preview_files = preview_paths(files)
        download_files = preview_files.copy()
        result_payload = {
            "ok": True,
            "request_id": request_id,
            "mode": mode,
            "model": model,
            "count": len(files),
            "size": size,
            "requested_size": requested_size,
            "edit_size": size if mode == "edit" else None,
            "resized_for_edit": mode == "edit" and size != requested_size,
            "input_images": len(image_paths),
            "mask": bool(args.mask),
            "files": files,
            "preview_files": preview_files,
            "download_files": download_files,
            "image_info": image_info,
        }

        # The ready sidecar is the single completion signal. It contains the
        # validated paths and metadata, reserves the task ID, and avoids extra
        # post-result filesystem work that can delay the caller.
        _write_sidecar(ready, result_payload)
        _hide_sidecar(ready)
        print(json.dumps(result_payload, ensure_ascii=False), flush=True)
        return 0
    except ImageGenError as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
