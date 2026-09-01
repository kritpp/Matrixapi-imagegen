"""Provider-specific reference image packing helpers.

The Yali OpenAI Images endpoint accepts at most six reference images.  This
module creates deterministic, uncropped contact sheets only when the caller
explicitly selects the Yali adapter.  Other providers never use this path.
"""

from __future__ import annotations

from pathlib import Path
import io
import math
from typing import Iterable


YALIAI_MAX_REFERENCES = 6
YALIAI_MAX_SINGLE_BYTES = 12 * 1024 * 1024
YALIAI_MAX_TOTAL_BYTES = 30 * 1024 * 1024
YALIAI_MAX_GRID_EDGE = 3840


class ReferencePackError(RuntimeError):
    """Raised when Yali reference packing cannot be completed safely."""


def _load_pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - depends on user environment
        raise ReferencePackError(
            "亚立超过 6 张参考图或文件超限时需要 Pillow；请先运行 python -m pip install pillow。"
        ) from exc
    return Image, ImageOps


def _grid_shape(count: int) -> tuple[int, int]:
    columns = 3 if count > 2 else count
    rows = max(1, math.ceil(count / columns))
    return columns, rows


def _encode_grid(images, Image, ImageOps, width: int, height: int, quality: int) -> bytes:
    columns, rows = _grid_shape(len(images))
    tile_w = max(16, width // columns)
    tile_h = max(16, height // rows)
    canvas = Image.new("RGB", (tile_w * columns, tile_h * rows), "white")
    for index, image in enumerate(images):
        converted = image.convert("RGB")
        fitted = ImageOps.contain(converted, (tile_w, tile_h), method=Image.Resampling.LANCZOS)
        x = (index % columns) * tile_w + (tile_w - fitted.width) // 2
        y = (index // columns) * tile_h + (tile_h - fitted.height) // 2
        canvas.paste(fitted, (x, y))
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
    return output.getvalue()


def _bounded_grid_bytes(images, Image, ImageOps, target_bytes: int) -> bytes:
    """Encode one grid below Yali's single-file limit with a safety margin."""
    width = height = YALIAI_MAX_GRID_EDGE
    for _ in range(8):
        for quality in (90, 82, 74, 66, 58):
            encoded = _encode_grid(images, Image, ImageOps, width, height, quality)
            if len(encoded) <= target_bytes:
                return encoded
        width = max(768, int(width * 0.82))
        height = max(768, int(height * 0.82))
    raise ReferencePackError(
        "亚立宫格参考图仍超过单张 12 MiB 限制；请减少参考图数量或降低原图分辨率。"
    )


def pack_yaliai_references(
    image_paths: Iterable[str], output_dir: Path
) -> tuple[list[str], dict[str, object]]:
    """Pack more-than-six (or oversized) references into <=6 JPEG grids.

    Every source is letterboxed into a grid tile; no source pixels are cropped.
    The returned paths are temporary request inputs and the caller still makes
    exactly one upstream generation request.
    """
    paths = [Path(value).expanduser().resolve() for value in image_paths]
    if not paths:
        return [], {"enabled": False, "packed": False}
    try:
        source_bytes = [path.stat().st_size for path in paths]
    except OSError as exc:
        raise ReferencePackError(f"无法读取亚立参考图大小: {exc}") from exc
    needs_pack = (
        len(paths) > YALIAI_MAX_REFERENCES
        or any(size > YALIAI_MAX_SINGLE_BYTES for size in source_bytes)
        or sum(source_bytes) > YALIAI_MAX_TOTAL_BYTES
    )
    if not needs_pack and len(paths) <= YALIAI_MAX_REFERENCES:
        return [str(path) for path in paths], {
            "enabled": True,
            "packed": False,
            "source_count": len(paths),
            "request_count": len(paths),
            "source_bytes": sum(source_bytes),
        }

    Image, ImageOps = _load_pillow()
    loaded = []
    for path in paths:
        try:
            image = Image.open(path)
            image.load()
            loaded.append(image.copy())
            image.close()
        except Exception as exc:  # Pillow raises several format-specific errors
            for opened in loaded:
                opened.close()
            raise ReferencePackError(f"无法读取亚立参考图 {path.name}: {exc}") from exc

    output_dir = output_dir.expanduser().resolve() / ".reference-packs"
    output_dir.mkdir(parents=True, exist_ok=True)
    packed_paths: list[str] = []
    try:
        for group_start in range(0, len(loaded), YALIAI_MAX_REFERENCES):
            group = loaded[group_start : group_start + YALIAI_MAX_REFERENCES]
            group_count = math.ceil(len(loaded) / YALIAI_MAX_REFERENCES)
            target_bytes = min(
                YALIAI_MAX_SINGLE_BYTES - 512 * 1024,
                max(1 * 1024 * 1024, (YALIAI_MAX_TOTAL_BYTES - 1 * 1024 * 1024) // group_count),
            )
            data = _bounded_grid_bytes(group, Image, ImageOps, target_bytes)
            path = output_dir / f"yaliai-grid-{group_start // YALIAI_MAX_REFERENCES + 1}.jpg"
            temp = path.with_suffix(path.suffix + ".part")
            temp.write_bytes(data)
            temp.replace(path)
            packed_paths.append(str(path))
    finally:
        for image in loaded:
            try:
                image.close()
            except Exception:
                pass

    packed_bytes = [Path(path).stat().st_size for path in packed_paths]
    if len(packed_paths) > YALIAI_MAX_REFERENCES or sum(packed_bytes) > YALIAI_MAX_TOTAL_BYTES:
        raise ReferencePackError(
            "亚立宫格参考图总量超过 30 MiB；请减少参考图数量或使用更小的原图。"
        )
    return packed_paths, {
        "enabled": True,
        "packed": True,
        "source_count": len(paths),
        "request_count": len(packed_paths),
        "source_bytes": sum(source_bytes),
        "packed_bytes": sum(packed_bytes),
        "group_size": YALIAI_MAX_REFERENCES,
        "single_limit_bytes": YALIAI_MAX_SINGLE_BYTES,
        "total_limit_bytes": YALIAI_MAX_TOTAL_BYTES,
        "preserved_aspect": True,
        "cropped": False,
    }
