"""#59: transport token from an env reference, removed after reading, never echoed."""
import logging
import os
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from runner.app import create_app
from runner.binding import initialize_binding

TOKEN = 'synthetic-transport-' + 'z'*40


@pytest.fixture
def env(tmp_path, monkeypatch):
    owner, state, pod = (str(uuid4()) for _ in range(3))
    root = tmp_path/'native'
    initialize_binding(root, owner, state)
    for key, value in {'APP_ENV':'test', 'RUNNER_ACCOUNT_ID':owner, 'RUNNER_STATE_ID':state,
                       'RUNNER_POD_UID':pod, 'RUNNER_TOKEN':TOKEN, 'RUNNER_STATE_DIR':str(root),
                       'CODEX_AUTH_MODE':'none', 'RESEARCH_TOOL_CALLBACK_URL':'http://callback.invalid/tools'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('RUNNER_TOKEN_FILE', raising=False)
    seen = []
    class Native:
        fixture, real_verified = False, False
        def __init__(self, *args, **kwargs):
            seen.append('RUNNER_TOKEN' in os.environ)  # Native children start after this point.
            self.state_lock, self.lock = threading.RLock(), threading.Lock()
            self.active_session, self.mapping = None, {}
        def close(self): return True
        def availability(self): return None
    monkeypatch.setattr('runner.app.Native', Native)
    return state, seen


def test_env_token_is_removed_before_native_and_never_echoed(env, caplog, capfd):
    state, seen = env
    caplog.set_level(logging.DEBUG)
    app = create_app()
    assert 'RUNNER_TOKEN' not in os.environ
    with TestClient(app) as client:
        good = client.get('/health', headers={'Authorization':'Bearer '+TOKEN, 'X-Runner-State-Id':state})
        bad = client.get('/health', headers={'Authorization':'Bearer '+'w'*40, 'X-Runner-State-Id':state})
    assert (good.status_code, bad.status_code) == (200, 401)
    assert seen == [False]
    out, err = capfd.readouterr()
    assert TOKEN not in good.text + bad.text + out + err + caplog.text


def test_short_env_token_is_refused_without_echo(env, monkeypatch):
    monkeypatch.setenv('RUNNER_TOKEN', 'short-secret-value')
    with pytest.raises(ValueError) as error:
        create_app()
    assert 'short-secret-value' not in str(error.value) + repr(error.value)
    assert 'RUNNER_TOKEN' not in os.environ
