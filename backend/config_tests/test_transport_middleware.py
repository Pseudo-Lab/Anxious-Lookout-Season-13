"""#90: the API `auth_transport` and runner `authenticate` middleware, pinned before and after their move from
BaseHTTPMiddleware to plain ASGI. No DB (the probe routes never touch it), no Codex binary, no network.

The probe routes are added by the tests themselves; the middleware wraps every route, so they exercise exactly the
code a real route sees. Each case asserts status, body and the Cache-Control header.
"""
import threading
from uuid import uuid4

import pytest
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.testclient import TestClient

from app.main import SESSION_COOKIE, create_app as create_api
from app.settings import Settings

ORIGIN = 'https://m2.example.invalid'
KEY = 'A' * 43 + '='  # A syntactically valid Fernet key; nothing is encrypted in these tests.


def settings(**changes):
    value = dict(database_url='postgresql+psycopg://unused:unused@127.0.0.1:1/unused', origin=ORIGIN,
                 oauth_mode='github', client_id='unused', client_secret='unused', transaction_key=KEY,
                 authorize_url='https://github.invalid/authorize', token_url='https://github.invalid/token',
                 user_url='https://github.invalid/user', trusted_proxy_cidrs=('10.42.0.0/16',))
    value.update(changes)
    return Settings(**value)


def probe_api(config):
    app = create_api(config)
    prefix = config.base_path + '/api'

    def chunks():
        yield b'data: one\n\n'
        yield b'data: two\n\n'

    @app.get(prefix + '/research/probe')
    def research_probe():
        return {'ok': True}

    @app.get(prefix + '/research/probe-cached')
    def research_probe_cached():
        return JSONResponse({'ok': True}, headers={'Cache-Control': 'private, max-age=60'})

    @app.get(prefix + '/research/probe-stream')
    def research_probe_stream():
        return StreamingResponse(chunks(), media_type='text/event-stream')

    # Under the cacheable /api/public/posts/ prefix, but not shadowed by the real /posts or /posts/{slug} routes.
    @app.get(prefix + '/public/posts/probe/own')
    def public_posts_probe():
        return JSONResponse({'items': []}, headers={'Cache-Control': 'public, max-age=30'})

    @app.get(prefix + '/public/posts/probe/none')
    def public_posts_plain_probe():
        return JSONResponse({'items': []})

    @app.get(prefix + '/public/posts/probe/not-modified')
    def public_posts_not_modified_probe():
        return Response(status_code=304, headers={'Cache-Control': 'public, max-age=30'})

    @app.get(prefix + '/public/posts/probe/missing')
    def public_posts_missing_probe():
        return JSONResponse({'error': 'x'}, status_code=404, headers={'Cache-Control': 'public, max-age=30'})

    @app.post(prefix + '/public/posts/probe/own')
    def public_posts_write_probe():
        return JSONResponse({'ok': True}, headers={'Cache-Control': 'public, max-age=30'})

    @app.get(prefix + '/other/probe')
    def other_probe():
        return JSONResponse({'ok': True}, headers={'Cache-Control': 'public, max-age=30'})

    return app


@pytest.fixture
def api(monkeypatch):
    for name in ('CODEX_USER_RUNNERS_ENABLE', 'CODEX_RUNNERS_FILE', 'CODEX_PERSONAL_ACCOUNT_ID'):
        monkeypatch.delenv(name, raising=False)
    return probe_api(settings())


def client_for(app, peer='203.0.113.9'):
    return TestClient(app, base_url=ORIGIN, client=(peer, 50000), raise_server_exceptions=False)


def test_matching_origin_passes_and_is_never_cached(api):
    reply = client_for(api).get('/api/research/probe')
    assert (reply.status_code, reply.json(), reply.headers['cache-control']) == (200, {'ok': True}, 'no-store')
    # A route's own Cache-Control is replaced (one header, not appended).
    reply = client_for(api).get('/api/research/probe-cached')
    assert reply.status_code == 200 and reply.headers.get_list('cache-control') == ['no-store']


@pytest.mark.parametrize('base', ['http://m2.example.invalid', 'https://other.example.invalid',
                                  'https://m2.example.invalid:8443'])
def test_wrong_scheme_host_or_port_is_not_ready(api, base):
    reply = TestClient(api, base_url=base, client=('203.0.113.9', 50000)).get('/api/research/probe')
    assert reply.status_code == 503
    assert reply.json() == {'error': {'code': 'not_ready', 'message': 'Service is not ready'}}
    assert reply.headers['cache-control'] == 'no-store'


def test_github_start_mismatch_redirects_to_login_failure(api):
    reply = TestClient(api, base_url='http://m2.example.invalid', client=('203.0.113.9', 50000),
                       follow_redirects=False).get('/api/auth/github/start')
    assert reply.status_code == 303
    assert reply.headers['location'] == '/auth/login/?auth_error=server_error'
    assert reply.headers['cache-control'] == 'no-store' and reply.headers['referrer-policy'] == 'no-referrer'


def test_forwarded_proto_only_from_trusted_peer(api):
    # Plain HTTP to the app, HTTPS at the proxy: only a trusted peer's X-Forwarded-Proto counts.
    headers = {'x-forwarded-proto': 'https'}
    trusted = TestClient(api, base_url='http://m2.example.invalid', client=('10.42.0.7', 50000))
    assert trusted.get('/api/research/probe', headers=headers).status_code == 200
    untrusted = TestClient(api, base_url='http://m2.example.invalid', client=('203.0.113.9', 50000))
    assert untrusted.get('/api/research/probe', headers=headers).status_code == 503
    # A trusted peer without the header has no protocol at all.
    assert trusted.get('/api/research/probe').status_code == 503


def test_non_auth_paths_are_not_origin_checked(api):
    reply = TestClient(api, base_url='http://wrong.invalid', client=('203.0.113.9', 50000)).get('/api/other/probe')
    assert reply.status_code == 200 and reply.headers.get_list('cache-control') == ['no-store']


def test_mock_oauth_skips_check_and_cookie_triggers_it(monkeypatch):
    for name in ('CODEX_USER_RUNNERS_ENABLE', 'CODEX_RUNNERS_FILE', 'CODEX_PERSONAL_ACCOUNT_ID'):
        monkeypatch.delenv(name, raising=False)
    wrong = dict(base_url='http://wrong.invalid', client=('203.0.113.9', 50000))
    mock = probe_api(settings(oauth_mode='mock'))
    assert TestClient(mock, cookies={SESSION_COOKIE: 'x'}, **wrong).get('/api/research/probe').status_code == 200
    disabled = probe_api(settings(oauth_mode='disabled'))
    assert TestClient(disabled, **wrong).get('/api/research/probe').status_code == 200
    assert TestClient(disabled, cookies={SESSION_COOKIE: 'x'}, **wrong).get('/api/research/probe').status_code == 503


def test_public_cacheable_get_keeps_its_own_cache_header(api):
    client = client_for(api)
    assert client.get('/api/public/posts/probe/own').headers.get_list('cache-control') == ['public, max-age=30']
    reply = client.get('/api/public/posts/probe/not-modified')
    assert reply.status_code == 304 and reply.headers.get_list('cache-control') == ['public, max-age=30']
    # No header of its own: the middleware supplies no-store (setdefault).
    assert client.get('/api/public/posts/probe/none').headers.get_list('cache-control') == ['no-store']
    # Cacheable path but not 200/304, or not GET: always no-store.
    reply = client.get('/api/public/posts/probe/missing')
    assert reply.status_code == 404 and reply.headers.get_list('cache-control') == ['no-store']
    assert client.post('/api/public/posts/probe/own').headers.get_list('cache-control') == ['no-store']


def test_not_found_and_errors_are_no_store(api):
    reply = client_for(api).get('/api/research/missing')
    assert reply.status_code == 404 and reply.headers['cache-control'] == 'no-store'


def test_stream_passes_through_with_no_store(api):
    with client_for(api).stream('GET', '/api/research/probe-stream') as reply:
        assert reply.status_code == 200
        assert reply.headers['content-type'].startswith('text/event-stream')
        assert reply.headers['cache-control'] == 'no-store'
        assert b''.join(reply.iter_bytes()) == b'data: one\n\ndata: two\n\n'


# --- runner `authenticate` -----------------------------------------------------------------------------------------

@pytest.fixture
def runner(tmp_path, monkeypatch):
    from runner.app import create_app
    from runner.binding import initialize_binding
    owner, state, pod = (str(uuid4()) for _ in range(3))
    root = tmp_path / 'native'
    initialize_binding(root, owner, state)
    token = tmp_path / 'token'
    token.write_text('t' * 40)
    for key, value in {'APP_ENV': 'test', 'RUNNER_ACCOUNT_ID': owner, 'RUNNER_STATE_ID': state,
                       'RUNNER_POD_UID': pod, 'RUNNER_TOKEN_FILE': str(token), 'RUNNER_STATE_DIR': str(root),
                       'CODEX_AUTH_MODE': 'none', 'RESEARCH_TOOL_CALLBACK_URL': 'http://callback.invalid/tools'}.items():
        monkeypatch.setenv(key, value)

    class Native:
        fixture, real_verified, blocked_reason, binding_failed = False, False, None, False

        def __init__(self, *args, **kwargs):
            self.state_lock, self.lock = threading.RLock(), threading.Lock()
            self.active_session, self.mapping = None, {}

        def close(self):
            return True

        def availability(self):
            return None

        def read(self, session):
            raise RuntimeError('synthetic read failure')

    monkeypatch.setattr('runner.app.Native', Native)
    app = create_app()

    @app.get('/probe-stream')
    def probe_stream():
        def chunks():
            yield b'data: one\n\n'
            yield b'data: two\n\n'
        return StreamingResponse(chunks(), media_type='text/event-stream')

    headers = {'Authorization': 'Bearer ' + 't' * 40, 'X-Runner-State-Id': state}
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, headers, dict(owner=owner, state=state, pod=pod, root=root)


@pytest.mark.parametrize('authorization', [None, '', 'Bearer ' + 't' * 39, 'Bearer ' + 't' * 41, 'bearer ' + 't' * 40,
                                           't' * 40])
def test_runner_bad_bearer_is_401(runner, authorization):
    client, headers, ids = runner
    sent = {**headers}
    sent.pop('Authorization')
    if authorization is not None:
        sent['Authorization'] = authorization
    reply = client.get('/health', headers=sent)
    assert (reply.status_code, reply.json()) == (401, {'error': 'unauthenticated'})
    assert 'cache-control' not in reply.headers


@pytest.mark.parametrize('state', [None, '', 'x', str(uuid4())])
def test_runner_wrong_state_id_is_refused(runner, state):
    client, headers, ids = runner
    sent = {'Authorization': headers['Authorization']}
    if state is not None:
        sent['X-Runner-State-Id'] = state
    reply = client.get('/health', headers=sent)
    assert (reply.status_code, reply.json()) == (503, {'ownerId': ids['owner'], 'error': 'codex_policy_refused'})
    assert 'cache-control' not in reply.headers


def test_runner_broken_binding_refuses_and_blocks_native(runner):
    client, headers, ids = runner
    (ids['root'] / 'state-binding.json').write_text('{"version": 1, "ownerId": "x", "stateId": "y"}')
    reply = client.get('/health', headers=headers)
    assert (reply.status_code, reply.json()) == (503, {'ownerId': ids['owner'], 'error': 'codex_policy_refused'})
    native = client.app.state.native
    assert native.blocked_reason == 'policy_refused' and native.binding_failed is True


def test_runner_authenticated_request_is_no_store(runner):
    client, headers, ids = runner
    reply = client.get('/health', headers=headers)
    assert reply.status_code == 200 and reply.json()['ownerId'] == ids['owner']
    assert reply.headers.get_list('cache-control') == ['no-store']


def test_runner_endpoint_exception_is_codex_unavailable(runner):
    client, headers, ids = runner
    reply = client.get('/sessions/' + str(uuid4()), headers=headers)
    assert (reply.status_code, reply.json()) == (503, {'ownerId': ids['owner'], 'error': 'codex_unavailable'})


def test_runner_stream_passes_through_with_no_store(runner):
    client, headers, ids = runner
    with client.stream('GET', '/probe-stream', headers=headers) as reply:
        assert reply.status_code == 200 and reply.headers['cache-control'] == 'no-store'
        assert b''.join(reply.iter_bytes()) == b'data: one\n\ndata: two\n\n'
    with client.stream('GET', '/probe-stream') as reply:
        assert reply.status_code == 401


def test_no_base_http_middleware_remains(api, runner):
    """#90: both checks run as plain ASGI middleware; nothing buffers or re-wraps a streaming response."""
    from starlette.middleware.base import BaseHTTPMiddleware
    client, headers, ids = runner
    for app in (api, client.app):
        classes = [entry.cls for entry in app.user_middleware]
        assert len(classes) == 1 and not issubclass(classes[0], BaseHTTPMiddleware)
