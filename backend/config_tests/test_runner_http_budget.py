"""Deterministic incoming-chunk and per-socket remaining-budget regressions."""
from types import SimpleNamespace
import ssl

import httpcore
import httpx
import pytest

from app import runner_http_budget as budget
from app.runner_kube import KubePods
from config_tests.test_runner_kube import plan
from runner.lifecycle import Refused


def test_trickle_checks_each_transport_chunk(plan, monkeypatch):
    elapsed, consumed = [0.0], []
    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                elapsed[0] += 0.25
                consumed.append(i)
                yield b' '
    monkeypatch.setattr('app.runner_kube.time', SimpleNamespace(monotonic=lambda: elapsed[0]))
    port = KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan], timeout=2, deadline=1,
        _transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Trickle())))
    try:
        with pytest.raises(Refused): port.lookup(plan.identity)
        assert len(consumed) <= 5
    finally:
        port.close()


class SocketStream:
    def __init__(self, elapsed):
        self.elapsed, self.calls, self.closed = elapsed, [], False

    def read(self, max_bytes, timeout):
        self.calls.append(('read', timeout))
        self.elapsed[0] += timeout
        raise httpcore.ReadTimeout('synthetic blocked read')

    def start_tls(self, context, hostname, timeout):
        self.calls.append(('tls', timeout, context, hostname))
        self.elapsed[0] += 0.2
        return self

    def close(self):
        self.closed = True

    def get_extra_info(self, name):
        return self if name == 'socket' else None

    def settimeout(self, timeout):
        self.timeout = timeout
        self.calls.append(('write', timeout))

    def send(self, value):
        import socket
        self.elapsed[0] += min(0.3, self.timeout)
        if self.timeout < 0.3:
            raise socket.timeout()
        return 1


def test_remaining_budget_caps_connect_tls_and_blocked_read():
    elapsed = [0.0]
    raw = SocketStream(elapsed)
    class Connect:
        def connect_tcp(self, host, port, timeout, **kwargs):
            assert host == '127.0.0.1' and timeout == 1
            elapsed[0] += 0.3
            return raw
    backend = budget.Backend(); backend.backend = Connect()
    token = budget.begin(1, lambda: elapsed[0])
    context = ssl.create_default_context()
    try:
        stream = backend.connect_tcp('127.0.0.1', 443, timeout=2)
        stream = stream.start_tls(context, '127.0.0.1', timeout=2)
        assert raw.calls[0] == ('tls', pytest.approx(0.7), context, '127.0.0.1')
        with pytest.raises(httpcore.ReadTimeout): stream.read(1024, timeout=2)
        assert raw.calls[-1] == ('read', pytest.approx(0.5)) and elapsed[0] == pytest.approx(1)
    finally:
        budget.finish(token)


def test_partial_writes_each_receive_remaining_budget():
    elapsed = [0.0]; raw = SocketStream(elapsed)
    token = budget.begin(1, lambda: elapsed[0])
    try:
        with pytest.raises(httpcore.WriteTimeout): budget.Stream(raw).write(b'x'*100, timeout=2)
        assert [call[1] for call in raw.calls] == pytest.approx([1, .7, .4, .1])
        assert elapsed[0] == pytest.approx(1)
    finally:
        budget.finish(token)


def test_reused_connection_uses_current_request_budget():
    elapsed = [0.0]; raw = SocketStream(elapsed); stream = budget.Stream(raw)
    for end in (1, 3):
        token = budget.begin(end, lambda: elapsed[0])
        try:
            with pytest.raises(httpcore.ReadTimeout): stream.read(1024, timeout=10)
        finally:
            budget.finish(token)
    assert [call[1] for call in raw.calls] == [1, 2]


def test_real_transport_refuses_hostname_before_dns(plan):
    with pytest.raises(Refused): KubePods('https://kube.invalid', 'isolated', 'k'*40, None, [plan])


def test_budget_transport_keeps_tls_verification(plan):
    port = KubePods('https://127.0.0.1', 'isolated', 'k'*40, None, [plan])
    try:
        context = port.client._transport._pool._ssl_context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert isinstance(port.client._transport._pool._network_backend, budget.Backend)
    finally:
        port.close()


@pytest.mark.parametrize('trusted', [True, False])
def test_real_loopback_tls_trickle_budget_and_ca_verification(plan, tmp_path, trusted):
    import socket
    import subprocess
    import threading
    import time
    cert, key = tmp_path/'cert.pem', tmp_path/'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-keyout', str(key), '-out', str(cert), '-subj', '/CN=127.0.0.1',
        '-addext', 'subjectAltName=IP:127.0.0.1'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(3)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
    sent = []
    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                with context.wrap_socket(connection, server_side=True) as stream:
                    headers = b''
                    while b'\r\n\r\n' not in headers:
                        value = stream.recv(4096)
                        if not value: return
                        headers += value
                    stream.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n')
                    for index in range(100):
                        stream.sendall(b' '); sent.append(index); time.sleep(.02)
        except (OSError, ssl.SSLError):
            pass
    worker = threading.Thread(target=serve); worker.start()
    port = KubePods('https://127.0.0.1:'+str(listener.getsockname()[1]), 'isolated', 'k'*40,
                    str(cert) if trusted else None, [plan], timeout=2, deadline=.25)
    started = time.monotonic()
    try:
        with pytest.raises(Refused): port.lookup(plan.identity)
        elapsed = time.monotonic() - started
        assert elapsed < 1, elapsed  # Scheduling tolerance; deterministic tests cover exact budget.
    finally:
        port.close(); listener.close(); worker.join(timeout=3)
    assert not worker.is_alive()
    assert 0 < len(sent) < 100 if trusted else len(sent) == 0
