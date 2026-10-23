"""用 Pillow 读取图片，并限制模型附件的尺寸和大小。"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from cade.harness.tools import ToolResult

_MAX_INLINE_BYTES = 3_300_000


def read_image(data: bytes) -> ToolResult | None:
    try:
        original = Image.open(io.BytesIO(data))
    except UnidentifiedImageError:
        return None
    with original:
        if original.format not in {"PNG", "JPEG", "GIF", "WEBP", "BMP"}:
            raise ValueError(f"Unsupported image format: {original.format}")
        mime = Image.MIME[original.format]
        width, height = original.size
        if (
            max(width, height) > 2000
            or len(data) > _MAX_INLINE_BYTES
            or mime == "image/bmp"
        ):
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((2000, 2000))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
            data, mime = buffer.getvalue(), "image/jpeg"
        if len(data) > _MAX_INLINE_BYTES:
            raise ValueError("Image exceeds the inline size limit after resizing")
        return ToolResult(
            f"Read image file [{mime}]",
            metadata={
                "images": [
                    {"data": base64.b64encode(data).decode("ascii"), "mimeType": mime}
                ]
            },
        )
