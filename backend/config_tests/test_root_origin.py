"""Root admission is separate opt-in; HTTP/session and trial boundaries persist."""
from dataclasses import replace

import pytest

from app.codex_policy import personal_origin_enabled
from app.main import session_digest
from app.settings import Settings

ORIGIN = "https://existing.example.invalid"


@pytest.fixture
def root(monkeypatch):
    for name, value in {
        "APP_ENV": "personal-test", "CODEX_PERSONAL_ENABLE": "true",
        "CODEX_PERSONAL_ROOT_ENABLE": "true", "CODEX_EXECUTION_SCOPE": "personal-private",
        "CODEX_PERSONAL_REMOTE_ORIGIN": ORIGIN,
    }.items():
        monkeypatch.setenv(name, value)
    return Settings(database_url="unused", origin=ORIGIN, oauth_mode="github",
                    client_id="unused", client_secret="unused", transaction_key="unused",
                    authorize_url="unused", token_url="unused", user_url="unused",
                    trusted_proxy_cidrs=("10.42.0.0/16",))


def test_reviewed_root_admission(root):
    assert personal_origin_enabled(root)


@pytest.mark.parametrize("name,value", [
    ("CODEX_PERSONAL_ROOT_ENABLE", None), ("CODEX_PERSONAL_ROOT_ENABLE", "false"),
    ("CODEX_PERSONAL_ROOT_ENABLE", "TRUE"), ("CODEX_PERSONAL_ENABLE", None),
    ("CODEX_PERSONAL_ENABLE", "false"), ("APP_ENV", "production"),
    ("CODEX_EXECUTION_SCOPE", "hosted"), ("CODEX_PERSONAL_REMOTE_ORIGIN", None),
    ("CODEX_PERSONAL_REMOTE_ORIGIN", "https://other.invalid"),
    ("CODEX_PERSONAL_REMOTE_ORIGIN", ORIGIN + "/"),
])
def test_root_requires_each_explicit_gate(root, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    assert not personal_origin_enabled(root)


@pytest.mark.parametrize("changes", [
    {"origin": "http://existing.example.invalid"}, {"secure_cookie": False},
    {"trusted_proxy_cidrs": ()}, {"base_path": "/"}, {"base_path": "/other"},
])
def test_root_flag_cannot_relax_transport_or_enable_arbitrary_path(root, monkeypatch, changes):
    altered = replace(root, **changes)
    monkeypatch.setenv("CODEX_PERSONAL_REMOTE_ORIGIN", altered.origin)
    assert not personal_origin_enabled(altered)


def test_trial_does_not_depend_on_root_opt_in(root, monkeypatch):
    monkeypatch.delenv("CODEX_PERSONAL_ROOT_ENABLE")
    assert personal_origin_enabled(replace(root, base_path="/codex-trial"))
    assert not personal_origin_enabled(root)


def test_loopback_keeps_existing_enable_requirement(root, monkeypatch):
    monkeypatch.delenv("CODEX_PERSONAL_ROOT_ENABLE")
    monkeypatch.delenv("CODEX_PERSONAL_REMOTE_ORIGIN")
    local = replace(root, origin="http://127.0.0.1", secure_cookie=False,
                    trusted_proxy_cidrs=())
    assert personal_origin_enabled(local)
    monkeypatch.setenv("CODEX_PERSONAL_ENABLE", "false")
    assert not personal_origin_enabled(local)


def test_http_session_cannot_be_adopted_by_https_origin():
    token = "disposable-existing-session"
    assert session_digest(token, ORIGIN) != session_digest(token, ORIGIN.replace("https:", "http:"))
