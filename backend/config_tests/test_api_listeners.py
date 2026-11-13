"""#62 API listener split: the port gate, unit level and over real loopback sockets (no DB)."""
import http.client
import json
import threading
import time

import pytest
import uvicorn

from app import serve

CALLBACK = '/api/internal/research/tools'


async def echo(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return
    body = json.dumps({'path': scope['path'], 'port': scope['server'][1]}).encode()
    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
    await send({'type': 'http.response.body', 'body': body})


@pytest.mark.parametrize('port,path,legacy,expected', [
    (8081, CALLBACK, False, True),
    (8081, CALLBACK + '/', False, False),
    (8081, '/api/research/sessions', False, False),
    (8081, '/healthz', False, False),
    (8081, '/api/internal/other', False, False),
    (8081, '/api/auth/github/start', True, False),
    (8080, CALLBACK, False, False),
    (8080, '/api/internal', False, False),
    (8080, '/api/internal/', False, False),
    (8080, '/api/internal/anything', False, False),
    (8080, CALLBACK, True, True),
    (8080, '/api/internalx', False, True),
    (8080, '/api/research/sessions', False, True),
    (8080, '/healthz', False, True),
    (8080, '/readyz', False, True),
    (None, '/healthz', False, False),
    (9999, '/healthz', True, False)])
def test_gate_table(port, path, legacy, expected):
    assert serve.allowed(port, path, '', legacy) is expected


def test_base_path_is_part_of_the_internal_prefix():
    assert serve.allowed(8081, '/base' + CALLBACK, '/base', False)
    assert not serve.allowed(8081, CALLBACK, '/base', False)
    assert not serve.allowed(8080, '/base' + CALLBACK, '/base', False)
    assert serve.allowed(8080, CALLBACK, '/base', False)  # not the base path's internal route


@pytest.mark.parametrize('value,expected', [(None, False), ('false', False), ('true', True)])
def test_flag_values(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(serve.FLAG, raising=False)
    else:
        monkeypatch.setenv(serve.FLAG, value)
    assert serve.public_internal() is expected


@pytest.mark.parametrize('value', ['True', '1', 'yes', '', 'true '])
def test_flag_refuses_other_values(monkeypatch, value):
    monkeypatch.setenv(serve.FLAG, value)
    with pytest.raises(RuntimeError):
        serve.public_internal()


@pytest.fixture(scope='module')
def listeners():
    server = uvicorn.Server(uvicorn.Config(serve.gate(echo, '', False), access_log=False,
                                           proxy_headers=False, log_level='warning'))
    sockets = [serve.listener(serve.PUBLIC_PORT), serve.listener(serve.INTERNAL_PORT)]
    thread = threading.Thread(target=server.run, kwargs={'sockets': sockets}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive()
        time.sleep(0.05)
    yield
    server.should_exit = True
    thread.join(10)
    for sock in sockets:
        sock.close()


def call(port, path, method='POST', headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    connection.request(method, path, body=b'{}', headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, response.getheader('cache-control'), body


def test_internal_socket_serves_only_the_callback(listeners):
    status, _, body = call(8081, CALLBACK)
    assert status == 200 and json.loads(body) == {'path': CALLBACK, 'port': 8081}
    for path in ('/healthz', '/api/research/sessions', '/api/auth/github/start', CALLBACK + 'x'):
        status, cache, body = call(8081, path)
        assert (status, cache) == (404, 'no-store') and json.loads(body)['error']['code'] == 'not_found'


def test_public_socket_refuses_internal_paths(listeners):
    status, _, body = call(8080, '/api/research/sessions')
    assert status == 200 and json.loads(body)['port'] == 8080
    for path in (CALLBACK, '/api/internal', '/api/%69nternal/research/tools', '/api/internal%2Fresearch/tools'):
        status, cache, _ = call(8080, path)
        assert (status, cache) == (404, 'no-store'), path


def test_headers_cannot_choose_the_listener(listeners):
    spoof = {'Host': 'api-internal.m2-hosting.svc.cluster.local:8081', 'X-Forwarded-Port': '8081',
             'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'api-internal:8081', 'X-Forwarded-For': '10.42.0.9'}
    status, _, _ = call(8080, CALLBACK, headers=spoof)
    assert status == 404
    status, _, body = call(8081, CALLBACK, headers={'Host': 'api.m2-hosting.svc.cluster.local:8080',
                                                    'X-Forwarded-Port': '8080'})
    assert status == 200 and json.loads(body)['port'] == 8081
