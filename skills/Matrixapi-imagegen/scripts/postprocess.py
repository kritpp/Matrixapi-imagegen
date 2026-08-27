#!/usr/bin/env python3
"""Deterministic local image post-processing for Matrixapi-imagegen.

This module intentionally contains no ML dependency.  It is used for exact
resizing, cropping, canvas fitting, format conversion, and compression after
an upstream image request has completed.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any


MAX_OUTPUT_EDGE = 16_384
MAX_OUTPUT_PIXELS = 100_000_000
FORMATS = {"same", "png", "jpeg", "jpg", "webp", "avif"}
FITS = {"cover", "contain", "fill", "inside", "outside"}
POSITIONS = {
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
}


class PostprocessError(RuntimeError):
    pass


def _pil():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - depends on target machine
        raise PostprocessError(
            "本地图片处理需要 Pillow；请安装 pillow，或省略本地后处理参数"
        ) from exc
    return Image, ImageOps


def parse_output_size(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*([1-9]\d*)\s*x\s*([1-9]\d*)\s*", value or "")
    if not match:
        raise PostprocessError("本地输出尺寸必须使用 WIDTHxHEIGHT，例如 1920x1080")
    width, height = int(match.group(1)), int(match.group(2))
    if width > MAX_OUTPUT_EDGE or height > MAX_OUTPUT_EDGE:
        raise PostprocessError(f"本地输出边长不能超过 {MAX_OUTPUT_EDGE}px")
    if width * height > MAX_OUTPUT_PIXELS:
        raise PostprocessError(
            f"本地输出像素不能超过 {MAX_OUTPUT_PIXELS:,}；请降低尺寸"
        )
    return width, height


def parse_crop(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"\s*([+-]?\d+)\s*,\s*([+-]?\d+)\s*,\s*([1-9]\d*)\s*,\s*([1-9]\d*)\s*", value or "")
    if not match:
        raise PostprocessError("裁剪区域必须使用 x,y,width,height，例如 100,0,1920,1080")
    x, y, width, height = (int(part) for part in match.groups())
    if x < 0 or y < 0:
        raise PostprocessError("裁剪坐标不能为负数")
    return x, y, width, height


def parse_text_box(value: str) -> tuple[int, int, int, int]:
    try:
        return parse_crop(value)
    except PostprocessError as exc:
        raise PostprocessError(
            "文字区域必须使用 x,y,width,height，例如 120,80,600,160"
        ) from exc


def parse_color(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"#?([0-9a-fA-F]{3,8})", value.strip())
    if not match or len(match.group(1)) not in {3, 4, 6, 8}:
        raise PostprocessError("背景色必须使用 #RGB、#RGBA、#RRGGBB 或 #RRGGBBAA")
    raw = match.group(1)
    if len(raw) in {3, 4}:
        raw = "".join(char * 2 for char in raw)
    if len(raw) == 6:
        raw += "FF"
    return tuple(int(raw[index : index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]


def _position_offset(
    container: tuple[int, int], item: tuple[int, int], position: str, *, crop: bool = False
) -> tuple[int, int]:
    cw, ch = container
    iw, ih = item
    dx, dy = (max(0, iw - cw), max(0, ih - ch)) if crop else (max(0, cw - iw), max(0, ch - ih))
    horizontal = "center"
    vertical = "center"
    if "left" in position:
        horizontal = "left"
    elif "right" in position:
        horizontal = "right"
    if position == "top":
        vertical = "top"
    elif position == "bottom":
        vertical = "bottom"
    elif "top" in position:
        vertical = "top"
    elif "bottom" in position:
        vertical = "bottom"
    x = 0 if horizontal == "left" else dx if horizontal == "right" else dx // 2
    y = 0 if vertical == "top" else dy if vertical == "bottom" else dy // 2
    return x, y


def _background(mode: str, color: tuple[int, int, int, int] | None):
    if color is not None:
        return color
    if mode in {"png", "webp", "avif"}:
        return (0, 0, 0, 0)
    return (255, 255, 255, 255)


def _format_for(source: Path, requested: str) -> str:
    if requested != "same":
        normalized = requested.lower()
        return "jpeg" if normalized == "jpg" else normalized
    suffix = source.suffix.lower().lstrip(".")
    return "jpeg" if suffix in {"jpg", "jpeg"} else suffix or "png"


def _extension(fmt: str) -> str:
    return ".jpg" if fmt == "jpeg" else f".{fmt}"


def _save(image: Any, path: Path, fmt: str, quality: int, background: tuple[int, int, int, int] | None) -> None:
    Image, _ = _pil()
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jpeg":
        canvas = Image.new("RGB", image.size, (background or (255, 255, 255, 255))[:3])
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            canvas.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
        else:
            canvas.paste(image.convert("RGB"))
        image = canvas
        image.save(path, format="JPEG", quality=quality, subsampling=0, optimize=True, progressive=True)
    elif fmt == "png":
        image.save(path, format="PNG", optimize=True)
    elif fmt == "webp":
        image.save(path, format="WEBP", quality=quality, method=6)
    elif fmt == "avif":
        try:
            image.save(path, format="AVIF", quality=quality)
        except (KeyError, OSError) as exc:
            raise PostprocessError("当前 Pillow 环境不支持 AVIF 输出") from exc
    else:
        raise PostprocessError(f"不支持本地输出格式: {fmt}")


def process_image(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    output_size: str | None = None,
    fit: str = "cover",
    position: str = "center",
    crop: str | None = None,
    output_format: str = "same",
    quality: int = 90,
    background_color: str | None = None,
) -> dict[str, Any]:
    Image, ImageOps = _pil()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise PostprocessError(f"本地输入图片不存在: {source}")
    if fit not in FITS:
        raise PostprocessError(f"--fit 必须是: {', '.join(sorted(FITS))}")
    if position not in POSITIONS:
        raise PostprocessError(f"--position 必须是: {', '.join(sorted(POSITIONS))}")
    if output_format not in FORMATS:
        raise PostprocessError(f"--output-format 必须是: {', '.join(sorted(FORMATS))}")
    if not 1 <= quality <= 100:
        raise PostprocessError("--output-quality 必须在 1-100 之间")
    target = parse_output_size(output_size) if output_size else None
    crop_box = parse_crop(crop) if crop else None
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        source_width, source_height = image.size
        if crop_box:
            x, y, width, height = crop_box
            if x + width > image.width or y + height > image.height:
                raise PostprocessError("裁剪区域超出源图片范围")
            image = image.crop((x, y, x + width, y + height))
        cropped_width, cropped_height = image.size

        if target:
            tw, th = target
            if fit == "fill":
                image = image.resize(target, Image.Resampling.LANCZOS)
            else:
                ratio = max(tw / image.width, th / image.height) if fit in {"cover", "outside"} else min(tw / image.width, th / image.height)
                if fit == "inside":
                    ratio = min(1.0, ratio)
                resized = image.resize(
                    (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
                if fit == "cover":
                    ox, oy = _position_offset(target, resized.size, position, crop=True)
                    image = resized.crop((ox, oy, ox + tw, oy + th))
                elif fit == "contain":
                    canvas = Image.new("RGBA", target, _background("png", parse_color(background_color)))
                    ox, oy = _position_offset(target, resized.size, position)
                    canvas.alpha_composite(resized, (ox, oy))
                    image = canvas
                else:
                    image = resized

        fmt = _format_for(source, output_format)
        if fmt not in {"png", "jpeg", "webp", "avif"}:
            raise PostprocessError(
                f"源图片格式 {fmt} 不能原样输出；请显式指定 --output-format png/jpeg/webp/avif"
            )
        suffix = []
        if target:
            suffix.append(f"w{target[0]}_h{target[1]}")
            suffix.append(fit)
        if crop_box:
            suffix.append("crop")
        if not suffix:
            suffix.append("convert")
        stem = source.stem + "_" + "_".join(suffix)
        if fmt != "png":
            stem += f"_{fmt}-q{quality}"
        output = Path(output_dir).expanduser().resolve() / f"{stem}{_extension(fmt)}"
        _save(image, output, fmt, quality, parse_color(background_color))

    return {
        "source": str(source),
        "output": str(output),
        "source_width": source_width,
        "source_height": source_height,
        "cropped_width": cropped_width,
        "cropped_height": cropped_height,
        "output_width": image.width,
        "output_height": image.height,
        "fit": fit,
        "position": position,
        "crop": crop_box,
        "format": fmt,
        "quality": quality,
        "source_bytes": source.stat().st_size,
        "output_bytes": output.stat().st_size,
    }


def process_many(paths: list[str], output_dir: str | Path, **kwargs: Any) -> list[dict[str, Any]]:
    try:
        results = [process_image(path, output_dir, **kwargs) for path in paths]
    except PostprocessError:
        raise
    except (OSError, ValueError) as exc:
        raise PostprocessError(f"本地图片处理失败: {exc}") from exc
    manifest_path = Path(output_dir).expanduser().resolve() / "postprocess-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"tool": "Matrixapi-imagegen-local-postprocess", "items": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results


def _text_font(font_path: str | None, size: int):
    try:
        from PIL import ImageFont
    except ImportError as exc:  # pragma: no cover
        raise PostprocessError("本地精准文字处理需要 Pillow") from exc
    candidates = [
        Path(font_path).expanduser() if font_path else None,
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    raise PostprocessError("找不到可用字体；请用 --font-path 指定支持目标文字的字体文件")


def precise_text_edit(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    text: str,
    text_box: str,
    font_path: str | None = None,
    font_size: int | None = None,
    text_color: str | None = None,
    background_color: str | None = None,
) -> dict[str, Any]:
    """Replace a known text region deterministically without an image API call."""
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:  # pragma: no cover
        raise PostprocessError("本地精准文字处理需要 Pillow") from exc
    if not text:
        raise PostprocessError("--new-text 不能为空")
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise PostprocessError(f"本地输入图片不存在: {source}")
    x, y, width, height = parse_text_box(text_box)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        if x + width > image.width or y + height > image.height:
            raise PostprocessError("文字区域超出源图片范围")

        explicit_background = parse_color(background_color)
        if explicit_background is None:
            border: list[tuple[int, int, int, int]] = []
            left, top, right, bottom = x, y, x + width, y + height
            for px in range(left, right):
                border.append(image.getpixel((px, top)))
                border.append(image.getpixel((px, bottom - 1)))
            for py in range(top, bottom):
                border.append(image.getpixel((left, py)))
                border.append(image.getpixel((right - 1, py)))
            fill = tuple(int(statistics.median(pixel[channel] for pixel in border)) for channel in range(4))
        else:
            fill = explicit_background

        explicit_text = parse_color(text_color)
        if explicit_text is None:
            luminance = 0.2126 * fill[0] + 0.7152 * fill[1] + 0.0722 * fill[2]
            ink = (20, 20, 20, 255) if luminance >= 150 else (245, 245, 245, 255)
        else:
            ink = explicit_text

        draw = ImageDraw.Draw(image)
        draw.rectangle((x, y, x + width - 1, y + height - 1), fill=fill)
        chosen_size = font_size or max(12, int(height * 0.68))
        while chosen_size >= 8:
            font = _text_font(font_path, chosen_size)
            box = draw.textbbox((0, 0), text, font=font)
            text_width, text_height = box[2] - box[0], box[3] - box[1]
            if text_width <= width * 0.94 and text_height <= height * 0.9:
                break
            chosen_size -= 1
        else:
            raise PostprocessError("目标文字无法放入指定区域；请扩大 --text-box")
        tx = x + (width - text_width) / 2 - box[0]
        ty = y + (height - text_height) / 2 - box[1]
        draw.text((tx, ty), text, font=font, fill=ink)

        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        output = destination / f"{source.stem}_precise-text.png"
        image.save(output, format="PNG", optimize=True)

    return {
        "source": str(source),
        "output": str(output),
        "text": text,
        "text_box": [x, y, width, height],
        "font_size": chosen_size,
        "text_color": list(ink),
        "background_color": list(fill),
        "api_called": False,
    }
