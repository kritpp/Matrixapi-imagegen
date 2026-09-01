#!/usr/bin/env python3
"""Generate or edit images through the recipient's configured Images API."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    from postprocess import PostprocessError, parse_output_size, process_many
except ImportError:  # pragma: no cover - allows importing this file as a module
    from .postprocess import PostprocessError, parse_output_size, process_many

try:
    from reference_pack import ReferencePackError, pack_yaliai_references
except ImportError:  # pragma: no cover - allows importing this file as a module
    from .reference_pack import ReferencePackError, pack_yaliai_references


DEFAULT_MODEL = "gpt-image-2"
SUPPORTED_MODELS = ("gpt-image-2", "gemini-3-pro-image")
SKILL_NAME = "Matrixapi-imagegen"
SKILL_VERSION = "1.8.41"
DEFAULT_BASE_URL = "https://matrixapii.com"
ALLOWED_BASE_HOST = "matrixapii.com"
RESULT_HIDE_DELAY_MS = 10_000
MAX_RESPONSE_BYTES = 100 * 1024 * 1024
# Result downloads are streamed to a task-scoped .part file, so a large 8K
# result never occupies a matching amount of memory. This is deliberately
# separate from the 50 MiB reference-upload limit below.
MAX_RESULT_IMAGE_BYTES = 512 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024
# Keep multipart uploads below the relay's 256 MiB request limit. The margin
# leaves room for multipart headers and prevents a proxy from cutting off a
# large request after the upstream task has already been billed.
MAX_MULTIPART_BODY_BYTES = 192 * 1024 * 1024
MAX_EDGE = 7680
MIN_PIXELS = 655_360
MAX_PIXELS = 58_982_400
MAX_INPUT_IMAGES = 16
MAX_YALIAI_SOURCE_IMAGES = 60
YALIAI_PROVIDER = "yaliai"
# A large local edit is more reliable as a JSON async task. The relay stages
# local files as temporary HTTPS references before submitting upstream, so
# this changes only the response transport, never the source pixels or size.
AUTO_ASYNC_REFERENCE_COUNT = 6
AUTO_ASYNC_REFERENCE_BYTES = 48 * 1024 * 1024
STORY_STATE_VERSION = 1
MAX_STORY_PAGES = 20
# Version 2 invalidates ledgers written by 1.8.28 and earlier. Those ledgers
# could contain a stale ``uncertain`` 503 and incorrectly block a later
# request after the customer manually changed channels.
IDEMPOTENCY_VERSION = 2
IDEMPOTENCY_TTL_MS = 15 * 60 * 1000
IDEMPOTENCY_WAIT_INTERVAL_SECONDS = 0.2
# The pinned GPT Image 2 routes accept native 4K edits. Older relays can still
# opt into the legacy downscale through IMAGEGEN_LEGACY_EDIT_RESIZE.
EDIT_MAX_EDGE = 1792
QUALITY_VALUES = {"auto", "low", "medium", "high"}
# ``auto`` lets the model choose.  Explicit ratios are passed through to the
# configured relay instead of being limited to the old 1:1/3:2/2:3 enum.
ASPECT_RATIOS = {"auto", "1:1", "3:2", "2:3"}
SUPPORTED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MASK_SUPPORT_ENV = "IMAGEGEN_MASK_SUPPORT"


class ImageGenError(RuntimeError):
    """A sanitized image error with transport metadata for safe recovery.

    Transient HTTP failures returned before a usable result (notably 503)
    must not be written as an ``uncertain`` idempotency result.  Otherwise a
    later, healthy request can replay the old error instead of querying the
    provider again.  Network/time-out failures after submission remain
    uncertain and continue to protect against duplicate paid submissions.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


CONTENT_POLICY_MARKERS = (
    "copyright",
    "trademark",
    "safety",
    "moderation",
    "disallowed",
    "content policy",
    "policy violation",
    "content violation",
    "版权",
    "著作权",
    "商标",
    "安全策略",
    "内容审核",
    "内容违规",
)
ROUTE_MARKERS = (
    "model_not_found",
    "no available channel",
    "no usable channel",
    "distributor",
    "无可用渠道",
    "无可用模型",
    "模型不存在",
)

PROMPT_LENGTH_MARKERS = (
    "prompt too long",
    "prompt is too long",
    "prompt length",
    "maximum prompt",
    "max prompt",
    "character limit",
    "characters maximum",
    "too many characters",
    "string too long",
    "提示词过长",
    "提示词太长",
    "提示词长度",
    "字符限制",
    "字符上限",
    "超过最大长度",
    "长度超限",
)


def _prompt_limit_from_error(error: ImageGenError) -> int | None:
    """Return a character limit only for an explicit pre-acceptance rejection."""
    if error.status_code not in {400, 413, 422}:
        return None
    detail = str(error).lower()
    if not any(marker.lower() in detail for marker in PROMPT_LENGTH_MARKERS):
        return None
    for pattern in (
        r"(?:max(?:imum)?|limit(?:ed)?|up to)\D{0,24}(\d{2,6})\s*(?:characters?|chars?)",
        r"(?:最大|上限|限制|不超过)\D{0,16}(\d{2,6})\s*(?:个)?(?:字符|字)",
        r"(\d{2,6})\s*(?:characters?|chars?|个字符|字符)(?:\s*(?:max(?:imum)?|limit|上限|限制))",
    ):
        match = re.search(pattern, detail, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 64 <= value <= 100_000:
                return value
    # Legacy GPT Image compatible routes commonly use 1024 characters but do
    # not always expose the numeric limit.  This default is used only after an
    # explicit prompt-length rejection, never as a preflight restriction.
    return 1024


def compact_prompt_for_upstream(prompt: str, limit: int) -> str:
    """Fallback-only compaction that keeps exact text and ordered constraints."""
    normalized = re.sub(r"[ \t\r\f\v]+", " ", prompt).strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    if len(normalized) <= limit:
        return normalized

    exact: list[str] = []
    for pattern in (
        r"“[^”]{1,500}”",
        r"「[^」]{1,500}」",
        r"《[^》]{1,500}》",
        r'"[^"]{1,500}"',
        r"'[^']{1,500}'",
    ):
        for match in re.finditer(pattern, normalized):
            if match.group(0) not in exact:
                exact.append(match.group(0))
    exact_clause = "\n精确保留文字：" + "；".join(exact) if exact else ""
    if len(exact_clause) >= limit - 48:
        raise ImageGenError(
            "上游明确拒绝了长提示词，但必须逐字保留的文字本身已超过其限制；"
            "本次未进行第二次提交。"
        )

    budget = limit - len(exact_clause)
    # Split at natural Chinese/English sentence or clause boundaries.  Keep
    # original order while prioritising subject setup and explicit constraints.
    clauses = [
        value.strip()
        for value in re.split(r"(?<=[。！？!?；;])|\n+", normalized)
        if value.strip()
    ]
    selected: set[int] = set()
    used = 0
    important = re.compile(
        r"(必须|不要|不得|仅|只|保持|保留|精确|文字|角色|尺寸|比例|构图|镜头|"
        r"must|never|only|preserve|exact|text|character|ratio|composition)",
        re.IGNORECASE,
    )
    ranked = sorted(
        range(len(clauses)),
        key=lambda index: (
            0 if index < 2 else 1,
            0 if important.search(clauses[index]) else 1,
            index,
        ),
    )
    for index in ranked:
        clause = clauses[index]
        addition = len(clause) + (1 if selected else 0)
        if used + addition <= budget:
            selected.add(index)
            used += addition
    compacted = "\n".join(clauses[index] for index in sorted(selected)).strip()
    if not compacted:
        compacted = normalized[:budget].rstrip()
    result = (compacted + exact_clause).strip()
    return result[:limit]


def _fallback_idempotency_key(original_key: str, prompt: str) -> str:
    if not original_key:
        return ""
    return hashlib.sha256(
        f"{original_key}:prompt-length-fallback:{prompt}".encode("utf-8")
    ).hexdigest()


def _diagnose_upstream_failure(detail: str, status_code: int | None = None) -> tuple[str, str]:
    """Classify a relay/upstream error without pretending a generic 400 is a policy verdict."""
    normalized = (detail or "").lower()
    if any(marker.lower() in normalized for marker in CONTENT_POLICY_MARKERS):
        return (
            "content_policy",
            "模型明确拒绝了这次内容/版权/安全策略请求。这可能涉及版权角色、商标或其他内容限制，"
            "不是本地 Skill 的尺寸或文件错误；请改用原创描述，或让中转站提供模型原始审核原因。",
        )
    if any(marker.lower() in normalized for marker in ROUTE_MARKERS):
        return (
            "model_route",
            "中转站当前没有可用的模型渠道，或模型映射不可用；这不是版权提示。请检查渠道、模型名和分组配置。",
        )
    if status_code in {400, 422} and (
        "request failed" in normalized
        or "bad_response_status_code" in normalized
        or not normalized
    ):
        return (
            "upstream_rejection_unknown",
            "中转站把模型的失败响应包装成了泛化错误，未返回具体原因；仅凭这个 400 无法确认是否版权拦截。"
            "相同请求在不同尺寸或模型也失败时，更应先排查模型渠道/路由和中转站错误透传。",
        )
    if status_code is not None and status_code >= 500:
        return (
            "upstream_service",
            "模型服务或中转站暂时失败，未生成图片；请检查模型服务状态和渠道日志。",
        )
    return (
        "upstream_request",
        "模型请求未成功，但返回信息不足以判断是参数、渠道还是内容策略；请查看中转站的原始响应。",
    )


def _format_upstream_error(detail: str, status_code: int | None = None) -> str:
    category, explanation = _diagnose_upstream_failure(detail, status_code)
    status = f"HTTP {status_code}" if status_code is not None else "异步任务失败"
    raw = (detail or "未提供详细原因").strip()[:1000]
    return f"{status} [{category}] {explanation} 原始信息: {raw}"


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


def _skill_env_file_values() -> dict[str, str]:
    """Read installer-managed credentials without evaluating shell syntax."""
    try:
        path = Path.home() / ".codex" / "Matrixapi-imagegen.env"
    except RuntimeError:
        return {}
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    allowed = {"IMAGEGEN_API_KEY", "IMAGEGEN_MODEL"}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name in allowed:
            values[name] = value.strip().strip('"').strip("'")
    return values


def _read_prompt_file(path: Path) -> str:
    """Read prompt text without assuming the shell's Windows encoding.

    Prompt files may come from PowerShell, Notepad, or Codex and therefore be
    UTF-8, UTF-8 with BOM, or UTF-16 LE/BE.  Detect the BOM first, then use
    strict UTF-8 and a final UTF-16 fallback so malformed input is rejected
    before any paid request is made.
    """
    try:
        data = path.expanduser().read_bytes()
    except OSError as exc:
        raise ImageGenError(f"Unable to read --prompt-file: {exc}") from exc
    if data.startswith(b"\xff\xfe"):
        encoding = "utf-16-le"
    elif data.startswith(b"\xfe\xff"):
        encoding = "utf-16-be"
    elif data.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        if encoding == "utf-8":
            try:
                return data.decode("utf-16")
            except UnicodeDecodeError as exc:
                raise ImageGenError(
                    "Unable to decode --prompt-file; use UTF-8 or UTF-16 LE/BE"
                ) from exc
        raise ImageGenError(
            "Unable to decode --prompt-file; use UTF-8 or UTF-16 LE/BE"
        )


def _hide_directory(path: Path) -> None:
    """Mark an internal state path hidden on Windows immediately."""
    if os.name != "nt":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x2
        INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
        kernel32 = ctypes.windll.kernel32
        get_attributes = kernel32.GetFileAttributesW
        set_attributes = kernel32.SetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attributes.restype = ctypes.c_int
        attributes = get_attributes(str(path))
        if attributes != INVALID_FILE_ATTRIBUTES:
            set_attributes(str(path), attributes | FILE_ATTRIBUTE_HIDDEN)
    except (AttributeError, OSError):
        pass


def mask_support_enabled(model: str) -> bool:
    """Return whether a model's local mask path was explicitly enabled."""
    if model not in {"gpt-image-2"}:
        return True
    return _environment_value(MASK_SUPPORT_ENV).lower() in {"1", "true", "yes"}


def selected_provider(value: str | None) -> str:
    """Return an explicit provider adapter; auto leaves all existing paths unchanged."""
    provider = (value or _environment_value("IMAGEGEN_PROVIDER") or "auto").strip().lower()
    if provider not in {"auto", YALIAI_PROVIDER}:
        raise ImageGenError("--provider must be auto or yaliai")
    return provider


def _user_environment_value(name: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return value.strip() if isinstance(value, str) else ""
    except (FileNotFoundError, OSError):
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
    skill_env = _skill_env_file_values()
    imagegen_key = _environment_value("IMAGEGEN_API_KEY") or skill_env.get(
        "IMAGEGEN_API_KEY", ""
    )
    if imagegen_key:
        base_url, key = DEFAULT_BASE_URL, imagegen_key
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
                    "No image API configuration found. Set IMAGEGEN_API_KEY, "
                    "set OPENAI_BASE_URL and OPENAI_API_KEY for the fixed relay, or "
                    "select a compatible Codex provider in CC Switch"
                )
            base_url, key, source = current

    # A persistent user-level model setting should win over a stale process
    # environment inherited by a long-lived desktop session.
    model = (
        _user_environment_value("IMAGEGEN_MODEL")
        or _environment_value("IMAGEGEN_MODEL")
        or skill_env.get("IMAGEGEN_MODEL", "")
        or DEFAULT_MODEL
    )
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
        raise ImageGenError("Image size must not exceed 7680px on either edge")
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


SIZE_ALIASES = {"1K", "2K", "4K", "8K"}


def normalize_size(size: str) -> str:
    normalized = size.strip().upper()
    if normalized in SIZE_ALIASES:
        return normalized
    return validate_size(normalized)


def legacy_pixel_size(size: str, aspect_ratio: str) -> str:
    """Map upstream size aliases to a multipart relay's pixel-size format."""
    if size not in SIZE_ALIASES:
        return validate_size(size)
    dimensions = {
        "1K": {"auto": "1024x1024", "1:1": "1024x1024", "3:2": "1536x1024", "2:3": "1024x1536"},
        "2K": {"auto": "2048x2048", "1:1": "2048x2048", "3:2": "2048x1360", "2:3": "1360x2048"},
        "4K": {"auto": "3840x2160", "1:1": "3840x3840", "3:2": "3840x2560", "2:3": "2560x3840"},
        "8K": {"auto": "7680x4320", "1:1": "7680x7680", "3:2": "7680x5120", "2:3": "5120x7680"},
    }
    if aspect_ratio in dimensions[size]:
        return validate_size(dimensions[size][aspect_ratio])
    match = re.fullmatch(r"([1-9]\d*):([1-9]\d*)", aspect_ratio)
    if not match:
        raise ImageGenError("Aspect ratio must use positive integers separated by ':'")
    ratio_width, ratio_height = int(match.group(1)), int(match.group(2))
    long_edge = {"1K": 1024, "2K": 2048, "4K": 3840, "8K": 7680}[size]
    if ratio_width >= ratio_height:
        width = long_edge
        height = max(16, int(round(long_edge * ratio_height / ratio_width / 16)) * 16)
    else:
        height = long_edge
        width = max(16, int(round(long_edge * ratio_width / ratio_height / 16)) * 16)
    return validate_size(f"{width}x{height}")


def validate_quality(quality: str) -> str:
    normalized = quality.strip().lower()
    if normalized not in QUALITY_VALUES:
        choices = ", ".join(sorted(QUALITY_VALUES))
        raise ImageGenError(f"Quality must be one of: {choices}")
    return normalized


def validate_aspect_ratio(aspect_ratio: str) -> str:
    normalized = aspect_ratio.strip().lower()
    if normalized in ASPECT_RATIOS:
        return normalized
    if not re.fullmatch(r"[1-9]\d*:[1-9]\d*", normalized):
        raise ImageGenError("Aspect ratio must use positive integers separated by ':'")
    return normalized


def _dimensions_from_image_bytes(data: bytes, mime: str, label: str) -> tuple[int, int]:
    """Read dimensions from supported image headers without requiring Pillow."""
    width = height = 0
    if mime == "image/png" and len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
    elif mime == "image/gif" and len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
    elif mime == "image/webp" and len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        offset = 12
        while offset + 8 <= len(data):
            chunk = data[offset : offset + 4]
            chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
            start = offset + 8
            end = start + chunk_size
            if end > len(data):
                break
            if chunk == b"VP8X" and chunk_size >= 10:
                width = 1 + int.from_bytes(data[start + 4 : start + 7], "little")
                height = 1 + int.from_bytes(data[start + 7 : start + 10], "little")
                break
            if chunk == b"VP8L" and chunk_size >= 5 and data[start] == 0x2F:
                bits = data[start + 1 : start + 5]
                width = 1 + (bits[0] | ((bits[1] & 0x3F) << 8))
                height = 1 + ((bits[1] >> 6) | (bits[2] << 2) | ((bits[3] & 0x0F) << 10))
                break
            if chunk == b"VP8 " and chunk_size >= 10:
                frame = data.find(b"\x9d\x01\x2a", start, end)
                if frame >= 0 and frame + 7 <= end:
                    width = int.from_bytes(data[frame + 3 : frame + 5], "little") & 0x3FFF
                    height = int.from_bytes(data[frame + 5 : frame + 7], "little") & 0x3FFF
                    break
            offset = end + (chunk_size & 1)
    elif mime == "image/jpeg" and len(data) >= 4 and data[:2] == b"\xff\xd8":
        offset = 2
        sof_markers = {
            *range(0xC0, 0xC4),
            *range(0xC5, 0xC8),
            *range(0xC9, 0xCC),
            *range(0xCD, 0xD0),
        }
        while offset + 3 < len(data):
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
            segment_length = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                break
            if marker in sof_markers and segment_length >= 7:
                height = int.from_bytes(data[offset + 3 : offset + 5], "big")
                width = int.from_bytes(data[offset + 5 : offset + 7], "big")
                break
            offset += segment_length

    if width <= 0 or height <= 0:
        raise ImageGenError(f"无法读取{label}的宽高；请提供有效的 PNG、JPEG、WEBP 或 GIF 图片")
    return width, height


def image_dimensions(path_value: str, label: str = "Input image") -> tuple[int, int]:
    """Validate a local image and return its pixel dimensions."""
    _, mime, data = _input_image(path_value, label)
    return _dimensions_from_image_bytes(data, mime, label)


def resolve_aspect_ratio(aspect_ratio: str, image_paths: list[str] | None = None) -> tuple[str, str]:
    """Resolve an explicit ratio without forcing a local edit into a crop."""
    normalized = validate_aspect_ratio(aspect_ratio)
    if normalized != "auto":
        return normalized, "user"
    if image_paths:
        # For local edits, ``auto`` means preserve source geometry. The old
        # implementation chose the nearest enum ratio and silently re-composed
        # wide banners and other non-standard layouts.
        return "auto", "input_image"
    return normalized, "model_default"


def source_preserving_edit_size(size: str, image_paths: list[str]) -> str:
    """Choose the lowest valid edit tier while preserving source geometry."""
    if not image_paths:
        raise ImageGenError("无法从空的输入图片列表保留编辑比例")
    width, height = image_dimensions(image_paths[0])
    long_edge = max(width, height)
    short_edge = min(width, height)
    tier_edges = {"1K": 1024, "2K": 2048, "4K": 3840, "8K": 7680}
    if size not in tier_edges:
        return validate_size(size)

    # A very wide/tall source can fall below the relay's minimum total pixels
    # when scaled to a 1K long edge.  Do not fail locally and make Codex issue a
    # second paid command.  Silently choose the first valid supported tier and
    # keep the source ratio; an explicit 2K/4K request remains a lower bound.
    ordered_tiers = ("1K", "2K", "4K", "8K")
    start = ordered_tiers.index(size)
    last_error: ImageGenError | None = None
    for tier in ordered_tiers[start:]:
        target_long = tier_edges[tier]
        scale = target_long / long_edge
        scaled_long = max(16, int(round(long_edge * scale / 16)) * 16)
        scaled_short = max(16, int(round(short_edge * scale / 16)) * 16)
        candidate = (
            f"{scaled_long}x{scaled_short}"
            if width >= height
            else f"{scaled_short}x{scaled_long}"
        )
        try:
            return validate_size(candidate)
        except ImageGenError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ImageGenError("无法为输入图片选择有效的编辑尺寸")


def edit_working_size(size: str, model: str) -> str:
    """Return the edit size, preserving native GPT Image 2 tiers by default."""
    if model in {"gpt-image-2"} and not _environment_value(
        "IMAGEGEN_LEGACY_EDIT_RESIZE"
    ).lower() in {"1", "true", "yes"}:
        return size

    width, height = (int(value) for value in size.split("x"))
    long_edge = max(width, height)
    if long_edge <= EDIT_MAX_EDGE:
        return size

    scale = EDIT_MAX_EDGE / long_edge
    working_width = max(16, int(width * scale) // 16 * 16)
    working_height = max(16, int(height * scale) // 16 * 16)

    # Rounding down can make a source ratio that was exactly 3:1 exceed the
    # provider's ratio limit by one block; trim the long edge if necessary.
    while max(working_width, working_height) > 3 * min(working_width, working_height):
        if working_width >= working_height:
            working_width -= 16
        else:
            working_height -= 16

    return validate_size(f"{working_width}x{working_height}")


def local_postprocess_requested(args: argparse.Namespace) -> bool:
    """Return whether deterministic local output processing was requested."""
    return bool(
        args.output_size
        or args.crop
        or args.output_format != "same"
        or args.output_quality is not None
        or args.output_background
    )


def validate_edit_delivery_options(
    mode: str,
    aspect_ratio: str,
    args: argparse.Namespace,
    story_begun: bool,
) -> None:
    """Block only explicitly requested local post-processing before an edit."""
    if mode != "edit" or story_begun:
        return
    if local_postprocess_requested(args) and not args.allow_postprocess:
        raise ImageGenError(
            "编辑请求默认直接交付上游原图；本次未发送请求，也不会扣费。"
            "已阻止自动 output-size/cover/fill/crop/格式处理。"
            "只有客户明确要求本地变换时才可使用 --allow-postprocess。"
        )


def validate_pro_edit_processing(
    model: str,
    mode: str,
    has_reference: bool,
    args: argparse.Namespace,
) -> None:
    """Backward-compatible helper retained for callers of the 1.8.13 test API."""
    if model != "gemini-3-pro-image" or mode != "edit" or not has_reference:
        return
    geometry_requested = getattr(args, "aspect_ratio", "auto") != "auto" or local_postprocess_requested(args)
    if geometry_requested and not getattr(args, "allow_pro_postprocess", False):
        raise ImageGenError(
            "Pro 编辑默认保持输入图片比例并直接返回上游原图；本次未发送请求，也不会扣费。"
        )


def should_auto_async_local_edit(
    size: str,
    model: str,
    image_count: int,
    input_bytes: int,
    has_mask: bool,
) -> bool:
    """Select async only for local GPT Image 2 edits likely to outlive sync."""
    if model not in SUPPORTED_MODELS or has_mask:
        return False
    width, height = (int(value) for value in size.split("x"))
    if max(width, height) <= EDIT_MAX_EDGE:
        return False
    return (
        image_count >= AUTO_ASYNC_REFERENCE_COUNT
        or input_bytes >= AUTO_ASYNC_REFERENCE_BYTES
    )


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
    text = raw.decode("utf-8", errors="replace").strip()
    if "data:" in text:
        last_event: dict[str, Any] | None = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                last_event = event
        if last_event:
            if last_event.get("url") and not last_event.get("data"):
                last_event["data"] = [{"url": last_event["url"]}]
            return last_event
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImageGenError("Image API returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ImageGenError("Image API returned an invalid JSON object")
    if not result.get("data") and isinstance(result.get("results"), list):
        result["data"] = result["results"]
    if not result.get("data") and result.get("url"):
        result["data"] = [{"url": result["url"]}]
    return result


def _normalize_task_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize common relay wrappers before async completion handling.

    Relays do not all return the completed image list at the same level.  A
    successful task may be wrapped in ``result``, ``output``, ``task`` or a
    single ``image`` object.  Treating those responses as merely "no async
    result" caused the caller to submit the already-billed request again.
    """
    if not isinstance(result, dict):
        return {}
    normalized: dict[str, Any] = dict(result)
    for key in ("result", "output", "task", "response"):
        nested = normalized.get(key)
        if isinstance(nested, dict):
            merged = dict(nested)
            for outer_key in ("id", "task_id", "status", "state", "error", "failure_reason"):
                if outer_key in normalized and outer_key not in merged:
                    merged[outer_key] = normalized[outer_key]
            normalized = merged
            break
    data = normalized.get("data")
    if isinstance(data, dict):
        normalized["data"] = [data]
    elif data is None:
        for key in ("images", "outputs", "files"):
            value = normalized.get(key)
            if isinstance(value, list) and value:
                normalized["data"] = value
                break
        else:
            image = normalized.get("image")
            if isinstance(image, dict):
                normalized["data"] = [image]
            elif isinstance(image, str) and image:
                normalized["data"] = [{"url": image}]
    data = normalized.get("data")
    if isinstance(data, list):
        normalized["data"] = [
            {"url": item} if isinstance(item, str) else item
            for item in data
            if isinstance(item, (dict, str))
        ]
    status = normalized.get("status") or normalized.get("state")
    if status is not None:
        normalized["status"] = str(status).strip().lower()
    return normalized


def _post_image_request(
    endpoint: str,
    key: str,
    body: bytes,
    content_type: str,
    timeout: int,
    idempotency_key: str = "",
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "Accept": "application/json",
        "User-Agent": f"Matrixapi-imagegen-skill/{SKILL_VERSION}",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = _read_limited(response, MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        detail = _safe_http_detail(exc, key)
        # A gateway/rate-limit 5xx/429 is a request-scoped upstream failure.
        # Do not cache it as an unknown paid outcome: the next request must
        # perform a fresh status check instead of replaying this error.
        raise ImageGenError(
            _format_upstream_error(detail, exc.code),
            status_code=exc.code,
            retryable=exc.code in {408, 425, 429, 500, 502, 503, 504},
        ) from exc
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
    options: dict[str, Any] | None = None,
    aspect_ratio: str = "",
    image_urls: list[str] | None = None,
    stream: bool = False,
    async_mode: bool = False,
    webhook: str = "",
    metadata: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Call the JSON generations endpoint."""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
    }
    if count != 1:
        payload["n"] = count
    if options:
        payload.update(options)
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio
    if image_urls:
        payload["images"] = image_urls
    payload["stream"] = stream
    payload["async"] = async_mode
    if webhook:
        payload["webhook"] = webhook
    if metadata:
        payload["metadata"] = metadata
    try:
        return _post_image_request(
            endpoint,
            key,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
            timeout,
            idempotency_key,
        )
    except ImageGenError as exc:
        limit = _prompt_limit_from_error(exc)
        if limit is None or len(prompt) <= limit:
            raise
        fallback_prompt = compact_prompt_for_upstream(prompt, limit)
        if fallback_prompt == prompt:
            raise
        payload["prompt"] = fallback_prompt
        result = _post_image_request(
            endpoint,
            key,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
            timeout,
            _fallback_idempotency_key(idempotency_key, fallback_prompt),
        )
        result["_matrixapi_prompt_compacted"] = True
        result["_matrixapi_prompt_limit"] = limit
        return result


def _status_endpoint(endpoint: str, task_id: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    path = parsed.path
    marker = "/images/generations"
    if marker in path:
        path = path.split(marker, 1)[0] + f"/status/{urllib.parse.quote(task_id, safe='')}"
    else:
        path = path.rstrip("/") + f"/status/{urllib.parse.quote(task_id, safe='')}"
    return urllib.parse.urlunparse(parsed._replace(path=path, query="", fragment=""))


def _get_image_request(endpoint: str, key: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": f"Matrixapi-imagegen-skill/{SKILL_VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _parse_api_response(_read_limited(response, MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as exc:
        detail = _safe_http_detail(exc, key)
        raise ImageGenError(
            _format_upstream_error(detail, exc.code),
            status_code=exc.code,
            retryable=exc.code in {404, 408, 409, 425, 429, 500, 502, 503, 504},
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ImageGenError(f"Image status request failed: {exc}") from exc


def wait_for_task(
    result: dict[str, Any], endpoint: str, key: str, timeout: int
) -> dict[str, Any]:
    result = _normalize_task_result(result)
    if result.get("data"):
        return result
    task_id = str(result.get("id") or result.get("task_id") or "").strip()
    if not task_id:
        raise ImageGenError("Async image API response did not include a task id")
    deadline = time.monotonic() + timeout
    status_url = _status_endpoint(endpoint, task_id)
    latest = result
    while time.monotonic() < deadline:
        try:
            latest = _normalize_task_result(
                _get_image_request(status_url, key, min(60, max(10, timeout)))
            )
        except ImageGenError as exc:
            # A task can be committed before the relay's status index is
            # visible.  Retry only the free status GET; never submit another
            # billed image request from this path.
            detail = str(exc).lower()
            retryable = any(
                marker in detail
                for marker in (
                    "http 404",
                    "http 408",
                    "http 409",
                    "http 425",
                    "http 429",
                    "http 500",
                    "http 502",
                    "http 503",
                    "http 504",
                    "timed out",
                    "temporarily",
                )
            )
            if not retryable:
                raise
            time.sleep(1)
            continue
        status = str(latest.get("status") or "").lower()
        if status in {
            "succeeded",
            "success",
            "completed",
            "complete",
            "done",
            "finished",
            "ready",
        } or latest.get("data"):
            return latest
        if status in {"failed", "failure", "error", "cancelled", "canceled"}:
            reason = latest.get("failure_reason") or latest.get("error") or status
            reason_text = (
                json.dumps(reason, ensure_ascii=False)
                if isinstance(reason, (dict, list))
                else str(reason)
            )
            raise ImageGenError(_format_upstream_error(reason_text))
        # Results are delivered as soon as the relay reports completion. A
        # one-second interval avoids the old multi-second handoff delay while
        # keeping a single status request in flight.
        time.sleep(1)
    raise ImageGenError(f"Image task timed out after {timeout} seconds: {task_id}")


def response_requires_task_polling(result: dict[str, Any]) -> bool:
    """Return whether a successful image response is a task envelope.

    Some routes acknowledge even a 1K request with ``202`` and a task id,
    rather than returning image data directly. The wire status is not
    available after urllib has parsed a successful response, so decide from
    the response shape: an id without usable image data is a task that must
    be queried. This path only performs free status GETs; it never submits
    another image request.
    """
    normalized = _normalize_task_result(result)
    if normalized.get("data"):
        return False
    return bool(str(normalized.get("id") or normalized.get("task_id") or "").strip())


def resolve_image_task_response(
    result: dict[str, Any], endpoint: str, key: str, timeout: int, async_mode: bool
) -> dict[str, Any]:
    """Poll an acknowledged task when explicitly async or task-shaped."""
    if async_mode or response_requires_task_polling(result):
        return wait_for_task(result, endpoint, key, timeout)
    return result


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


def _input_image_bytes(paths: Iterable[str], mask_path: str | None = None) -> int:
    """Validate local file sizes before building or sending multipart data."""
    total = 0
    labelled_paths = [(path, "Input image") for path in paths]
    if mask_path:
        labelled_paths.append((mask_path, "Mask image"))
    for path_value, label in labelled_paths:
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
        total += size
    return total


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
    boundary = f"----Matrixapi-imagegen-{uuid.uuid4().hex}"
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
    idempotency_key: str = "",
) -> dict[str, Any]:
    if not image_paths:
        raise ImageGenError("At least one --image file is required for editing")
    if len(image_paths) > MAX_INPUT_IMAGES:
        raise ImageGenError(f"At most {MAX_INPUT_IMAGES} input images are supported")
    total_input_bytes = _input_image_bytes(image_paths, mask_path)
    if total_input_bytes > MAX_MULTIPART_BODY_BYTES:
        limit_mib = MAX_MULTIPART_BODY_BYTES // (1024 * 1024)
        actual_mib = total_input_bytes / (1024 * 1024)
        raise ImageGenError(
            f"Combined input images are too large for one upload ({actual_mib:.1f} MiB; "
            f"limit {limit_mib} MiB). Remove unrelated references or submit a smaller set; "
            "the images were not sent and no task was charged."
        )

    fields: list[tuple[str, str]] = [
        ("model", model),
        ("prompt", prompt),
        ("size", size),
    ]
    # The pinned GPT Image 2 contract does not guarantee the generic `n`
    # parameter. A single edit is the normal skill path, so omit n=1 even
    # before the relay's multipart-to-JSON conversion. Other image models keep
    # the legacy multipart count behavior for compatibility.
    if count != 1 and model not in {"gpt-image-2"}:
        fields.append(("n", str(count)))
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
    try:
        return _post_image_request(
            endpoint, key, body, content_type, timeout, idempotency_key
        )
    except ImageGenError as exc:
        limit = _prompt_limit_from_error(exc)
        if limit is None or len(prompt) <= limit:
            raise
        fallback_prompt = compact_prompt_for_upstream(prompt, limit)
        if fallback_prompt == prompt:
            raise
        fallback_fields = [
            (name, fallback_prompt if name == "prompt" else value)
            for name, value in fields
        ]
        fallback_body, fallback_content_type = _multipart_body(fallback_fields, files)
        result = _post_image_request(
            endpoint,
            key,
            fallback_body,
            fallback_content_type,
            timeout,
            _fallback_idempotency_key(idempotency_key, fallback_prompt),
        )
        result["_matrixapi_prompt_compacted"] = True
        result["_matrixapi_prompt_limit"] = limit
        return result


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


def _decode_data_url(url: str) -> tuple[bytes, str]:
    header, encoded = url.split(",", 1)
    try:
        return base64.b64decode(encoded, validate=True), header[5:].split(";", 1)[0]
    except (binascii.Error, ValueError) as exc:
        raise ImageGenError("Image API returned invalid base64 image data") from exc


def _download_image_to_path(
    url: str,
    endpoint: str,
    key: str,
    timeout: int,
    destination: Path,
) -> tuple[bytes, str]:
    """Stream a result URL into ``destination`` and return its header bytes.

    Task status responses remain capped separately. Completed image URLs can be
    much larger at 8K, so read them in bounded chunks and atomically publish the
    final result only after the transfer and image signature validation succeed.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageGenError("Image API returned an unsupported image URL")
    headers = {"User-Agent": f"Matrixapi-imagegen-skill/{SKILL_VERSION}"}
    if _origin(url) == _origin(endpoint):
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            content_length = response.headers.get("Content-Length", "").strip()
            if content_length:
                try:
                    if int(content_length) > MAX_RESULT_IMAGE_BYTES:
                        raise ImageGenError("Generated image exceeds the 512 MB delivery limit")
                except ValueError:
                    pass
            total = 0
            header = b""
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_RESULT_IMAGE_BYTES:
                        raise ImageGenError("Generated image exceeds the 512 MB delivery limit")
                    if len(header) < 64:
                        header += chunk[: 64 - len(header)]
                    handle.write(chunk)
            if total == 0:
                raise ImageGenError("Generated image is empty")
            return header, content_type
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ImageGenError(f"Unable to download the generated image: {exc}") from exc


def save_images(
    result: dict[str, Any], endpoint: str, key: str, output_dir: Path, timeout: int
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = uuid.uuid4().hex[:8]
    paths: list[str] = []

    items = result.get("data") or result.get("results")
    if not isinstance(items, list) or not items:
        raise ImageGenError("Image API returned no image data")

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ImageGenError("Image API returned an invalid image item")
        content_type = ""
        final_path_base = output_dir / f"image-{stamp}-{run_id}-{index}"
        if item.get("b64_json"):
            try:
                data = base64.b64decode(item["b64_json"], validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise ImageGenError("Image API returned invalid base64 image data") from exc
            if not data or len(data) > MAX_IMAGE_BYTES:
                raise ImageGenError("Generated image is empty or too large")
            suffix = _image_extension(data, content_type)
            final_path = final_path_base.with_suffix(suffix)
            temp_path = final_path.with_suffix(final_path.suffix + ".part")
            temp_path.write_bytes(data)
            temp_path.replace(final_path)
            paths.append(str(final_path.resolve()))
            continue
        elif item.get("url"):
            temp_path = final_path_base.with_suffix(".part")
            try:
                header, content_type = _download_image_to_path(
                    str(item["url"]), endpoint, key, timeout, temp_path
                )
                suffix = _image_extension(header, content_type)
                final_path = final_path_base.with_suffix(suffix)
                temp_path.replace(final_path)
                paths.append(str(final_path.resolve()))
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
            continue
        else:
            raise ImageGenError("Image item has neither url nor b64_json")
    return paths


def preview_paths(paths: Iterable[str]) -> list[str]:
    """Return absolute paths normalized for the chat renderer's Markdown URLs."""
    return [Path(path).resolve().as_posix() for path in paths]


def normalize_task_id(value: str | None = None) -> str:
    """Return a unique, filename-safe task id supplied before the API call."""
    if value:
        task_id = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", task_id):
            raise ImageGenError(
                "--task-id must be 8-128 characters using letters, numbers, dot, dash, or underscore"
            )
        return task_id
    return f"task-{time.time_ns()}-{uuid.uuid4().hex[:12]}"


def result_record_path(output_dir: Path, task_id: str) -> Path:
    return output_dir.expanduser().resolve() / f"result-{task_id}.json"


def _idempotency_scope(task_id: str) -> str:
    thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    session_id = os.environ.get("CODEX_SESSION_ID", "").strip()
    if thread_id:
        return f"thread:{thread_id}"
    if session_id:
        return f"session:{session_id}"
    # Codex may omit both identifiers.  Never fall back to task_id here:
    # retries legitimately receive a fresh task id, and using it would make
    # an identical paid request look new and defeat duplicate-charge guards.
    # The request body and ordered reference digests already distinguish
    # different requests; a conservative process-wide scope is safest when
    # no conversation identifier is available.  ``task_id`` remains part of
    # the result record, but not the idempotency fingerprint.
    return "process"


def _local_reference_fingerprint(
    image_paths: list[str], mask_path: str | None
) -> dict[str, Any]:
    def one(path_value: str) -> dict[str, Any]:
        path = Path(path_value).expanduser().resolve()
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise ImageGenError(f"Unable to fingerprint input image: {path}") from exc
        return {
            # Clipboard and recovery flows may copy the same image to a new
            # temporary path.  The path is not part of request identity;
            # otherwise a lost stdout response becomes a second paid request.
            "size": size,
            "sha256": digest.hexdigest(),
        }

    return {
        "images": [one(path) for path in image_paths],
        "mask": one(mask_path) if mask_path else None,
    }


def request_fingerprint(
    task_id: str, request: dict[str, Any], force_new: bool = False
) -> str:
    canonical: dict[str, Any] = {
        "version": IDEMPOTENCY_VERSION,
        "scope": _idempotency_scope(task_id),
        "request": request,
    }
    if force_new:
        canonical["force_new_task_id"] = task_id
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def idempotency_record_path(output_dir: Path, fingerprint: str) -> Path:
    return output_dir.expanduser().resolve() / ".idempotency" / f"{fingerprint}.json"


def _load_idempotency_record(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _cached_result_files_exist(payload: dict[str, Any]) -> bool:
    paths = payload.get("preview_files") or payload.get("download_files")
    return bool(
        isinstance(paths, list)
        and paths
        and all(isinstance(path, str) and Path(path).is_file() for path in paths)
    )


def claim_idempotency(
    output_dir: Path, fingerprint: str, task_id: str, timeout: int
) -> tuple[Path, Path, dict[str, Any] | None]:
    record_path = idempotency_record_path(output_dir, fingerprint)
    lock_path = record_path.with_suffix(".lock")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the deduplication ledger available to the script while hiding it
    # from ordinary Explorer/Codex views.  The files themselves are retained
    # because removing them would re-enable duplicate paid submissions.
    _hide_directory(record_path.parent)
    deadline = time.monotonic() + timeout
    while True:
        now_ms = time.time_ns() // 1_000_000
        record = _load_idempotency_record(record_path)
        if record:
            if int(record.get("version") or 0) != IDEMPOTENCY_VERSION:
                # A ledger from an older Skill has different error-lifecycle
                # semantics; never reuse it for the current request.
                try:
                    record_path.unlink(missing_ok=True)
                except OSError:
                    pass
                record = None
            if record is None:
                continue
            age_ms = now_ms - int(record.get("created_at_ms") or 0)
            if 0 <= age_ms <= IDEMPOTENCY_TTL_MS:
                status = record.get("status")
                if status == "success":
                    payload = record.get("payload")
                    if isinstance(payload, dict) and _cached_result_files_exist(payload):
                        return record_path, lock_path, payload
                if status == "uncertain":
                    detail = str(record.get("error") or "previous identical request did not complete safely")
                    raise ImageGenError(
                        "An identical request in this Codex conversation has already been submitted "
                        "or has an unknown final state, so it was not submitted again. Use --force-new "
                        f"only after the user explicitly confirms a retry. Previous error: {detail[:800]}"
                    )
                if status == "failed":
                    try:
                        record_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                try:
                    record_path.unlink(missing_ok=True)
                except OSError:
                    pass

        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                lock_age_ms = now_ms - int(lock_path.stat().st_mtime_ns // 1_000_000)
            except (FileNotFoundError, OSError):
                continue
            if lock_age_ms > IDEMPOTENCY_TTL_MS:
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise ImageGenError(
                    "An identical image request is still running; it was not submitted again"
                )
            time.sleep(IDEMPOTENCY_WAIT_INTERVAL_SECONDS)
            continue

        os.close(descriptor)
        _atomic_write_json(
            record_path,
            {
                "version": IDEMPOTENCY_VERSION,
                "fingerprint": fingerprint,
                "status": "in_progress",
                "task_id": task_id,
                "created_at_ms": now_ms,
            },
        )
        return record_path, lock_path, None


def finish_idempotency(
    record_path: Path | None,
    lock_path: Path | None,
    fingerprint: str | None,
    task_id: str | None,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
    uncertain: bool = False,
) -> None:
    if record_path is None or fingerprint is None or task_id is None:
        return
    record: dict[str, Any] = {
        "version": IDEMPOTENCY_VERSION,
        "fingerprint": fingerprint,
        "status": "success" if payload is not None else "uncertain" if uncertain else "failed",
        "task_id": task_id,
        "created_at_ms": time.time_ns() // 1_000_000,
    }
    if payload is not None:
        record["payload"] = payload
    else:
        record["error"] = (error or "request failed")[:1000]
    try:
        _atomic_write_json(record_path, record)
        _schedule_result_hide(record_path)
    except ImageGenError:
        pass
    finally:
        if lock_path is not None:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def emit_reused_success(
    cached_payload: dict[str, Any],
    output_dir: Path,
    task_id: str,
    request_started_at_ms: int,
) -> dict[str, Any]:
    payload = dict(cached_payload)
    reused_from_task_id = payload.get("task_id")
    for key in (
        "task_id",
        "request_started_at_ms",
        "request_started_at",
        "completed_at_ms",
        "completed_at",
        "result_file",
        "result_match",
        "result_hide_delay_ms",
    ):
        payload.pop(key, None)
    payload["idempotency_reused"] = True
    payload["reused_from_task_id"] = reused_from_task_id
    return emit_success(payload, output_dir, task_id, request_started_at_ms)


def ensure_new_task(output_dir: Path, task_id: str) -> None:
    if result_record_path(output_dir, task_id).exists():
        raise ImageGenError(
            f"Task id already has a result; choose a new --task-id: {task_id}"
        )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ImageGenError(f"Unable to save story state: {exc}") from exc


def _story_id(task_id: str) -> str:
    return f"story-{uuid.uuid5(uuid.NAMESPACE_URL, task_id).hex}"


def story_state_path(output_dir: Path, story_id: str) -> Path:
    return output_dir.expanduser().resolve() / ".stories" / f"{story_id}.json"


def _load_story_state(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ImageGenError("Story state file is too large")
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImageGenError(f"Story state file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageGenError(f"Unable to read story state: {exc}") from exc
    if not isinstance(state, dict) or state.get("state_version") != STORY_STATE_VERSION:
        raise ImageGenError("Story state file is invalid or incompatible")
    return state


def build_story_page_prompt(root_prompt: str, page: int, total_pages: int) -> str:
    if page == 1:
        phase = (
            "Establish the setting and both main characters, then begin the conflict "
            "with a clear first exchange that leaves forward momentum."
        )
        reference_rule = (
            "Use all supplied reference images together to derive one coherent visual style."
        )
    elif page == total_pages:
        phase = (
            "Continue directly from the previous page into the visual climax and a clear "
            "resolution; do not restart the encounter or introduce unrelated characters."
        )
        reference_rule = "Use the supplied previous page as the exact continuity reference."
    else:
        phase = (
            "Continue directly from the previous page, escalate the action, and advance the "
            "story toward the climax without restarting the encounter."
        )
        reference_rule = "Use the supplied previous page as the exact continuity reference."
    return (
        f"Create comic page {page} of {total_pages}. {reference_rule} {phase} "
        "Choose an effective number of panels and camera angles for this page. Preserve character "
        "designs, costumes, colors, environment, lighting, drawing style, and action continuity. "
        "No captions, speech bubbles, lettering, logos, or watermarks. "
        f"Full story request: {root_prompt.strip()}"
    )


def _claim_story_continuation(path: Path, task_id: str) -> dict[str, Any]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ImageGenError("This story page is already being generated") from exc
    try:
        os.close(descriptor)
        state = _load_story_state(path)
        if state.get("status") != "active":
            raise ImageGenError(
                f"Story cannot continue because its status is {state.get('status', 'unknown')}"
            )
        if state.get("next_task_id") != task_id:
            raise ImageGenError("--task-id does not match this story's next page")
        next_page = int(state.get("page", 0)) + 1
        if next_page > int(state.get("total_pages", 0)):
            raise ImageGenError("This story is already complete")
        state.update(
            {
                "status": "in_progress",
                "pending_page": next_page,
                "current_task_id": task_id,
                "next_task_id": None,
                "updated_at_ms": time.time_ns() // 1_000_000,
            }
        )
        _atomic_write_json(path, state)
        return state
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _start_story(
    path: Path,
    task_id: str,
    total_pages: int,
    root_prompt: str,
    output_dir: Path,
    model: str,
    size: str,
    quality: str,
    aspect_ratio: str,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ImageGenError("This story task is already starting") from exc
    try:
        os.close(descriptor)
        if path.exists():
            raise ImageGenError(
                "This story task already exists; do not submit its first page again"
            )
        now_ms = time.time_ns() // 1_000_000
        state = {
            "state_version": STORY_STATE_VERSION,
            "story_id": path.stem,
            "status": "in_progress",
            "page": 0,
            "pending_page": 1,
            "total_pages": total_pages,
            "root_prompt": root_prompt,
            "output_dir": output_dir.resolve().as_posix(),
            "model": model,
            "size": size,
            "quality": quality,
            "aspect_ratio": aspect_ratio,
            "last_original_file": None,
            "current_task_id": task_id,
            "next_task_id": None,
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
        }
        _atomic_write_json(path, state)
        _schedule_result_hide(path)
        return state
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _complete_story_page(
    path: Path, task_id: str, original_file: str
) -> dict[str, Any]:
    state = _load_story_state(path)
    if state.get("status") != "in_progress" or state.get("current_task_id") != task_id:
        raise ImageGenError("Story state no longer matches the completed page")
    page = int(state["pending_page"])
    total_pages = int(state["total_pages"])
    state.update(
        {
            "page": page,
            "pending_page": None,
            "last_original_file": Path(original_file).resolve().as_posix(),
            "current_task_id": None,
            "updated_at_ms": time.time_ns() // 1_000_000,
        }
    )
    if page >= total_pages:
        state["status"] = "completed"
        state["next_task_id"] = None
    else:
        state["status"] = "active"
        state["next_task_id"] = normalize_task_id()
    _atomic_write_json(path, state)
    _schedule_result_hide(path)
    return state


def _fail_story_page(path: Path | None, task_id: str | None, error: str) -> None:
    if path is None or task_id is None or not path.is_file():
        return
    try:
        state = _load_story_state(path)
        if state.get("status") != "in_progress" or state.get("current_task_id") != task_id:
            return
        state.update(
            {
                "status": "failed",
                "pending_page": None,
                "next_task_id": None,
                "error": error[:1000],
                "updated_at_ms": time.time_ns() // 1_000_000,
            }
        )
        _atomic_write_json(path, state)
        _schedule_result_hide(path)
    except ImageGenError:
        pass


def story_result_payload(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "story_id": state["story_id"],
        "state_file": path.resolve().as_posix(),
        "page": state["page"],
        "total_pages": state["total_pages"],
        "status": state["status"],
    }
    if state.get("status") == "active":
        payload.update(
            {
                "next_task_id": state["next_task_id"],
                "next_arguments": [
                    "--story-next",
                    path.resolve().as_posix(),
                    "--task-id",
                    state["next_task_id"],
                ],
            }
        )
    return payload


def _utc_millis_text(timestamp_ms: int) -> str:
    seconds, millis = divmod(timestamp_ms, 1000)
    return f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(seconds))}.{millis:03d}Z"


def format_display_summary(
    requested_size: str,
    actual_size: str,
    quality: str,
    aspect_ratio: str,
) -> str:
    """Build the local-only metadata line shown beside a delivered image."""
    requested = str(requested_size or actual_size or "auto").strip() or "auto"
    actual = str(actual_size or "").strip()
    parts = [f"尺寸：{requested}"]
    if actual and actual != requested:
        parts.append(f"实际像素：{actual}")
    parts.append(f"比例：{str(aspect_ratio or 'auto').strip() or 'auto'}")
    parts.append(f"画质：{str(quality or 'auto').strip() or 'auto'}")
    return "｜".join(parts)


def _schedule_result_hide(path: Path) -> bool:
    """Hide a delivered result later without delaying stdout or command exit."""
    if os.name != "nt":
        return False
    helper = Path(__file__).resolve().with_name("hide_result.py")
    if not helper.is_file():
        return False
    try:
        subprocess.Popen(
            [
                sys.executable,
                str(helper),
                str(path),
                "--delay-ms",
                str(RESULT_HIDE_DELAY_MS),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=0x08000000 | 0x00000008,
        )
    except OSError:
        return False
    return True


def emit_success(
    payload: dict[str, Any],
    output_dir: Path,
    task_id: str,
    request_started_at_ms: int,
) -> dict[str, Any]:
    """Atomically persist and emit the exact current-task result once."""
    completed_at_ms = max(time.time_ns() // 1_000_000, request_started_at_ms)
    result_path = result_record_path(output_dir, task_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    enriched = dict(payload)
    enriched.update(
        {
            "task_id": task_id,
            "request_started_at_ms": request_started_at_ms,
            "request_started_at": _utc_millis_text(request_started_at_ms),
            "completed_at_ms": completed_at_ms,
            "completed_at": _utc_millis_text(completed_at_ms),
            "result_file": result_path.as_posix(),
            "result_match": {
                "task_id": task_id,
                "not_before_ms": request_started_at_ms,
                "completed_at_ms": completed_at_ms,
            },
            "result_hide_delay_ms": RESULT_HIDE_DELAY_MS if os.name == "nt" else None,
        }
    )
    temp_path = result_path.with_suffix(result_path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(result_path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ImageGenError(f"Unable to save the current task result JSON: {exc}") from exc

    print(json.dumps(enriched, ensure_ascii=False), flush=True)
    _schedule_result_hide(result_path)
    return enriched


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt",
        help="Prompt text; sent verbatim without local compaction or length truncation",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="Read UTF-8 prompt text from a file; avoids shell quoting errors on Windows",
    )
    parser.add_argument("--model", help="Model id; defaults to IMAGEGEN_MODEL or gpt-image-2")
    parser.add_argument(
        "--provider",
        choices=("auto", "yaliai"),
        default=None,
        help="Optional provider adapter; use yaliai only when the VPS selected the Yali upstream",
    )
    parser.add_argument(
        "--image",
        "--reference-image",
        dest="images",
        action="append",
        metavar="PATH",
        help="Local input/reference image; repeat for multiple images and enable edit mode",
    )
    parser.add_argument(
        "--reference-url",
        dest="reference_urls",
        action="append",
        metavar="URL",
        help="Public reference image URL; repeat up to 16 times for JSON edit requests",
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
    parser.add_argument("--size", help="1K, 2K, 4K, or WIDTHxHEIGHT; defaults to 1K")
    parser.add_argument(
        "--aspect-ratio",
        default="auto",
        help="Upstream aspect ratio for JSON generation/edit requests; explicit ratios are passed through",
    )
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--quality", choices=sorted(QUALITY_VALUES), default="auto")
    parser.add_argument("--stream", action="store_true", help="Request SSE output")
    parser.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="Submit an async task and poll /v1/status/{task_id}",
    )
    parser.add_argument("--webhook", help="Public HTTPS callback URL for async tasks")
    parser.add_argument("--metadata", help="Lightweight JSON object attached to async tasks")
    parser.add_argument("--background")
    parser.add_argument("--input-fidelity")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument(
        "--task-id",
        help="Unique id for matching this command's result without scanning output directories",
    )
    parser.add_argument(
        "--force-new",
        action="store_true",
        help="Submit an identical request only after the user explicitly confirms a paid retry",
    )
    parser.add_argument(
        "--story-pages",
        type=int,
        help="Start a sequential comic story with this many pages; each command returns one page",
    )
    parser.add_argument(
        "--story-next",
        type=Path,
        help="Continue the exact story state returned by the preceding successful page",
    )
    parser.add_argument(
        "--output-size",
        help="本地最终输出尺寸 WIDTHxHEIGHT；生成完成后精确缩放/裁剪，不发送给模型",
    )
    parser.add_argument(
        "--allow-postprocess",
        action="store_true",
        help="仅在客户明确要求本地尺寸、裁剪或格式转换时允许编辑后处理",
    )
    parser.add_argument(
        "--allow-edit-geometry",
        action="store_true",
        help="仅在客户明确要求上游编辑比例时允许发送非 auto 比例",
    )
    parser.add_argument(
        "--fit",
        choices=("cover", "contain", "fill", "inside", "outside"),
        default="cover",
        help="本地输出尺寸的适配方式，默认 cover",
    )
    parser.add_argument(
        "--position",
        choices=(
            "center",
            "top",
            "bottom",
            "left",
            "right",
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
        ),
        default="center",
        help="cover/contain 裁剪或留白位置，默认 center",
    )
    parser.add_argument(
        "--crop",
        help="本地像素裁剪区域 x,y,width,height；可与 --output-size 组合",
    )
    parser.add_argument(
        "--output-format",
        choices=("same", "png", "jpeg", "jpg", "webp", "avif"),
        default="same",
        help="本地最终输出格式，默认保持模型返回格式",
    )
    parser.add_argument(
        "--output-quality",
        type=int,
        help="本地 JPEG/WebP/AVIF 质量 1-100；不传则使用 90",
    )
    parser.add_argument(
        "--output-background",
        help="本地 contain/JPEG 画布背景色，例如 #FFFFFF 或 #RRGGBBAA",
    )
    parser.add_argument(
        "--postprocess-dir",
        type=Path,
        help="本地后处理输出目录；默认使用原图目录下的 processed 子目录",
    )
    parser.add_argument(
        "--process-only",
        action="store_true",
        help="只处理已有 --image 文件，不调用图片 API",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Overall request/task timeout in seconds (10-600; default 600)",
    )
    parser.add_argument("--check-config", action="store_true")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.prompt is not None and args.prompt_file is not None:
        print("--prompt and --prompt-file cannot be used together", file=sys.stderr)
        return 2
    if args.prompt_file is not None:
        try:
            args.prompt = _read_prompt_file(args.prompt_file)
            # A prompt-file may be created beside the package by a shell or
            # front-end.  Its contents have already been read, so hide it at
            # once instead of leaving customer-visible prompt text behind.
            # This is deliberately non-destructive and never changes prompt
            # content, length limits, or the outbound request.
            _hide_directory(args.prompt_file.expanduser())
        except ImageGenError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    request_started_at_ms = time.time_ns() // 1_000_000
    task_id: str | None = None
    story_path: Path | None = None
    story_state: dict[str, Any] | None = None
    story_begun = False
    story_root_prompt: str | None = None
    story_page: int | None = None
    provider = "auto"
    idempotency_fingerprint: str | None = None
    idempotency_record: Path | None = None
    idempotency_lock: Path | None = None
    request_submitted = False
    try:
        if args.n < 1 or args.n > 4:
            raise ImageGenError("Image count must be between 1 and 4")
        if args.timeout < 10 or args.timeout > 600:
            raise ImageGenError("Timeout must be between 10 and 600 seconds")
        if args.output_quality is not None and not 1 <= args.output_quality <= 100:
            raise ImageGenError("--output-quality must be between 1 and 100")
        provider = selected_provider(args.provider)
        if args.story_pages is not None and args.story_next is not None:
            raise ImageGenError("Use --story-pages or --story-next, not both")
        if args.story_pages is not None:
            if args.story_pages < 2 or args.story_pages > MAX_STORY_PAGES:
                raise ImageGenError(
                    f"--story-pages must be between 2 and {MAX_STORY_PAGES}"
                )
            if args.n != 1:
                raise ImageGenError("Sequential stories generate exactly one page per command")
            if args.process_only:
                raise ImageGenError("--story-pages cannot be combined with --process-only")
            if args.aspect_ratio == "auto":
                args.aspect_ratio = "2:3"
            args.async_mode = True
            story_root_prompt = (args.prompt or "").strip()
            story_page = 1
        elif args.story_next is not None:
            if args.process_only:
                raise ImageGenError("--story-next cannot be combined with --process-only")
            story_path = args.story_next.expanduser().resolve()
            story_state = _load_story_state(story_path)
            if story_state.get("status") != "active":
                raise ImageGenError(
                    f"Story cannot continue because its status is {story_state.get('status', 'unknown')}"
                )
            story_root_prompt = str(story_state.get("root_prompt") or "").strip()
            story_page = int(story_state.get("page", 0)) + 1
            total_pages = int(story_state.get("total_pages", 0))
            args.prompt = build_story_page_prompt(
                story_root_prompt, story_page, total_pages
            )
            args.model = str(story_state.get("model") or "").strip()
            args.size = str(story_state.get("size") or "").strip()
            args.quality = str(story_state.get("quality") or "").strip()
            args.aspect_ratio = str(story_state.get("aspect_ratio") or "").strip()
            args.images = [str(story_state.get("last_original_file") or "").strip()]
            args.reference_urls = []
            args.mask = None
            args.mode = "auto"
            args.n = 1
            args.stream = False
            args.async_mode = True
            args.webhook = None
            args.metadata = None
        requested_size = normalize_size(args.size or "1K")

        image_paths = list(args.images or [])
        reference_urls = list(args.reference_urls or [])
        if args.process_only:
            task_id = normalize_task_id(args.task_id)
            if not image_paths:
                raise ImageGenError("--process-only requires at least one --image file")
            if reference_urls or args.mask or args.stream or args.async_mode or args.webhook:
                raise ImageGenError(
                    "--process-only only accepts local --image files and local post-processing options"
                )
            if (
                args.output_size is None
                and args.crop is None
                and args.output_format == "same"
                and args.output_background is None
            ):
                raise ImageGenError(
                    "--process-only requires --output-size, --crop, or --output-format"
                )
            output_dir = args.postprocess_dir or args.out_dir or (
                Path.home() / ".codex" / "generated_images" / SKILL_NAME / "processed"
            )
            ensure_new_task(output_dir, task_id)
            try:
                processed = process_many(
                    image_paths,
                    output_dir,
                    output_size=args.output_size,
                    fit=args.fit,
                    position=args.position,
                    crop=args.crop,
                    output_format=args.output_format,
                    quality=args.output_quality or 90,
                    background_color=args.output_background,
                )
            except PostprocessError as exc:
                raise ImageGenError(str(exc)) from exc
            files = [item["output"] for item in processed]
            preview_files = preview_paths(files)
            emit_success(
                {
                    "ok": True,
                    "skill_name": SKILL_NAME,
                    "version": SKILL_VERSION,
                    "mode": "local-process",
                    "count": len(files),
                    "files": files,
                    "original_files": preview_paths(image_paths),
                    "processed_files": files,
                    "preview_files": preview_files,
                    "download_files": preview_files,
                    "postprocess_manifest": str(Path(output_dir).resolve() / "postprocess-manifest.json"),
                },
                output_dir,
                task_id,
                request_started_at_ms,
            )
            return 0

        base_url, key, model, source = discover_credentials()
        model = (args.model or model).strip()
        generation_url = generation_endpoint(base_url)
        edit_url = edit_endpoint(base_url)
        if args.check_config:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "skill_name": SKILL_NAME,
                        "version": SKILL_VERSION,
                        "credential_source": source,
                        "model": model,
                        "supported_models": list(SUPPORTED_MODELS),
                        "prompt_limit": None,
                        "supported_modes": [
                            "generate",
                            "edit",
                            "url-reference-edit",
                            "async",
                            "stream",
                            "sequential-story",
                            "local-process",
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        task_id = normalize_task_id(args.task_id)
        if story_state is not None:
            state_output_dir = str(story_state.get("output_dir") or "").strip()
            if not state_output_dir:
                raise ImageGenError("Story state does not contain an output directory")
            output_dir = Path(state_output_dir)
            model = str(story_state.get("model") or model).strip()
        else:
            output_dir = args.out_dir or (
                Path.home() / ".codex" / "generated_images" / SKILL_NAME
            )
        ensure_new_task(output_dir, task_id)

        if args.story_pages is not None:
            if not story_root_prompt:
                raise ImageGenError("Prompt must not be empty")
            raw_prompt = build_story_page_prompt(
                story_root_prompt, 1, args.story_pages
            )
        else:
            raw_prompt = (args.prompt or "").strip()
        if not raw_prompt:
            raise ImageGenError("Prompt must not be empty")
        original_prompt_chars = len(raw_prompt)
        # Never alter a customer prompt locally.  Compression can discard
        # constraints and exact wording, producing an unrelated image.  The
        # configured upstream alone decides whether it accepts the full text.
        prompt = raw_prompt
        prompt_compacted = False
        prompt_limit = None

        quality = validate_quality(args.quality)
        aspect_ratio = validate_aspect_ratio(args.aspect_ratio)
        aspect_ratio_source = "user" if aspect_ratio != "auto" else "model_default"
        if args.output_size:
            try:
                output_width, output_height = parse_output_size(args.output_size)
            except PostprocessError as exc:
                raise ImageGenError(str(exc)) from exc
            if args.size is None:
                longest = max(output_width, output_height)
                requested_size = (
                    "1K"
                    if longest <= 1024
                    else "2K"
                    if longest <= 2048
                    else "4K"
                    if longest <= 3840
                    else "8K"
                )
            if aspect_ratio == "auto":
                aspect_ratio = (
                    "1:1"
                    if output_width == output_height
                    else "3:2"
                    if output_width > output_height
                    else "2:3"
                )
                aspect_ratio_source = "output_size"
        if image_paths and reference_urls:
            raise ImageGenError("Use --image or --reference-url, not both")
        if args.mode == "generate" and (image_paths or reference_urls or args.mask):
            raise ImageGenError("--mode generate cannot be combined with reference images")
        if args.mode == "edit" and not (image_paths or reference_urls):
            raise ImageGenError("--mode edit requires --image or --reference-url")
        if args.mask and not image_paths:
            raise ImageGenError("--mask requires at least one local --image")
        if args.mask and not mask_support_enabled(model):
            raise ImageGenError(
                "当前模型编辑通道未启用本地遮罩；本次请求未发送，也不会扣费。"
                "请移除 --mask，使用整图参考编辑，并在提示词中说明需要修改的区域。"
                f"仅当中转站确认该模型支持 mask 后，才可设置 {MASK_SUPPORT_ENV}=1。"
            )
        reference_count = len(image_paths) + len(reference_urls)
        if provider == YALIAI_PROVIDER and reference_count > MAX_YALIAI_SOURCE_IMAGES:
            raise ImageGenError(
                f"亚立适配最多处理 {MAX_YALIAI_SOURCE_IMAGES} 张原始参考图；"
                "请减少数量后再提交，本次未发送，也不会扣费。"
            )
        if provider != YALIAI_PROVIDER and reference_count > MAX_INPUT_IMAGES:
            raise ImageGenError(f"At most {MAX_INPUT_IMAGES} reference images are supported")
        if args.webhook and not args.async_mode:
            raise ImageGenError("--webhook requires --async")
        if args.metadata:
            try:
                metadata = json.loads(args.metadata)
            except json.JSONDecodeError as exc:
                raise ImageGenError("--metadata must be a valid JSON object") from exc
            if not isinstance(metadata, dict):
                raise ImageGenError("--metadata must be a JSON object")
        else:
            metadata = None
        if image_paths and (args.stream or args.async_mode) and model not in {
            "gpt-image-2",
            "gemini-3-pro-image",
        }:
            raise ImageGenError(
                "This relay only supports --stream/--async for local GPT Image 2 edits"
            )

        mode = "edit" if (image_paths or reference_urls) else "generate"
        if image_paths and aspect_ratio == "auto":
            aspect_ratio, aspect_ratio_source = resolve_aspect_ratio(
                aspect_ratio, image_paths
            )
        edit_input_size = None
        input_bytes = None
        request_image_paths = list(image_paths)
        reference_pack = {"enabled": False, "packed": False}
        if image_paths:
            input_bytes = _input_image_bytes(image_paths, args.mask)
            if provider == YALIAI_PROVIDER:
                try:
                    request_image_paths, reference_pack = pack_yaliai_references(
                        image_paths, output_dir
                    )
                except ReferencePackError as exc:
                    raise ImageGenError(str(exc)) from exc
            if aspect_ratio == "auto":
                edit_input_size = source_preserving_edit_size(
                    requested_size, image_paths
                )
            else:
                edit_input_size = legacy_pixel_size(requested_size, aspect_ratio)
            size = edit_input_size
            size = edit_working_size(size, model)
        else:
            size = requested_size
            if reference_urls:
                edit_input_size = requested_size

        requested_async = args.async_mode
        auto_async = bool(
            image_paths
            and input_bytes is not None
            and should_auto_async_local_edit(
                size,
                model,
                len(image_paths),
                input_bytes,
                bool(args.mask),
            )
        )
        async_mode = args.async_mode or auto_async
        async_reason = "large_local_edit" if auto_async else None
        async_fallback = None
        if image_paths and args.metadata and auto_async:
            raise ImageGenError(
                "Metadata for a large local edit requires explicit --async; "
                "the automatic async path does not attach metadata to multipart requests"
            )

        idempotency_fingerprint = request_fingerprint(
            task_id,
            {
                "base_url": base_url,
                "mode": mode,
                "model": model,
                "provider": provider,
                "prompt": prompt,
                "size": size,
                "requested_size": requested_size,
                "quality": quality,
                "aspect_ratio": aspect_ratio,
                "n": args.n,
                "stream": args.stream,
                "async": async_mode,
                "webhook": args.webhook or "",
                "metadata": metadata,
                "background": args.background or "",
                "input_fidelity": args.input_fidelity or "",
                "reference_urls": reference_urls,
                "local_references": _local_reference_fingerprint(
                    image_paths, args.mask
                ),
                "reference_pack": reference_pack,
                "story_pages": args.story_pages,
                "story_page": story_page,
                "story_next": story_path.as_posix() if story_path else "",
                "output_size": args.output_size or "",
                "fit": args.fit,
                "position": args.position,
                "crop": args.crop or "",
                "output_format": args.output_format,
                "output_quality": args.output_quality,
                "output_background": args.output_background or "",
            },
            force_new=args.force_new,
        )
        (
            idempotency_record,
            idempotency_lock,
            cached_payload,
        ) = claim_idempotency(
            output_dir,
            idempotency_fingerprint,
            task_id,
            args.timeout,
        )
        if cached_payload is not None:
            emit_reused_success(
                cached_payload,
                output_dir,
                task_id,
                request_started_at_ms,
            )
            return 0

        if args.story_next is not None:
            if story_path is None:
                raise ImageGenError("Story state path is missing")
            story_state = _claim_story_continuation(story_path, task_id)
            story_begun = True
        elif args.story_pages is not None:
            story_path = story_state_path(output_dir, _story_id(task_id))
            story_state = _start_story(
                story_path,
                task_id,
                args.story_pages,
                story_root_prompt or "",
                output_dir,
                model,
                requested_size,
                quality,
                aspect_ratio,
            )
            story_begun = True

        validate_edit_delivery_options(
            mode,
            aspect_ratio,
            args,
            story_begun,
        )

        options = _option_fields(quality, args.background, args.input_fidelity)
        if image_paths:
            # For auto local edits the pixel size is the source-ratio signal;
            # do not send a forced 1:1/3:2/2:3 enum that would re-compose it.
            if aspect_ratio != "auto":
                options["aspect_ratio"] = aspect_ratio
            if args.stream:
                options["stream"] = "true"
            if async_mode:
                options["async"] = "true"
            request_submitted = True
            result = call_edit_api(
                edit_url,
                key,
                model,
                prompt,
                size,
                args.n,
                request_image_paths,
                args.mask,
                args.timeout,
                options,
                idempotency_fingerprint,
            )
            prompt_compacted = bool(result.pop("_matrixapi_prompt_compacted", False))
            prompt_limit = result.pop("_matrixapi_prompt_limit", None)
            result_endpoint = generation_url if async_mode else edit_url
            result = resolve_image_task_response(
                result, generation_url, key, args.timeout, async_mode
            )
        else:
            options["aspect_ratio"] = aspect_ratio
            request_submitted = True
            result = call_api(
                generation_url,
                key,
                model,
                prompt,
                size,
                args.n,
                args.timeout,
                options,
                aspect_ratio=aspect_ratio,
                image_urls=reference_urls or None,
                stream=args.stream,
                async_mode=async_mode,
                webhook=args.webhook or "",
                metadata=metadata,
                idempotency_key=idempotency_fingerprint,
            )
            prompt_compacted = bool(result.pop("_matrixapi_prompt_compacted", False))
            prompt_limit = result.pop("_matrixapi_prompt_limit", None)
            result_endpoint = generation_url
            result = resolve_image_task_response(
                result, generation_url, key, args.timeout, async_mode
            )
        files = save_images(
            result,
            result_endpoint,
            key,
            output_dir,
            args.timeout,
        )
        original_files = preview_paths(files)
        processed_files = files.copy()
        postprocess_requested = local_postprocess_requested(args)
        postprocess_manifest = None
        if postprocess_requested:
            processed_dir = args.postprocess_dir or (output_dir / "processed")
            try:
                processed = process_many(
                    files,
                    processed_dir,
                    output_size=args.output_size,
                    fit=args.fit,
                    position=args.position,
                    crop=args.crop,
                    output_format=args.output_format,
                    quality=args.output_quality or 90,
                    background_color=args.output_background,
                )
            except PostprocessError as exc:
                _fail_story_page(story_path, task_id, str(exc))
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": f"图片已生成，但本地后处理失败；原始图片仍保留在 {output_dir}: {exc}",
                            "mode": mode,
                            "model": model,
                            "original_files": original_files,
                            "processed_files": [],
                            "postprocess": True,
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
                return 1
            processed_files = [item["output"] for item in processed]
            postprocess_manifest = str(Path(processed_dir).resolve() / "postprocess-manifest.json")
        preview_files = preview_paths(processed_files)
        download_files = preview_files.copy()
        story_payload = None
        if story_begun:
            if story_path is None or not original_files:
                raise ImageGenError("Story page completed without a usable original image")
            story_state = _complete_story_page(story_path, task_id, original_files[0])
            story_payload = story_result_payload(story_path, story_state)
        display_summary = format_display_summary(
            requested_size=requested_size,
            actual_size=size,
            quality=quality,
            aspect_ratio=aspect_ratio,
        )
        success_payload = {
            "ok": True,
            "skill_name": SKILL_NAME,
            "version": SKILL_VERSION,
            "mode": mode,
            "model": model,
            "provider": provider,
            "count": len(files),
            "prompt_limit": prompt_limit,
            "prompt_compacted": prompt_compacted,
            "prompt_original_chars": original_prompt_chars,
            "prompt_chars": len(prompt),
            "size": size,
            "requested_size": requested_size,
            "edit_size": size if mode == "edit" else None,
            "resized_for_edit": mode == "edit" and size != edit_input_size,
            "quality": quality,
            "aspect_ratio": aspect_ratio,
            "aspect_ratio_source": aspect_ratio_source,
            "display_summary": display_summary,
            "async": async_mode,
            "async_requested": requested_async,
            "async_auto": auto_async,
            "async_reason": async_reason,
            "async_fallback": async_fallback,
            "stream": args.stream,
            "input_images": len(image_paths) + len(reference_urls),
            "request_input_images": len(request_image_paths) + len(reference_urls),
            "input_bytes": input_bytes,
            "reference_pack": reference_pack,
            "mask": bool(args.mask),
            "files": files,
            "original_files": original_files,
            "processed_files": processed_files,
            "postprocess": postprocess_requested,
            "postprocess_manifest": postprocess_manifest,
            "preview_files": preview_files,
            "download_files": download_files,
        }
        if story_payload is not None:
            success_payload["story"] = story_payload
        cached_success_payload = dict(success_payload)
        cached_success_payload["task_id"] = task_id
        finish_idempotency(
            idempotency_record,
            idempotency_lock,
            idempotency_fingerprint,
            task_id,
            payload=cached_success_payload,
        )
        emit_success(
            success_payload,
            output_dir,
            task_id,
            request_started_at_ms,
        )
        return 0
    except ImageGenError as exc:
        _fail_story_page(story_path, task_id, str(exc))
        # HTTP 503/429/5xx from the image endpoint is returned as the current
        # request's error and is not reused for a later healthy request.  A
        # transport timeout or an unknown post-submit failure remains
        # ``uncertain`` to prevent a duplicate paid submission.
        transient_upstream_failure = exc.retryable and exc.status_code in {
            408,
            425,
            429,
            500,
            502,
            503,
            504,
        }
        finish_idempotency(
            idempotency_record,
            idempotency_lock,
            idempotency_fingerprint,
            task_id,
            error=str(exc),
            uncertain=request_submitted and not transient_upstream_failure,
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "skill_name": SKILL_NAME,
                    "version": SKILL_VERSION,
                    "task_id": task_id,
                    "request_started_at_ms": request_started_at_ms,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
