"""命令行登录：在 Mini 自己的凭据存储中保存认证。"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence

import httpx

from cade_mini.ai.auth import AuthCredential, LoginCancelledError, get_auth_provider
from cade_mini.app.auth import JsonCredentialStore

_PROVIDERS = ("openai-codex", "openai", "deepseek", "chatglm", "mimo", "custom")


def login(argv: Sequence[str]) -> int:
    """解析登录参数；认证成功后才写入凭据文件。"""
    parser = argparse.ArgumentParser(
        prog="cade-mini login",
        description="Save credentials to ~/.cade-mini/auth.json.",
    )
    parser.add_argument("--provider", choices=_PROVIDERS, default="openai-codex")
    parser.add_argument("--method", choices=("browser", "device_code", "api_key"))
    args = parser.parse_args(argv)
    method = args.method or (
        "browser" if args.provider == "openai-codex" else "api_key"
    )
    if (args.provider == "openai-codex") == (method == "api_key"):
        parser.error("openai-codex requires OAuth; other providers require api_key")
    try:
        if method == "api_key":
            key = getpass.getpass(f"API key for {args.provider}: ").strip()
            if not key:
                raise ValueError("API key must not be empty")
            credential = AuthCredential(
                type="api_key", provider=args.provider, access=key
            )
        else:
            auth = get_auth_provider(args.provider)
            if auth is None:
                raise ValueError(f"OAuth is not supported for {args.provider}")
            credential = auth.login(method=method, notify_callback=_notify)
            if not credential.access:
                raise ValueError("Authorization returned an empty access token")
        store = JsonCredentialStore()
        store.save(credential)
    except (KeyboardInterrupt, LoginCancelledError):
        print("cade-mini: login cancelled", file=sys.stderr)
        return 130
    except (EOFError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"cade-mini: login failed: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {args.provider} credentials to {store.path}")
    return 0


def _notify(*messages: str) -> None:
    print("\n".join(messages))
