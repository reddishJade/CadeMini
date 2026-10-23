"""识别图片类型，并按尺寸和编码上限生成模型附件。"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from cade.harness.tools import ToolResult


def read_image(data: bytes) -> ToolResult | None:
    mime = detect_image_type(data[:4100])
    if mime is None:
        return None
    return _process_image(data, mime)


def detect_image_type(data: bytes) -> str | None:
    mime: str | None = None
    if (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        and len(data) >= 16
        and int.from_bytes(data[8:12], "big") == 13
        and data[12:16] == b"IHDR"
    ):
        offset = 8
        while offset + 8 <= len(data):
            if data[offset + 4 : offset + 8] == b"acTL":
                return None
            if data[offset + 4 : offset + 8] == b"IDAT":
                break
            following = offset + 12 + int.from_bytes(data[offset : offset + 4], "big")
            if following <= offset or following > len(data):
                break
            offset = following
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = None if len(data) > 3 and data[3] == 0xF7 else "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    elif data.startswith(b"BM") and len(data) >= 26:
        size = int.from_bytes(data[2:6], "little")
        offset = int.from_bytes(data[10:14], "little")
        header = int.from_bytes(data[14:18], "little")
        if (size and size < 26) or offset < 14 + header or (size and offset >= size):
            return None
        if header == 12:
            planes, bits = (
                int.from_bytes(data[22:24], "little"),
                int.from_bytes(data[24:26], "little"),
            )
        elif 40 <= header <= 124 and len(data) >= 30:
            planes, bits = (
                int.from_bytes(data[26:28], "little"),
                int.from_bytes(data[28:30], "little"),
            )
        else:
            return None
        if planes == 1 and bits in {1, 4, 8, 16, 24, 32}:
            mime = "image/bmp"
    return mime


def _process_image(data: bytes, mime: str) -> ToolResult:
    converted = mime == "image/bmp"
    try:
        with Image.open(io.BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original)
            width, height = image.size
            hints: list[str] = []
            if converted:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
                hints.append("[Image converted from image/bmp to image/png.]")
                mime = "image/png"
            encoded = base64.b64encode(data).decode("ascii")
            if width > 2000 or height > 2000 or len(encoded) >= 4.5 * 1024 * 1024:
                target_width, target_height = width, height
                if target_width > 2000:
                    target_height = int(target_height * 2000 / target_width + 0.5)
                    target_width = 2000
                if target_height > 2000:
                    target_width = int(target_width * 2000 / target_height + 0.5)
                    target_height = 2000
                while True:
                    resized = image.resize(
                        (max(1, target_width), max(1, target_height)),
                        Image.Resampling.LANCZOS,
                    )
                    candidates = [
                        ("PNG", 80),
                        *(("JPEG", quality) for quality in (80, 85, 70, 55, 40)),
                    ]
                    for format_name, quality in candidates:
                        buffer = io.BytesIO()
                        sample = (
                            resized.convert("RGB") if format_name == "JPEG" else resized
                        )
                        sample.save(buffer, format=format_name, quality=quality)
                        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                        if len(encoded) < 4.5 * 1024 * 1024:
                            mime = "image/png" if format_name == "PNG" else "image/jpeg"
                            break
                    else:
                        if target_width == target_height == 1:
                            return ToolResult(
                                f"Read image file [{mime}]\n[Image omitted: could not be resized below the inline image size limit.]"
                            )
                        target_width, target_height = (
                            max(1, int(target_width * 0.75)),
                            max(1, int(target_height * 0.75)),
                        )
                        continue
                    break
                hints.append(
                    f"[Image: original {width}x{height}, displayed at {target_width}x{target_height}. Multiply coordinates by {width / target_width:.2f} to map to original image.]"
                )
    except (OSError, ValueError, UnidentifiedImageError):
        return ToolResult(
            f"Read image file [{mime}]\n[Image omitted: could not be resized below the inline image size limit.]"
        )
    text = f"Read image file [{mime}]"
    if hints:
        text += "\n" + "\n".join(hints)
    return ToolResult(text, metadata={"images": [{"data": encoded, "mimeType": mime}]})
