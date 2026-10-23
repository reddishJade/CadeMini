"""优先使用本地工具，允许联网时下载缺失的程序。"""

from __future__ import annotations

import io
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import httpx


def bin_directory() -> Path:
    return Path.home() / ".cade" / "bin"


def ensure_tool(tool: str, *, allow_download: bool = True) -> str | None:
    binary_name = tool + (".exe" if sys.platform == "win32" else "")
    cached = bin_directory() / binary_name
    if cached.is_file():
        return str(cached)
    existing = shutil.which(tool) or (shutil.which("fdfind") if tool == "fd" else None)
    if existing:
        return existing
    if not allow_download or os.environ.get("CADE_OFFLINE", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    repo = "sharkdp/fd" if tool == "fd" else "BurntSushi/ripgrep"
    prefix = "fd-v" if tool == "fd" else "ripgrep-"
    tag_prefix = "v" if tool == "fd" else ""
    architecture = (
        "aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
    )
    target = {
        "win32": "pc-windows-msvc",
        "darwin": "apple-darwin",
        "linux": "unknown-linux-musl",
    }.get(sys.platform)
    if target is None or sys.platform == "android":
        return None
    extension = ".zip" if sys.platform == "win32" else ".tar.gz"
    try:
        with httpx.Client(
            headers={"User-Agent": "cade-coding-agent"}, follow_redirects=True
        ) as client:
            if tool == "fd" and sys.platform == "darwin" and architecture == "x86_64":
                version = "10.3.0"
            else:
                release = client.get(
                    f"https://github.com/{repo}/releases/latest",
                    follow_redirects=False,
                    timeout=10,
                )
                location = release.headers.get("location", "")
                if (
                    release.status_code not in range(300, 400)
                    or "/releases/tag/" not in location
                ):
                    return None
                version = location.rsplit("/", 1)[-1].removeprefix("v")
            asset = f"{prefix}{version}-{architecture}-{target}{extension}"
            response = client.get(
                f"https://github.com/{repo}/releases/download/{tag_prefix}{version}/{asset}",
                timeout=120,
            )
            response.raise_for_status()
        archive = io.BytesIO(response.content)
        if extension == ".zip":
            with zipfile.ZipFile(archive) as zipped:
                member = next(
                    (
                        name
                        for name in zipped.namelist()
                        if name.rsplit("/", 1)[-1] == binary_name
                    ),
                    None,
                )
                if member is None:
                    return None
                data = zipped.read(member)
        else:
            with tarfile.open(fileobj=archive, mode="r:gz") as tar:
                member = next(
                    (
                        item
                        for item in tar.getmembers()
                        if item.isfile() and item.name.rsplit("/", 1)[-1] == binary_name
                    ),
                    None,
                )
                if member is None:
                    return None
                stream = tar.extractfile(member)
                if stream is None:
                    return None
                with stream:
                    data = stream.read()
        # 只取目标可执行文件，不解压归档中的目录或其他文件。
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        if sys.platform != "win32":
            cached.chmod(0o755)
        return str(cached)
    except (httpx.HTTPError, OSError, tarfile.TarError, zipfile.BadZipFile):
        return None
