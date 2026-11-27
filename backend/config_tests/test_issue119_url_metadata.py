"""#119 K3 (D9): SSRF guards and metadata mapping, no network (fake resolver + httpx.MockTransport)."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4
import socket

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import url_metadata as um
from app.research import ResearchError

PUBLIC4, PUBLIC6 = '93.184.216.34', '2606:4700:4700::1111'


def lookup_of(table):
    def lookup(host, port, type=None):
        import ipaddress
        try:  # like getaddrinfo, an IP literal resolves to itself
            values = table.get(host) or [str(ipaddress.ip_address(host))]
        except ValueError:
            values = table[host]
        if isinstance(values, Exception):
            raise values
        return [(socket.AF_INET6 if ':' in v else socket.AF_INET, socket.SOCK_STREAM, 6, '', (v, port)) for v in values]
    return lookup


def code(call):
    with pytest.raises(ResearchError) as caught:
        call()
    return caught.value.args[0], caught.value.args[2]


HTML = '''<html><head><meta charset="utf-8"><title> Fallback  title </title>
<meta name="citation_title" content="논문 제목"><meta name="citation_author" content="Kim, Minji">
<meta name="citation_author" content="Lee Jun"><meta name="citation_publication_date" content="2026/11/18">
<meta name="citation_journal_title" content="Journal of Tests"><meta name="citation_doi" content="https://doi.org/10.1234/abc.5">
<meta property="og:site_name" content="Example Site"></head><body>x</body></html>'''


def page(body=HTML, status=200, kind='text/html; charset=utf-8', headers=None):
    # A streamed body, as a real transport returns it (content= would be pre-read and leave nothing for iter_raw).
    return httpx.Response(status, headers={'content-type': kind, **(headers or {})},
                          stream=Counted(body.encode() if isinstance(body, str) else body))


def run(url, routes, table=None, clock=None):
    seen = []
    def handler(request):
        seen.append(request)
        return routes(request) if callable(routes) else routes
    final, html = um.fetch(url, lookup=lookup_of(table or {'example.com': [PUBLIC4]}),
                           transport=httpx.MockTransport(handler), **({'clock': clock} if clock else {}))
    return final, html, seen


# --- URL guard ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('url,expected', [
    ('ftp://example.com/', ('url_not_allowed', 422)), ('file:///etc/passwd', ('url_not_allowed', 422)),
    ('javascript:alert(1)', ('url_not_allowed', 422)), ('https://user:pw@example.com/', ('url_not_allowed', 422)),
    ('https://user@example.com/', ('url_not_allowed', 422)), ('https://example.com:8443/', ('url_not_allowed', 422)),
    ('http://example.com:22/', ('url_not_allowed', 422)), ('https://example.com:99999/', ('validation_error', 422)),
    ('https://example.com:0/', ('url_not_allowed', 422)), ('http://example.com:00/', ('url_not_allowed', 422)),
    ('https://exa mple.com/', ('validation_error', 422)), ('https://example.com/\x00', ('validation_error', 422)),
    ('https://example.com/' + 'a' * 2048, ('validation_error', 422)), ('https:///nohost', ('url_not_allowed', 422))])
def test_scheme_userinfo_port_and_shape_guards(url, expected):
    assert code(lambda: um.target(url)) == expected


def test_allowed_targets_and_idna():
    assert um.target('https://example.com/a?b') == ('https', 'example.com', 443)
    assert um.target('http://example.com:443/') == ('http', 'example.com', 443)
    assert um.target('https://[2606:4700:4700::1111]/') == ('https', PUBLIC6, 443)
    assert um.target('https://한국.kr/')[1] == 'xn--3e0b707e.kr'


# --- address guard --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('address', [
    '127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.169.254', '100.64.0.1', '0.0.0.0', '224.0.0.1',
    '240.0.0.1', '255.255.255.255', '192.0.2.1', '198.18.0.1', '::1', '::', 'fe80::1', 'fc00::1', 'ff02::1',
    '::ffff:10.0.0.1', '::ffff:127.0.0.1', '::ffff:169.254.169.254', '2002:0a00:0001::1', '2002:7f00:0001::1',
    '64:ff9b::a00:1', '64:ff9b::a9fe:a9fe', '2001:db8::1', '2001:0:4136:e378:8000:63bf:f5ff:fffe',
    '2002:5db8:d822::1'])  # 6to4 is never globally routable, even around a public IPv4
def test_non_public_addresses_are_refused(address):
    assert not um.public_address(address)
    assert code(lambda: um.resolve('h', 443, lookup_of({'h': [address]}))) == ('url_not_allowed', 422)


@pytest.mark.parametrize('address', [PUBLIC4, PUBLIC6, '::ffff:93.184.216.34'])
def test_public_addresses_pass(address):
    assert um.public_address(address)


def refusal(call):
    with pytest.raises(ResearchError) as caught:
        call()
    return caught.value.args


def test_unresolvable_private_and_timed_out_names_get_one_identical_answer():
    """review r1 major / PM: status, code, message and details are the same, so internal names cannot be probed."""
    import threading
    release = threading.Event()
    def slow(host, port, type=None):
        release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC4, port))]
    try:
        answers = [refusal(lambda: um.resolve('h', 443, lookup_of({'h': [PUBLIC4, '10.0.0.1']}))),  # any private answer
                   refusal(lambda: um.resolve('h', 443, lookup_of({'h': ['10.96.0.10']}))),          # cluster-internal
                   refusal(lambda: um.resolve('h', 443, lookup_of({'h': socket.gaierror('nx')}))),   # does not exist
                   refusal(lambda: um.resolve('h', 443, lookup_of({'h': OSError('resolver')}))),
                   refusal(lambda: um.resolve('h', 443, lookup_of({'h': []}))),
                   refusal(lambda: um.resolve('h', 443, slow, timeout=0.05)),                        # lookup timed out
                   refusal(lambda: um.resolve('h', 443, lookup_of({'h': [PUBLIC4]}), timeout=0))]    # deadline already gone
    finally:
        release.set()
    assert all(answer == answers[0] for answer in answers)
    assert answers[0][0] == 'url_not_allowed' and answers[0][2] == 422 and answers[0][1] == 'This address cannot be fetched'


# --- connection pinning, redirects, limits --------------------------------------------------------------------------

def test_connection_goes_to_the_validated_address_with_original_host_and_sni():
    final, html, seen = run('https://example.com/path?q=1#frag', page())
    request = seen[0]
    assert request.url.host == PUBLIC4 and request.url.port in (None, 443) and request.url.raw_path == b'/path?q=1'
    assert request.headers['host'] == 'example.com' and request.extensions['sni_hostname'] == 'example.com'
    assert request.headers['user-agent'] == um.USER_AGENT and 'cookie' not in request.headers
    assert final == 'https://example.com/path?q=1#frag' and '논문 제목' in html


def test_ipv6_target_is_bracketed_in_url_and_host():
    _, _, seen = run('http://[2606:4700:4700::1111]/', page(), table={PUBLIC6: [PUBLIC6]})
    assert seen[0].url.host == PUBLIC6 and seen[0].headers['host'] == '[2606:4700:4700::1111]'


def test_dns_rebinding_cannot_reach_a_private_address_on_a_later_hop():
    """Each hop resolves and validates again; a name that now answers privately is refused before any connection."""
    answers = iter([[PUBLIC4], ['127.0.0.1']])
    def lookup(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (v, port)) for v in next(answers)]
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={'location': 'https://example.com/again'})
    assert code(lambda: um.fetch('https://example.com/', lookup=lookup, transport=httpx.MockTransport(handler))) == ('url_not_allowed', 422)
    assert len(seen) == 1 and seen[0].url.host == PUBLIC4


@pytest.mark.parametrize('location,expected', [('http://127.0.0.1/', ('url_not_allowed', 422)),
                                               ('http://169.254.169.254/latest/meta-data/', ('url_not_allowed', 422)),
                                               ('https://example.com:8443/', ('url_not_allowed', 422)),
                                               ('file:///etc/passwd', ('url_not_allowed', 422)),
                                               ('https://u:p@example.com/', ('url_not_allowed', 422))])
def test_redirect_targets_are_revalidated(location, expected):
    routes = lambda request: httpx.Response(302, headers={'location': location})
    assert code(lambda: run('https://example.com/', routes)) == expected


def test_relative_redirect_and_the_redirect_limit():
    hops = iter([httpx.Response(301, headers={'location': '/next'}), page()])
    final, _, seen = run('https://example.com/start', lambda request: next(hops))
    assert final == 'https://example.com/next' and seen[1].url.raw_path == b'/next'
    loop = lambda request: httpx.Response(302, headers={'location': '/loop'})
    calls = []
    def counting(request):
        calls.append(request)
        return loop(request)
    assert code(lambda: run('https://example.com/', counting)) == ('fetch_failed', 502)
    assert len(calls) == um.MAX_REDIRECTS + 1


def test_status_content_type_and_transport_errors():
    assert code(lambda: run('https://example.com/', page(status=404))) == ('fetch_failed', 502)
    assert code(lambda: run('https://example.com/', page(status=500))) == ('fetch_failed', 502)
    assert code(lambda: run('https://example.com/', page(kind='application/pdf'))) == ('unsupported_content', 415)
    assert code(lambda: run('https://example.com/', page(kind='image/svg+xml'))) == ('unsupported_content', 415)
    def broken(request):
        raise httpx.ConnectError('refused', request=request)
    assert code(lambda: run('https://example.com/', broken)) == ('fetch_failed', 502)
    def slow(request):
        raise httpx.ReadTimeout('slow', request=request)
    assert code(lambda: run('https://example.com/', slow)) == ('fetch_failed', 502)


def test_body_read_stops_at_one_mebibyte():
    big = b'<title>t</title>' + b'a' * (3 * um.MAX_BYTES)
    _, html, _ = run('https://example.com/', page(big))
    assert len(html) == um.MAX_BYTES and html.startswith('<title>t</title>')


def test_a_lookup_still_queued_at_the_deadline_never_runs():
    """review r2: with every resolver thread busy, the expired lookup is cancelled instead of running later."""
    import threading
    release, started = threading.Event(), threading.Barrier(um.CONCURRENT + 1)
    def busy():
        started.wait(); release.wait(5)
    blockers = [um.RESOLVER.submit(busy) for _ in range(um.CONCURRENT)]
    started.wait(2)
    calls = []
    def lookup(host, port, type=None):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC4, port))]
    try:
        assert refusal(lambda: um.resolve('queued.example', 443, lookup, timeout=0.05))[0] == 'url_not_allowed'
    finally:
        release.set()
    for blocker in blockers:
        blocker.result(2)
    um.RESOLVER.submit(lambda: None).result(2)  # the pool has drained everything still queued
    assert calls == []
    assert um.resolve('next.example', 443, lookup, timeout=2) == PUBLIC4 and calls == ['next.example']


def test_a_lookup_already_running_at_the_deadline_finishes_and_its_result_is_discarded():
    """The OS lookup in progress cannot be interrupted; the caller is refused and the late answer is not used."""
    import threading
    entered, release = threading.Event(), threading.Event()
    def slow(host, port, type=None):
        entered.set(); release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC4, port))]
    try:
        assert refusal(lambda: um.resolve('slow.example', 443, slow, timeout=0.05))[0] == 'url_not_allowed'
        assert entered.is_set()
    finally:
        release.set()
    um.RESOLVER.submit(lambda: None).result(2)
    fast = lookup_of({'fast.example': [PUBLIC4]})
    assert um.resolve('fast.example', 443, fast, timeout=2) == PUBLIC4


def test_total_deadline_stops_further_hops():
    """A later hop whose name lookup starts after the deadline gets the unified address refusal (PM: same answer)."""
    times = iter([0.0, 1.0, 1.0, 6.0, 6.0, 6.0])
    hops = iter([httpx.Response(302, headers={'location': '/next'}), page()])
    assert code(lambda: run('https://example.com/', lambda request: next(hops), clock=lambda: next(times))) == ('url_not_allowed', 422)


def test_deadline_after_lookup_but_before_the_request_is_502():
    times = iter([0.0, 1.0, 6.0])
    assert code(lambda: run('https://example.com/', page(), clock=lambda: next(times))) == ('fetch_failed', 502)


# --- one absolute deadline over every socket operation (review r1 blocker) -------------------------------------------

class FakeStream:
    def __init__(self, log):
        self.log = log

    def read(self, max_bytes, timeout=None):
        self.log.append(('read', timeout)); return b''

    def write(self, buffer, timeout=None):
        self.log.append(('write', timeout))

    def close(self):
        self.log.append(('close', None))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.log.append(('tls', timeout, server_hostname)); return self

    def get_extra_info(self, info):
        return None


class FakeInner:
    def __init__(self, log):
        self.log = log

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.log.append(('connect', timeout, host, port)); return FakeStream(self.log)

    def sleep(self, seconds):
        pass


def test_every_socket_operation_waits_at_most_until_the_deadline():
    import httpcore
    now = [0.0]
    log = []
    backend = um.DeadlineBackend(5.0, clock=lambda: now[0], inner=FakeInner(log))
    stream = backend.connect_tcp('93.184.216.34', 443, timeout=30)
    now[0] = 1.5; tls = stream.start_tls(None, 'example.com', timeout=30)
    now[0] = 4.0; tls.write(b'GET', timeout=30)
    now[0] = 4.75; tls.read(10, timeout=None)
    assert log == [('connect', 5.0, '93.184.216.34', 443), ('tls', 3.5, 'example.com'), ('write', 1.0), ('read', 0.25)]
    now[0] = 5.0
    for operation, error in ((lambda: tls.read(10, 1), httpcore.ReadTimeout), (lambda: tls.write(b'x', 1), httpcore.WriteTimeout),
                             (lambda: backend.connect_tcp('h', 443, 1), httpcore.ConnectTimeout),
                             (lambda: tls.start_tls(None, 'h', 1), httpcore.ConnectTimeout)):
        with pytest.raises(error):
            operation()
    assert len(log) == 4  # nothing reached the socket after the deadline


def test_production_transport_keeps_tls_verification_and_uses_the_deadline_backend():
    import ssl
    transport = um.DeadlineTransport(10.0)
    context = transport._pool._ssl_context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert isinstance(transport._pool._network_backend, um.DeadlineBackend)
    transport.close()


class Trickle:
    """Loopback HTTP server that answers slowly at the header or body stage."""
    def __init__(self, stage):
        import threading
        self.stage, self.server = stage, socket.socket()
        self.server.bind(('127.0.0.1', 0)); self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.done = threading.Event()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        connection, _ = self.server.accept()
        with connection:
            connection.recv(4096)
            if self.stage == 'headers':
                connection.sendall(b'HTTP/1.1 200 OK\r\n')
                payload = b'X-Slow: ' + b'a' * 100
            else:
                connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n<title>t</title>')
                payload = b'a' * 100
            for byte in payload:
                if self.done.wait(0.05):
                    break
                try:
                    connection.sendall(bytes([byte]))
                except OSError:
                    break

    def close(self):
        self.done.set(); self.server.close()


class Loopback:
    """Connects every pinned public address to the local trickle server (the real socket backend underneath)."""
    def __init__(self, port):
        import httpcore
        self.port, self.inner = port, httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return self.inner.connect_tcp('127.0.0.1', self.port, timeout, local_address, socket_options)

    def sleep(self, seconds):
        pass


@pytest.mark.parametrize('stage', ['headers', 'body'])
def test_a_trickling_server_is_cut_at_the_deadline(monkeypatch, stage):
    """review r1 blocker: a server that keeps sending header (or body) bytes cannot hold the fetch past 5 s."""
    monkeypatch.setattr(um, 'TOTAL_SECONDS', 0.6)
    server = Trickle(stage)
    try:
        started = um.time.monotonic()
        assert code(lambda: um.fetch('http://example.com/', lookup=lookup_of({'example.com': [PUBLIC4]}),
                                     backend=Loopback(server.port))) == ('fetch_failed', 502)
        assert um.time.monotonic() - started < 1.2
    finally:
        server.close()


# --- decompression bounds (review r1 major) ---------------------------------------------------------------------------

class Counted(httpx.SyncByteStream):
    def __init__(self, data, size=65536):
        self.data, self.size, self.pulled = data, size, 0

    def __iter__(self):
        for start in range(0, len(self.data), self.size):
            chunk = self.data[start:start + self.size]
            self.pulled += len(chunk)
            yield chunk


def encoded(data, encoding):
    import gzip, zlib
    return gzip.compress(data) if encoding == 'gzip' else zlib.compress(data)


@pytest.mark.parametrize('encoding', ['gzip', 'deflate'])
def test_compression_bomb_is_bounded_after_decoding(encoding):
    body = encoded(b'<title>bomb</title>' + b'\0' * (64 * um.MAX_BYTES), encoding)
    import tracemalloc
    stream = Counted(body, size=len(body))  # one raw chunk that would inflate to 64 MiB at once
    response = httpx.Response(200, headers={'content-type': 'text/html', 'content-encoding': encoding}, stream=stream)
    tracemalloc.start()
    try:
        _, html, _ = run('https://example.com/', response)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(html) == um.MAX_BYTES and html.startswith('<title>bomb</title>')
    assert peak < 16 * um.MAX_BYTES, peak  # decoding stops at 1 MiB; it never holds the 64 MiB expansion


def test_raw_bytes_are_bounded_before_decoding():
    import os as _os
    noise = _os.urandom(4 * um.MAX_BYTES)  # incompressible: the raw stream itself is ~4 MiB
    stream = Counted(encoded(b'<title>n</title>' + noise, 'gzip'))
    response = httpx.Response(200, headers={'content-type': 'text/html', 'content-encoding': 'gzip'}, stream=stream)
    _, html, _ = run('https://example.com/', response)
    assert len(html) <= um.MAX_BYTES and stream.pulled <= um.MAX_BYTES + 65536
    plain = Counted(b'<title>p</title>' + b'a' * (4 * um.MAX_BYTES))
    _, html, _ = run('https://example.com/', httpx.Response(200, headers={'content-type': 'text/html'}, stream=plain))
    assert len(html) == um.MAX_BYTES and plain.pulled <= um.MAX_BYTES + 65536


def test_only_identity_gzip_and_deflate_are_accepted():
    for encoding in ('br', 'zstd', 'compress', 'gzip, gzip'):
        response = httpx.Response(200, headers={'content-type': 'text/html', 'content-encoding': encoding}, stream=Counted(b'x'))
        assert code(lambda: run('https://example.com/', response)) == ('unsupported_content', 415)
    broken = httpx.Response(200, headers={'content-type': 'text/html', 'content-encoding': 'gzip'}, stream=Counted(b'not gzip'))
    assert code(lambda: run('https://example.com/', broken)) == ('fetch_failed', 502)
    _, _, seen = run('https://example.com/', page())
    assert seen[0].headers['accept-encoding'] == 'gzip, deflate'


def test_proxy_environment_is_ignored(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://10.0.0.1:3128'); monkeypatch.setenv('ALL_PROXY', 'http://10.0.0.1:3128')
    made = []
    real = httpx.Client
    def client(*args, **kwargs):
        made.append(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(um.httpx, 'Client', client)
    run('https://example.com/', page())
    assert made[0]['trust_env'] is False and made[0]['follow_redirects'] is False


def test_per_account_rate_and_process_concurrency():
    now = [0.0]
    limits = um.Limits(clock=lambda: now[0])
    owner = uuid4()
    for _ in range(um.PER_MINUTE):
        limits.admit(owner); limits.release()
    assert code(lambda: limits.admit(owner)) == ('rate_limited', 429)
    limits.admit(uuid4()); limits.release()  # another account is not affected
    now[0] = 60.0
    limits.admit(owner); limits.release()
    held = [uuid4() for _ in range(um.CONCURRENT)]
    for value in held:
        limits.admit(value)
    assert code(lambda: limits.admit(uuid4())) == ('rate_limited', 429)
    limits.release()
    limits.admit(uuid4())


# --- metadata ---------------------------------------------------------------------------------------------------------

def test_citation_metadata_maps_to_validated_csl():
    value = um.metadata(HTML)
    assert value['title'] == '논문 제목' and value['siteName'] == 'Example Site'
    assert value['bibliography'] == {'type': 'article-journal', 'title': '논문 제목',
                                     'author': [{'family': 'Kim', 'given': 'Minji'}, {'literal': 'Lee Jun'}],
                                     'issued': {'date-parts': [[2026, 11, 18]]}, 'container-title': 'Journal of Tests',
                                     'DOI': '10.1234/abc.5'}


def test_opengraph_and_title_fallbacks_and_invalid_fields_are_dropped():
    html = '''<title>Only &amp; title</title><meta property="og:type" content="article">
    <meta property="article:published_time" content="2026-02-30T10:00:00Z"><meta property="article:author" content="https://x/u">
    <meta name="author" content="홍길동"><meta property="og:site_name" content="뉴스">
    <meta name="citation_doi" content="not-a-doi">'''
    value = um.metadata(html)
    assert value['title'] == 'Only & title'
    assert value['bibliography'] == {'type': 'webpage', 'title': 'Only & title', 'author': [{'literal': '홍길동'}],
                                     'container-title': '뉴스'}  # Feb 30 and the bad DOI are dropped, not fatal


def test_empty_or_broken_html_still_answers():
    assert um.metadata('') == {'bibliography': {'type': 'webpage'}}
    assert um.metadata('<title>x')['title'] == 'x'


def test_charset_from_header_or_meta():
    body = '<title>한글</title>'.encode('euc-kr')
    assert um.decode(body, 'text/html; charset=euc-kr') == '<title>한글</title>'
    assert um.decode(b'<meta charset="euc-kr">' + body, 'text/html') .endswith('<title>한글</title>')
    assert um.decode(b'<title>x</title>', 'text/html; charset=nonsense') == '<title>x</title>'


# --- endpoint ---------------------------------------------------------------------------------------------------------

def endpoint(monkeypatch, *, allow=True, fetched=None):
    order = []
    app = FastAPI()
    app.add_exception_handler(ResearchError, lambda request, error: JSONResponse({'error': {'code': error.args[0]}}, status_code=error.args[2]))
    from pydantic import ValidationError  # the real app maps it to 422 (app/research.py exception handler)
    app.add_exception_handler(ValidationError, lambda request, error: JSONResponse({'error': {'code': 'validation_error'}}, status_code=422))
    @contextmanager
    def authorized(request, mutate=False):
        order.append(('authorized', mutate))
        if not allow:
            raise ResearchError('forbidden', 'no', 403)
        yield SimpleNamespace(owner=uuid4())
        order.append('released')
    def fetch(url):
        order.append(('fetch', url))
        return fetched or ('https://example.com/final', HTML)
    monkeypatch.setattr(um, 'fetch', fetch)
    um.register(app, authorized, SimpleNamespace(base_path='/base'), lambda value: JSONResponse(value, headers={'Cache-Control': 'no-store'}))
    return TestClient(app), order


def test_endpoint_authorizes_first_then_fetches_outside_the_transaction(monkeypatch):
    client, order = endpoint(monkeypatch)
    reply = client.post('/base/api/research/url-metadata', json={'url': 'https://example.com/a'})
    assert reply.status_code == 200 and reply.json()['url'] == 'https://example.com/final'
    assert reply.json()['bibliography']['DOI'] == '10.1234/abc.5' and reply.headers['cache-control'] == 'no-store'
    assert order == [('authorized', True), 'released', ('fetch', 'https://example.com/a')]


def test_endpoint_refuses_before_any_fetch(monkeypatch):
    client, order = endpoint(monkeypatch, allow=False)
    assert client.post('/base/api/research/url-metadata', json={'url': 'https://example.com/'}).status_code == 403
    assert not any(isinstance(step, tuple) and step[0] == 'fetch' for step in order)
    client, order = endpoint(monkeypatch)
    assert client.post('/base/api/research/url-metadata', json={'url': 'http://10.0.0.1:8080/'}).status_code == 422
    assert client.post('/base/api/research/url-metadata', json={'url': 'https://e.com', 'extra': 1}).status_code == 422
    assert not any(isinstance(step, tuple) and step[0] == 'fetch' for step in order)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv('URL_METADATA_FETCH', 'off')
    client, order = endpoint(monkeypatch)
    reply = client.post('/base/api/research/url-metadata', json={'url': 'https://example.com/'})
    assert reply.status_code == 503 and reply.json()['error']['code'] == 'not_ready'
    assert ('authorized', True) in order and not any(isinstance(step, tuple) and step[0] == 'fetch' for step in order)
