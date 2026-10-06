import os
import re
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def secret(name: str) -> str:
    filename = os.getenv(f"{name}_FILE")
    return Path(filename).read_text().strip() if filename else os.getenv(name, "")


@dataclass(frozen=True)
class Settings:
    database_url: str
    origin: str
    oauth_mode: str
    client_id: str
    client_secret: str
    transaction_key: str
    authorize_url: str
    token_url: str
    user_url: str
    base_path: str = ""
    session_seconds: int = 28800
    transaction_seconds: int = 300
    secure_cookie: bool = True
    trusted_proxy_cidrs: tuple[str, ...] = ()

    @classmethod
    def load(cls):
        origin = os.getenv("AUTH_ORIGIN", "https://m2.invalid").rstrip("/")
        parsed = urlsplit(origin)
        insecure = os.getenv("ALLOW_INSECURE_LOOPBACK", "false") == "true"
        public_http = os.getenv("ALLOW_PUBLIC_IP_HTTP", "false") == "true"
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("Invalid authentication origin")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            public_ipv4 = address.version == 4 and address.is_global
        except ValueError:
            public_ipv4 = False
        if parsed.scheme == "http" and not ((insecure and parsed.hostname in {"127.0.0.1", "localhost", "::1"}) or (public_http and public_ipv4)):
            raise ValueError("HTTP authentication requires an explicitly enabled verification origin")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid authentication port")
        if parsed.port is not None and not parsed.netloc.endswith(f":{parsed.port}"):
            raise ValueError("Use the canonical origin port")
        # Browsers omit the default port from Origin; configure exactly that form.
        if (parsed.scheme == "http" and parsed.port == 80) or (parsed.scheme == "https" and parsed.port == 443):
            raise ValueError("Omit the default origin port")
        base = os.getenv("APP_BASE_PATH", "")
        if base and not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", base):
            raise ValueError("Invalid base path")
        mode = os.getenv("OAUTH_MODE", "disabled")
        if mode not in {"disabled", "github", "mock"} or (mode == "mock" and os.getenv("APP_ENV") != "test"):
            raise ValueError("Invalid OAuth mode")
        settings = cls(
            database_url=secret("DATABASE_URL"), origin=origin, base_path=base,
            oauth_mode=mode, client_id=os.getenv("GITHUB_CLIENT_ID", ""),
            client_secret=secret("GITHUB_CLIENT_SECRET"), transaction_key=secret("AUTH_TRANSACTION_KEY"),
            authorize_url=os.getenv("GITHUB_AUTHORIZE_URL", "https://github.com/login/oauth/authorize"),
            token_url=os.getenv("GITHUB_TOKEN_URL", "https://github.com/login/oauth/access_token"),
            user_url=os.getenv("GITHUB_USER_URL", "https://api.github.com/user"),
            session_seconds=int(os.getenv("SESSION_SECONDS", "28800")),
            transaction_seconds=int(os.getenv("TRANSACTION_SECONDS", "300")),
            secure_cookie=parsed.scheme == "https",
            trusted_proxy_cidrs=tuple(filter(None, os.getenv("AUTH_TRUSTED_PROXY_CIDRS", "").split(","))),
        )
        if not settings.database_url or settings.session_seconds < 60 or not 30 <= settings.transaction_seconds <= 600:
            raise ValueError("Invalid database or session settings")
        if mode != "disabled" and not all((settings.client_id, settings.client_secret, settings.transaction_key)):
            raise ValueError("OAuth credentials are required in enabled mode")
        if mode == "github" and (settings.authorize_url != "https://github.com/login/oauth/authorize" or settings.token_url != "https://github.com/login/oauth/access_token" or settings.user_url != "https://api.github.com/user"):
            raise ValueError("GitHub endpoints cannot be overridden outside mock mode")
        for cidr in settings.trusted_proxy_cidrs:
            ipaddress.ip_network(cidr, strict=True)
        return settings

    @property
    def callback_url(self):
        return f"{self.origin}{self.base_path}/api/auth/github/callback"

    @property
    def cookie_path(self):
        return f"{self.base_path}/api"
