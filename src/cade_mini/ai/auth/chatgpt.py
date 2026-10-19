"""Sign in with ChatGPT (SIWC) OAuth 授权实现。

使用 OAuth 2.0 Authorization Code + PKCE，动态注册客户端，并用 ChatGPT
套餐额度调用公开的 Responses API。首次登录注册客户端；后续登录复用已签发的
client_id，并携带 id_token_hint 与 login_hint。
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from typing import Any, cast

import httpx
import jwt
from jwt import PyJWKClient

from .types import AuthCredential, LoginCancelledError

_LOGGER = logging.getLogger(__name__)

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "Cade Mini"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
CALLBACK_PATH = "/auth/callback"
DEFAULT_CALLBACK_PORT = 1455
CALLBACK_HOST = "127.0.0.1"

_JWK_CLIENT: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    """返回带缓存的 JWKS 客户端。"""
    global _JWK_CLIENT
    if _JWK_CLIENT is None:
        _JWK_CLIENT = PyJWKClient(JWKS_URL, cache_keys=True)
    return _JWK_CLIENT


def generate_pkce() -> tuple[str, str]:
    """生成 PKCE verifier 与 S256 challenge。"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def callback_uri(port: int) -> str:
    """构造与授权请求完全一致的 loopback 回调地址。"""
    return f"http://{CALLBACK_HOST}:{port}{CALLBACK_PATH}"


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    challenge: str,
    host_id: str,
    existing: AuthCredential | None = None,
) -> str:
    """构造 SIWC 授权地址。

    首次注册使用 dynamic_agent_client 并附带 agent_name_hint；后续登录使用
    已签发的 client_id，并携带保留的 id_token 与 email 提示。
    """
    params = {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if existing is not None:
        extra = existing.extra or {}
        id_token = extra.get("id_token")
        email = extra.get("email")
        if isinstance(id_token, str) and id_token:
            params["id_token_hint"] = id_token
        if isinstance(email, str) and email:
            params["login_hint"] = email
    else:
        params["agent_name_hint"] = AGENT_NAME
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """接收 SIWC 授权回调的临时服务器。"""

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        cb_server = cast(_OAuthCallbackServer, self.server)
        query = urllib.parse.parse_qs(parsed_url.query)
        cb_server.received_code = query.get("code", [None])[0]
        cb_server.received_state = query.get("state", [None])[0]
        cb_server.received_error = query.get("error", [None])[0]
        cb_server.received_error_description = query.get("error_description", [None])[0]
        cb_server.received_client_id = query.get("client_id", [None])[0]
        cb_server.received_scope = query.get("scope", [None])[0]

        failed = (
            not cb_server.received_code
            or bool(cb_server.received_error)
            or (
                cb_server.expected_state is not None
                and cb_server.received_state != cb_server.expected_state
            )
        )
        if failed:
            self.send_response(400)
            title = "Cade Mini authorization failed"
            message = (
                "The sign-in could not be completed. Return to the terminal for "
                "details and try again."
            )
        else:
            self.send_response(200)
            title = "Cade Mini authorization successful"
            message = (
                "Connected to your ChatGPT account. You can close this browser "
                "window and return to the terminal."
            )
        self.send_header("Content-Type", "text/html; charset=utf-8")
        body = f"""
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2>{title}</h2>
            <p>{message}</p>
        </body>
        </html>
        """.encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _OAuthCallbackServer(http.server.HTTPServer):
    received_code: str | None = None
    received_state: str | None = None
    received_error: str | None = None
    received_error_description: str | None = None
    received_client_id: str | None = None
    received_scope: str | None = None
    expected_state: str | None = None


def _start_callback_server() -> tuple[_OAuthCallbackServer, int]:
    """优先使用固定端口；被占用时改用系统分配的 loopback 端口。"""
    try:
        server = _OAuthCallbackServer(
            (CALLBACK_HOST, DEFAULT_CALLBACK_PORT), _OAuthCallbackHandler
        )
    except OSError:
        server = _OAuthCallbackServer((CALLBACK_HOST, 0), _OAuthCallbackHandler)
    return server, int(server.server_address[1])


def _serve_callback(server: _OAuthCallbackServer, deadline: float) -> None:
    """处理回调请求，直到拿到授权码、出错或到达截止时间。"""
    try:
        while time.time() < deadline:
            if server.received_code or server.received_error:
                return
            server.handle_request()
    except (OSError, ValueError):
        return


def login_chatgpt_browser(
    existing: AuthCredential | None = None,
    host_id: str | None = None,
    open_browser: bool = True,
    notify_callback: Callable[[str], None] | None = None,
    timeout_seconds: int = 180,
    cancel_event: threading.Event | None = None,
) -> AuthCredential:
    """通过系统浏览器完成 SIWC 授权。

    cancel_event 被设置时关闭回调服务器并抛出 LoginCancelledError。
    """
    if not host_id:
        raise ValueError("SIWC login requires a stable host ID")

    issued_client_id = _issued_client_id(existing)
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)

    server, port = _start_callback_server()
    server.timeout = 0.5
    server.expected_state = state
    redirect_uri = callback_uri(port)
    client_id = issued_client_id or CLIENT_ID
    auth_url = build_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        challenge=challenge,
        host_id=host_id,
        existing=existing if issued_client_id else None,
    )

    if notify_callback:
        notify_callback(
            "Complete ChatGPT authorization in your browser.\n"
            f"If the browser does not open automatically, visit:\n{auth_url}"
        )

    deadline = time.time() + timeout_seconds
    server_thread = threading.Thread(
        target=_serve_callback, args=(server, deadline), daemon=True
    )
    server_thread.start()

    if open_browser:
        webbrowser.open(auth_url)

    while server_thread.is_alive():
        if _wait_cancelled(cancel_event, 0.2):
            server.server_close()
            raise LoginCancelledError("ChatGPT browser authorization cancelled.")
        if time.time() > deadline:
            server.server_close()
            raise TimeoutError("ChatGPT browser authorization timed out; please retry.")

    server.server_close()
    if (
        time.time() > deadline
        and not server.received_code
        and not server.received_error
    ):
        raise TimeoutError("ChatGPT browser authorization timed out; please retry.")

    if server.received_state != state:
        raise RuntimeError("OAuth state mismatch; a CSRF attack may be in progress.")
    if server.received_error:
        detail = server.received_error
        if server.received_error_description:
            detail = f"{detail}: {server.received_error_description}"
        raise RuntimeError(f"ChatGPT authorization failed: {detail}")
    if not server.received_code:
        raise RuntimeError("Could not obtain an authorization code from the callback.")

    if issued_client_id is None:
        returned_client_id = server.received_client_id
        if not returned_client_id or returned_client_id == CLIENT_ID:
            raise RuntimeError(
                "ChatGPT did not issue a client ID; the registration is incomplete."
            )
        issued_client_id = returned_client_id
    elif server.received_client_id and server.received_client_id != issued_client_id:
        raise RuntimeError(
            "ChatGPT returned a different client ID; refusing to replace the saved "
            "registration."
        )

    token = exchange_code_for_token(
        server.received_code,
        verifier,
        redirect_uri,
        client_id=issued_client_id,
    )
    return _credential_from_token(
        token,
        client_id=issued_client_id,
        nonce=nonce,
        fallback_scope=server.received_scope,
        previous=existing,
    )


def _wait_cancelled(cancel_event: threading.Event | None, seconds: float) -> bool:
    """等待一个轮询间隔；期间被取消时立即返回 True。"""
    if cancel_event is None:
        time.sleep(seconds)
        return False
    return cancel_event.wait(seconds)


def _json_object(response: httpx.Response, action: str) -> dict[str, Any]:
    """解析 Token 端点响应，拒绝非对象载荷。"""
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{action} returned invalid JSON") from exc
    if not isinstance(data, dict):
        # 登录入口统一把协议错误当作 RuntimeError 处理。
        raise RuntimeError(f"{action} returned an unexpected payload")  # noqa: TRY004
    return data


def _token_error_detail(response: httpx.Response) -> str:
    """提取 Token 端点的错误码，避免把响应正文写入日志或终端。"""
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(data, dict):
        parts = [
            str(data[key])
            for key in ("error", "error_description", "code", "message")
            if isinstance(data.get(key), str) and data[key]
        ]
        if parts:
            return f"HTTP {response.status_code}: {' - '.join(parts)}"
    return f"HTTP {response.status_code}"


def exchange_code_for_token(
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    client_id: str,
) -> dict[str, Any]:
    """使用授权码与 PKCE verifier 换取 Token。"""
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "resource": RESOURCE,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TOKEN_URL, data=data)
        if resp.status_code != 200:
            raise RuntimeError(
                "Failed to exchange the authorization code for tokens "
                f"({_token_error_detail(resp)})"
            )
        return _json_object(resp, "The token exchange")


def refresh_chatgpt_token(
    refresh_token: str,
    *,
    client_id: str,
    previous: AuthCredential | None = None,
) -> AuthCredential:
    """使用 refresh_token 刷新访问令牌。"""
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
        "resource": RESOURCE,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TOKEN_URL, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Token refresh failed ({_token_error_detail(resp)})")
        token_data = _json_object(resp, "The token refresh")
        if "refresh_token" not in token_data:
            token_data["refresh_token"] = refresh_token
        return _credential_from_token(
            token_data,
            client_id=client_id,
            nonce=None,
            previous=previous,
        )


def validate_id_token(
    id_token: str,
    *,
    client_id: str,
    nonce: str | None,
) -> dict[str, Any]:
    """校验 ID Token 的签名、签发方、受众、有效期与 nonce。"""
    try:
        signing_key = _jwks().get_signing_key_from_jwt(id_token).key
    except jwt.PyJWTError as exc:
        raise RuntimeError(f"Could not load the ChatGPT signing key: {exc}") from exc
    try:
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise RuntimeError(f"Could not validate the ChatGPT ID token: {exc}") from exc
    if nonce is not None and claims.get("nonce") != nonce:
        raise RuntimeError(
            "The ID token nonce did not match the authorization request."
        )
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise RuntimeError("The ID token did not contain a subject.")
    return claims


def _credential_from_token(
    data: dict[str, Any],
    *,
    client_id: str,
    nonce: str | None,
    fallback_scope: str | None = None,
    previous: AuthCredential | None = None,
) -> AuthCredential:
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("The token response did not contain an access token.")

    scope = data.get("scope") or fallback_scope
    previous_extra = (previous.extra or {}) if previous is not None else {}
    if not scope:
        retained_scopes = previous_extra.get("scopes")
        scopes = (
            [str(item) for item in retained_scopes]
            if isinstance(retained_scopes, list)
            else []
        )
    else:
        scopes = scope.split() if isinstance(scope, str) else []
    if PLAN_SCOPE not in scopes:
        raise RuntimeError(
            "ChatGPT plan usage was not authorized. Re-run login and grant "
            f"'{PLAN_SCOPE}', or use an OpenAI API key."
        )

    token_id_token = data.get("id_token")
    id_token = token_id_token
    if not isinstance(id_token, str) or not id_token:
        retained = previous_extra.get("id_token")
        id_token = retained if isinstance(retained, str) else ""

    claims: dict[str, Any] = {}
    if isinstance(token_id_token, str) and token_id_token:
        claims = validate_id_token(token_id_token, client_id=client_id, nonce=nonce)
        previous_subject = previous_extra.get("subject")
        if (
            isinstance(previous_subject, str)
            and previous_subject
            and claims["sub"] != previous_subject
        ):
            raise RuntimeError(
                "The ChatGPT account identity changed during reauthorization; "
                "refusing to replace the saved credentials."
            )

    expires_in = data.get("expires_in")
    if expires_in:
        expires = int(time.time()) + int(expires_in)
    elif isinstance(claims.get("exp"), int):
        expires = int(claims["exp"])
    else:
        expires = int(time.time()) + 3600

    subject = claims.get("sub") or previous_extra.get("subject")
    email = claims.get("email") or previous_extra.get("email")
    return AuthCredential(
        type="oauth",
        access=access_token,
        refresh=data.get("refresh_token"),
        expires=expires,
        provider="chatgpt",
        extra={
            "client_id": client_id,
            "id_token": id_token,
            "subject": subject,
            "email": email,
            "scopes": scopes,
            "issuer": ISSUER,
        },
    )


def _issued_client_id(existing: AuthCredential | None) -> str | None:
    """读取已签发的 client_id；忽略首次注册占位符。"""
    if existing is None:
        return None
    value = (existing.extra or {}).get("client_id")
    if isinstance(value, str) and value and value != CLIENT_ID:
        return value
    return None


class ChatGPTAuthProvider:
    """ChatGPT 登录、Token 交换与刷新实现。"""

    id = "chatgpt"

    def login(
        self,
        *,
        method: str,
        notify_callback: Callable[..., None] | None = None,
        cancel_event: threading.Event | None = None,
        existing: AuthCredential | None = None,
        host_id: str | None = None,
    ) -> AuthCredential:
        """执行 ChatGPT 浏览器授权登录。"""
        if method != "browser":
            raise ValueError(f"Unsupported ChatGPT login method: {method}")
        return login_chatgpt_browser(
            existing=existing,
            host_id=host_id,
            notify_callback=notify_callback,
            cancel_event=cancel_event,
        )

    def refresh(self, credential: AuthCredential) -> AuthCredential:
        """使用已存凭据刷新 ChatGPT Access Token。"""
        if not credential.refresh:
            raise ValueError("ChatGPT credentials are missing a refresh token")
        client_id = _issued_client_id(credential)
        if client_id is None:
            raise ValueError("ChatGPT credentials are missing an issued client ID")
        return refresh_chatgpt_token(
            credential.refresh,
            client_id=client_id,
            previous=credential,
        )
