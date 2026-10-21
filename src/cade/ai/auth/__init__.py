"""AI Provider 认证协议、凭据类型与解析。"""

from .chatgpt import ChatGPTAuthProvider
from .registry import AUTH_PROVIDER_REGISTRY, get_auth_provider
from .resolver import CredentialResolver
from .types import AuthCredential, AuthProvider, CredentialStore, LoginCancelledError

__all__ = [
    "AUTH_PROVIDER_REGISTRY",
    "AuthCredential",
    "AuthProvider",
    "ChatGPTAuthProvider",
    "CredentialResolver",
    "CredentialStore",
    "LoginCancelledError",
    "get_auth_provider",
]
