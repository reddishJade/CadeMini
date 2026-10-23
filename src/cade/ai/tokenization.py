"""加载固定版本的官方分词器，只缓存公开模型文件。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import httpx
from tokenizers import Tokenizer

_DEEPSEEK_URL = "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"
_DEEPSEEK_MEMBER = "deepseek_v4_tokenizer/tokenizer.json"
_DEEPSEEK_SHA256 = "89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614"


@dataclass(frozen=True)
class TokenCounter:
    label: str
    count: Callable[[str], int]


def _verify(data: bytes) -> None:
    if hashlib.sha256(data).hexdigest() != _DEEPSEEK_SHA256:
        raise ValueError("DeepSeek V4 tokenizer checksum mismatch")


@lru_cache(maxsize=4)
def _load(path: Path) -> TokenCounter:
    data = path.read_bytes()
    _verify(data)
    tokenizer = Tokenizer.from_str(data.decode("utf-8"))

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    return TokenCounter("DeepSeek V4 tokenizer", count)


async def load_deepseek_v4_tokenizer() -> TokenCounter:
    """首次下载官方 V4 词表，后续从用户缓存加载。"""
    path = (
        Path.home()
        / ".cade"
        / "tokenizers"
        / f"deepseek-v4-{_DEEPSEEK_SHA256[:12]}.json"
    )
    if not path.is_file():
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(_DEEPSEEK_URL)
            response.raise_for_status()
        try:
            with ZipFile(BytesIO(response.content)) as archive:
                data = archive.read(_DEEPSEEK_MEMBER)
        except (BadZipFile, KeyError) as exc:
            raise ValueError("Invalid DeepSeek tokenizer archive") from exc
        _verify(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tokenizer-")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return await asyncio.to_thread(_load, path)
