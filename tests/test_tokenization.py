"""模型分词器的下载、校验和离线缓存回归。"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from cade.ai import tokenization


def _archive() -> tuple[bytes, bytes]:
    tokenizer = Tokenizer(
        models.WordLevel({"[UNK]": 0, "Hello": 1, "你好": 2, "!": 3}, unk_token="[UNK]")
    )
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    data = tokenizer.to_str().encode("utf-8")
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr("deepseek_v4_tokenizer/tokenizer.json", data)
    return archive.getvalue(), data


async def test_tokenizer_download_is_verified_and_then_works_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, data = _archive()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        tokenization, "_DEEPSEEK_SHA256", hashlib.sha256(data).hexdigest()
    )
    requests: list[str] = []

    def download(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        assert request.url.host == "cdn.deepseek.com"
        return httpx.Response(200, content=archive)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        tokenization.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(download)),
    )
    first = await tokenization.load_deepseek_v4_tokenizer()
    assert first.count("Hello 你好!") == 3
    assert first.count("") == 0
    assert first.count("你好") == 1
    second = await tokenization.load_deepseek_v4_tokenizer()
    assert second.count("Hello!") == 2
    assert len(requests) == 1
    assert list((tmp_path / ".cade" / "tokenizers").glob("*.json"))


async def test_tokenizer_rejects_the_wrong_model_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _data = _archive()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        tokenization.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=archive)
            )
        ),
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        await tokenization.load_deepseek_v4_tokenizer()
    assert not (tmp_path / ".cade" / "tokenizers").exists()
