import base64
import hashlib
import hmac
import json
import logging
import os
import ipaddress
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from .database import check_ready, database
from .models import Account, LoginSession, OAuthTransaction
from .settings import Settings

SESSION_COOKIE = "anxious_session"
TRANSACTION_COOKIE = "anxious_oauth"
log = logging.getLogger("hosting")


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def session_digest(value, origin):
    # A cookie obtained during HTTP/IP verification cannot authenticate after
    # an origin or HTTPS transition. Legacy unbound hashes require re-login.
    return digest("m2-origin-session-v1\0" + origin + "\0" + value)


def error(code, message, status):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers={"Cache-Control": "no-store"})


def create_app(settings=None):
    settings = settings or Settings.load()
    engine, sessions = database(settings.database_url)
    cipher = Fernet(settings.transaction_key.encode()) if settings.oauth_mode != "disabled" else None
    version_path = Path(os.getenv("RELEASE_FILE", "/app/release.json"))

    @asynccontextmanager
    async def lifespan(app):
        release = json.loads(version_path.read_text())
        if not re.fullmatch(r"[0-9a-f]{40}", release.get("sha", "")):
            raise RuntimeError("Invalid release SHA")
        built = release.get("builtAt", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", built):
            raise RuntimeError("Invalid release timestamp")
        datetime.fromisoformat(built.replace("Z", "+00:00"))
        app.state.release = release
        yield
        engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.settings = settings
    prefix = settings.base_path + "/api"

    @app.middleware("http")
    async def auth_transport(request, call_next):
        # Mock transport is confined to APP_ENV=test. Never let a public HTTP
        # callback issue a real app session merely because configured origin is HTTPS.
        is_auth = request.url.path.startswith((prefix + "/auth/", prefix + "/research/"))
        needs_protection = settings.oauth_mode == "github" or bool(request.cookies.get(SESSION_COOKIE))
        if is_auth and needs_protection and settings.oauth_mode != "mock":
            protocol = request.url.scheme
            if settings.trusted_proxy_cidrs:
                try:
                    peer = ipaddress.ip_address(request.client.host)
                    trusted = any(peer in ipaddress.ip_network(cidr) for cidr in settings.trusted_proxy_cidrs)
                except ValueError:
                    trusted = False
                if trusted:
                    protocol = request.headers.get("x-forwarded-proto", "")
            try:
                actual = urlsplit(protocol + "://" + request.headers.get("host", ""))
                expected = urlsplit(settings.origin)
                default_port = 443 if expected.scheme == "https" else 80
                matches = (actual.scheme == expected.scheme and actual.hostname == expected.hostname
                           and (actual.port or default_port) == (expected.port or default_port)
                           and not any((actual.path, actual.query, actual.fragment, actual.username, actual.password)))
            except ValueError:
                matches = False
            if not matches:
                if request.url.path.endswith(("/github/start", "/github/callback")):
                    return callback_failure("server_error")
                return error("not_ready", "Service is not ready", 503)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return error("validation_error", "Invalid request fields", 422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        # Never log exception/SQL/connection URLs or callback query strings.
        log.warning("Authentication database unavailable")
        if request.url.path == prefix + "/auth/github/start":
            return callback_failure("server_error")
        return error("not_ready", "Service is not ready", 503)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        result = error("method_not_allowed" if exc.status_code == 405 else "not_found", "Method not allowed" if exc.status_code == 405 else "Not found", exc.status_code)
        if exc.headers:
            result.headers.update(exc.headers)
        if request.method == "HEAD":
            result.body = b""
            result.headers["content-length"] = "0"
        return result

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        log.error("Authentication request failed")
        if request.url.path == prefix + "/auth/github/start":
            return callback_failure("server_error")
        return error("internal_error", "Internal server error", 500)

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    @app.get("/readyz")
    @app.get(prefix + "/health")
    def ready():
        try:
            check_ready(engine)
        except (SQLAlchemyError, RuntimeError):
            return error("not_ready", "Service is not ready", 503)
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    @app.get(prefix + "/version")
    def version():
        return JSONResponse(app.state.release, headers={"Cache-Control": "no-store"})

    def set_cookie(response, name, value, path, lifetime):
        response.set_cookie(name, value, max_age=lifetime, path=path, secure=settings.secure_cookie, httponly=True, samesite="lax")

    def clear_cookie(response, name, path):
        response.delete_cookie(name, path=path, secure=settings.secure_cookie, httponly=True, samesite="lax")

    def callback_failure(code):
        response = RedirectResponse(settings.base_path + "/auth/login/?" + urlencode({"auth_error": code}), status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        clear_cookie(response, TRANSACTION_COOKIE, settings.cookie_path + "/auth/github")
        return response

    @app.get(prefix + "/auth/github/start")
    def start():
        if cipher is None:
            return callback_failure("server_error")
        state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with sessions.begin() as db:
            db.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at < now()))
            db.add(OAuthTransaction(state_hash=digest(state), binding_hash=digest(binding), verifier=cipher.encrypt(verifier.encode()).decode(), expires_at=now() + timedelta(seconds=settings.transaction_seconds)))
        query = {"client_id": settings.client_id, "redirect_uri": settings.callback_url, "state": state, "scope": "", "code_challenge": challenge, "code_challenge_method": "S256"}
        response = RedirectResponse(settings.authorize_url + "?" + urlencode(query), status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        set_cookie(response, TRANSACTION_COOKIE, binding, settings.cookie_path + "/auth/github", settings.transaction_seconds)
        return response

    @app.get(prefix + "/auth/github/callback")
    def callback(request: Request):
        if cipher is None:
            return callback_failure("server_error")
        state = request.query_params.get("state", "")
        binding = request.cookies.get(TRANSACTION_COOKIE, "")
        if not state or not binding or len(state) > 200 or len(binding) > 200:
            return callback_failure("invalid_state")
        try:
            with sessions.begin() as db:
                transaction = db.scalar(select(OAuthTransaction).where(OAuthTransaction.state_hash == digest(state)).with_for_update())
                if not transaction or not hmac.compare_digest(transaction.binding_hash, digest(binding)):
                    return callback_failure("invalid_state")
                encrypted = transaction.verifier
                expired = transaction.expires_at <= now()
                db.delete(transaction)  # Single-use, including denial/provider failures.
            if expired:
                return callback_failure("expired")
            if request.query_params.get("error"):
                return callback_failure("access_denied" if request.query_params["error"] == "access_denied" else "github_error")
            code = request.query_params.get("code", "")
            if not code or len(code) > 512:
                return callback_failure("github_error")
            verifier = cipher.decrypt(encrypted.encode()).decode()
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
                result = client.post(settings.token_url, data={"client_id": settings.client_id, "client_secret": settings.client_secret, "code": code, "redirect_uri": settings.callback_url, "code_verifier": verifier}, headers={"Accept": "application/json"})
                result.raise_for_status()
                token = result.json().get("access_token")
                if not isinstance(token, str) or not token or len(token) > 1000:
                    return callback_failure("github_error")
                user_result = client.get(settings.user_url, headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
                user_result.raise_for_status()
                user = user_result.json()
            if type(user.get("id")) is not int or user["id"] <= 0 or not isinstance(user.get("login"), str) or not 0 < len(user["login"]) <= 100:
                return callback_failure("github_error")
            session_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with sessions.begin() as db:
                statement = insert(Account).values(github_id=str(user["id"]), login=user["login"]).on_conflict_do_update(index_elements=[Account.github_id], set_={"login": user["login"], "updated_at": now()}).returning(Account.id)
                account_id = db.scalar(statement)
                db.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
                db.add(LoginSession(token_hash=session_digest(session_token, settings.origin), account_id=account_id, csrf_token=csrf, expires_at=now() + timedelta(seconds=settings.session_seconds)))
            response = RedirectResponse(settings.base_path + "/", status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
            set_cookie(response, SESSION_COOKIE, session_token, settings.cookie_path, settings.session_seconds)
            clear_cookie(response, TRANSACTION_COOKIE, settings.cookie_path + "/auth/github")
            return response
        except SQLAlchemyError:
            return callback_failure("server_error")
        except (httpx.HTTPError, ValueError, KeyError):
            return callback_failure("github_error")
        except Exception:
            return callback_failure("server_error")

    def current_session(db, request):
        token = request.cookies.get(SESSION_COOKIE, "")
        if not token or len(token) > 200:
            return None
        return db.scalar(select(LoginSession).where(LoginSession.token_hash == session_digest(token, settings.origin), LoginSession.expires_at > now()))

    @app.get(prefix + "/auth/me")
    def me(request: Request):
        with sessions() as db:
            session = current_session(db, request)
            if not session:
                return error("unauthenticated", "Authentication required", 401)
            account = db.get(Account, session.account_id)
            return JSONResponse({"user": {"accountId": str(account.id), "githubId": account.github_id, "login": account.login, "role": account.role, "isApproved": account.is_approved}, "csrfToken": session.csrf_token}, headers={"Cache-Control": "no-store"})

    @app.post(prefix + "/auth/logout")
    def logout(request: Request):
        if request.headers.get("origin") != settings.origin:
            return error("origin_not_allowed", "Origin not allowed", 403)
        with sessions.begin() as db:
            session = current_session(db, request)
            if session:
                if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf_token):
                    return error("csrf_invalid", "Invalid CSRF token", 403)
                db.delete(session)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        clear_cookie(response, SESSION_COOKIE, settings.cookie_path)
        return response

    from .research import register_research
    register_research(app, sessions, settings, current_session, error)
    return app
