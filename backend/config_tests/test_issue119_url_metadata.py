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
    return httpx.Response(status, headers={'content-type': kind, **(headers or {})}, content=body.encode() if isinstance(body, str) else body)


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


def test_any_private_answer_refuses_the_whole_name_and_dns_failure_is_502():
    assert code(lambda: um.resolve('h', 443, lookup_of({'h': [PUBLIC4, '10.0.0.1']}))) == ('url_not_allowed', 422)
    assert code(lambda: um.resolve('h', 443, lookup_of({'h': socket.gaierror('nx')}))) == ('fetch_failed', 502)
    assert code(lambda: um.resolve('h', 443, lookup_of({'h': []}))) == ('fetch_failed', 502)


def test_dns_lookup_is_waited_for_only_until_the_deadline():
    import threading
    release = threading.Event()
    def slow(host, port, type=None):
        release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC4, port))]
    try:
        assert code(lambda: um.resolve('h', 443, slow, timeout=0.05)) == ('fetch_failed', 502)
    finally:
        release.set()


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


def test_total_deadline_stops_further_hops():
    times = iter([0.0, 1.0, 1.0, 6.0, 6.0, 6.0])
    hops = iter([httpx.Response(302, headers={'location': '/next'}), page()])
    assert code(lambda: run('https://example.com/', lambda request: next(hops), clock=lambda: next(times))) == ('fetch_failed', 502)


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
