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


@pytest.mark.parametrize('port,path,legacy,proxied,expected', [
    (8081, CALLBACK, False, False, True),
    (8081, CALLBACK, False, True, True),  # policy limits 8081 to runner Pods; headers do not matter
    (8081, CALLBACK + '/', False, False, False),
    (8081, '/api/research/sessions', False, False, False),
    (8081, '/healthz', False, False, False),
    (8081, '/api/internal/other', False, False, False),
    (8081, '/api/auth/github/start', True, False, False),
    (8080, CALLBACK, False, False, False),
    (8080, '/api/internal', False, False, False),
    (8080, '/api/internal/', False, False, False),
    (8080, '/api/internal/anything', False, False, False),
    (8080, '/api/internalx', False, False, False),
    (8080, '/api/internal\x00/research/tools', False, False, False),
    (8080, CALLBACK, True, False, True),  # switch window: a pre-#62 runner calls back on 8080
    (8080, CALLBACK, True, True, False),  # ... but never through a proxy
    (8080, '/api/internal/other', True, False, False),
    (8080, '/api/internal', True, False, False),
    (8080, '/api/interna', False, False, True),
    (8080, '/api/INTERNAL/research/tools', False, True, True),  # no such route; the app answers 404
    (8080, '/api/research/sessions', False, True, True),
    (8080, '/healthz', False, False, True),
    (8080, '/readyz', False, False, True),
    (None, '/healthz', False, False, False),
    (9999, '/healthz', True, False, False)])
def test_gate_table(port, path, legacy, proxied, expected):
    assert serve.allowed(port, path, '', legacy, proxied) is expected


def test_base_path_is_part_of_the_internal_prefix():
    assert serve.allowed(8081, '/base' + CALLBACK, '/base', False, False)
    assert not serve.allowed(8081, CALLBACK, '/base', False, False)
    assert not serve.allowed(8080, '/base' + CALLBACK, '/base', True, True)
    assert serve.allowed(8080, '/base' + CALLBACK, '/base', True, False)
    assert serve.allowed(8080, CALLBACK, '/base', False, False)  # not the base path's internal route


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


@pytest.fixture(scope='module', params=[False, True], ids=['steady', 'switch'])
def listeners(request):
    server = uvicorn.Server(uvicorn.Config(serve.gate(echo, '', request.param), access_log=False,
                                           proxy_headers=False, log_level='warning'))
    sockets = [serve.listener(serve.PUBLIC_PORT), serve.listener(serve.INTERNAL_PORT)]
    thread = threading.Thread(target=server.run, kwargs={'sockets': sockets}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive()
        time.sleep(0.05)
    yield request.param
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


# What the pinned Traefik 3.7.8 adds to every proxied request (measured, #62 review round).
TRAEFIK = {'X-Forwarded-For': '10.42.0.5', 'X-Real-Ip': '10.42.0.5', 'X-Forwarded-Server': 'traefik-0',
           'X-Forwarded-Host': '192.88.99.1', 'X-Forwarded-Port': '80', 'X-Forwarded-Proto': 'http'}
# Raw forms Traefik forwards undecoded. uvicorn decodes the first group into exactly the callback
# path, the second into other paths under the internal prefix.
TO_CALLBACK = ('/api/%69nternal/research/tools', '/api/internal%2Fresearch/tools', '/api/internal%2fresearch%2ftools',
               '/api%2Finternal%2Fresearch%2Ftools')
UNDER_PREFIX = ('/api/internal%5Cresearch/tools', '/api/internal%00/research/tools',
                '/api/internal%252Fresearch/tools', '/api/internal;x/research/tools')
ENCODED = TO_CALLBACK + UNDER_PREFIX


def test_public_socket_refuses_internal_paths(listeners):
    status, _, body = call(8080, '/api/research/sessions')
    assert status == 200 and json.loads(body)['port'] == 8080
    for path in ('/api/internal', '/api/internal/', '/api/internalx') + UNDER_PREFIX:
        status, cache, _ = call(8080, path)
        assert (status, cache) == (404, 'no-store'), path
    # Only in the switch window, and only unproxied (a pre-#62 runner Pod; policy admits no one else
    # besides Traefik, whose requests always carry proxy headers).
    for path in (CALLBACK,) + TO_CALLBACK:
        status, _, body = call(8080, path)
        assert status == (200 if listeners else 404), path
        if listeners:
            assert json.loads(body) == {'path': CALLBACK, 'port': 8080}


def test_proxied_request_never_reaches_the_callback_on_public(listeners):
    for name, value in TRAEFIK.items():
        for path in (CALLBACK,) + ENCODED:
            status, cache, _ = call(8080, path, headers={name: value})
            assert (status, cache) == (404, 'no-store'), (name, path)
    # forwarded header names are case-insensitive on the wire
    status, _, _ = call(8080, CALLBACK, headers={'x-real-ip': '10.42.0.5'})
    assert status == 404


def test_headers_cannot_choose_the_listener(listeners):
    spoof = {'Host': 'api-internal.m2-hosting.svc.cluster.local:8081', 'X-Forwarded-Port': '8081',
             'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'api-internal:8081', 'X-Forwarded-For': '10.42.0.9'}
    status, _, _ = call(8080, CALLBACK, headers=spoof)
    assert status == 404
    status, _, body = call(8081, CALLBACK, headers={'Host': 'api.m2-hosting.svc.cluster.local:8080',
                                                    'X-Forwarded-Port': '8080'})
    assert status == 200 and json.loads(body)['port'] == 8081
