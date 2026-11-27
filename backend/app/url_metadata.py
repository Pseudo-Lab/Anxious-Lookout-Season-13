"""#119 K3: title/bibliography from a source URL, behind application-level SSRF guards (decision D9).

No network-policy change: API egress is default-allow (compose `outbound`, k3s PARITY.md). Every guard is here:
- http/https only, no userinfo, port 80/443 only, URL <= 2048, approved editor/admin session + CSRF
- every resolved address must be public unicast (also the IPv4 inside mapped/6to4/Teredo/NAT64 IPv6 forms);
  the connection goes to the validated address with Host/SNI = the original name (no DNS rebinding)
- <= 3 redirects, each re-validated; one 5 s deadline over DNS and every socket operation of every hop
- at most 1 MiB raw and 1 MiB decoded (identity/gzip/deflate only); HTML only; proxy env ignored
- a name that does not resolve, resolves privately or times out gets the same 422 url_not_allowed
- 10 requests/minute per account and 4 concurrent fetches per API process
Nothing is stored. Kill switch: URL_METADATA_FETCH=off.
"""
import ipaddress
import os
import re
import socket
import threading
import time
import zlib
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx
from fastapi import Request
from pydantic import Field

from .research import Input, fail

MAX_URL = 2048
MAX_BYTES = 1024 * 1024
TOTAL_SECONDS = 5.0
MAX_REDIRECTS = 3
PORTS = {80, 443}
PER_MINUTE = 10
CONCURRENT = 4
REDIRECTS = {301, 302, 303, 307, 308}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
NAT64 = ipaddress.ip_network("64:ff9b::/96")
USER_AGENT = "AnxiousLookoutMetadata/1.0 (+bibliography prefill)"
RESOLVER = ThreadPoolExecutor(max_workers=CONCURRENT, thread_name_prefix="url-metadata-dns")


class Body(Input):
    url: str = Field(min_length=1, max_length=MAX_URL)


def enabled():
    return os.getenv("URL_METADATA_FETCH", "on").strip().lower() != "off"


def public_address(value):
    """True only for a public unicast address, looking through IPv6 forms that embed an IPv4 address."""
    ip = ipaddress.ip_address(value)
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is None and ip in NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None and not public_address(embedded):
            return False
    return ip.is_global and not (ip.is_multicast or ip.is_private or ip.is_loopback or ip.is_link_local
                                 or ip.is_reserved or ip.is_unspecified)


def target(url):
    """Validate the URL itself; returns (scheme, host, port)."""
    if len(url) > MAX_URL or re.search(r"[\s\x00-\x1f\x7f]", url):
        fail("validation_error", "Invalid URL", 422)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        fail("validation_error", "Invalid URL", 422)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        fail("url_not_allowed", "Only http and https URLs are allowed", 422)
    if parts.username is not None or parts.password is not None:
        fail("url_not_allowed", "URLs with credentials are not allowed", 422)
    if port is None:  # review r1 minor: an explicit :0 is an explicit port, not the default
        port = 443 if scheme == "https" else 80
    if port not in PORTS:
        fail("url_not_allowed", "Only ports 80 and 443 are allowed", 422)
    host = parts.hostname
    if ":" not in host:  # IP literals stay as they are; names go to their ASCII (IDNA) form for DNS, Host and SNI
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            fail("validation_error", "Invalid URL host", 422)
    return scheme, host, port


def refuse_address():
    # review r1 / PM: one answer for "does not resolve", "resolves privately" and "lookup ran out of time", so the
    # endpoint cannot be used to learn which internal names exist.
    fail("url_not_allowed", "This address cannot be fetched", 422)


def resolve(host, port, lookup=socket.getaddrinfo, timeout=TOTAL_SECONDS):
    """Every address the name resolves to must be public; returns the first (connections are pinned to it).
    getaddrinfo has no timeout of its own, so it runs on a small pool and is waited for at most `timeout`."""
    if timeout <= 0:  # the deadline passed before this hop's lookup: same answer as a lookup that timed out
        refuse_address()
    future = RESOLVER.submit(lookup, host, port, type=socket.SOCK_STREAM)
    try:
        infos = future.result(timeout=timeout)
    except FutureTimeout:
        # review r2: a lookup still queued behind busy resolver threads is dropped, so an abandoned request never
        # runs later in front of the next one. One already running in getaddrinfo cannot be interrupted; it ends on
        # its own and its result is discarded.
        future.cancel()
        refuse_address()
    except (socket.gaierror, UnicodeError, OSError):
        refuse_address()
    addresses = [info[4][0].split("%", 1)[0] for info in infos]
    if not addresses or not all(public_address(address) for address in addresses):
        refuse_address()
    return addresses[0]


class DeadlineStream(httpcore.NetworkStream):
    """Every socket operation (TLS handshake, header and body reads, writes) waits at most until the fetch deadline."""
    def __init__(self, stream, backend):
        self.stream, self.backend = stream, backend

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, self.backend.budget(timeout, httpcore.ReadTimeout))

    def write(self, buffer, timeout=None):
        self.stream.write(buffer, self.backend.budget(timeout, httpcore.WriteTimeout))

    def close(self):
        self.stream.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return DeadlineStream(self.stream.start_tls(ssl_context, server_hostname, self.backend.budget(timeout, httpcore.ConnectTimeout)),
                              self.backend)

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class DeadlineBackend(httpcore.NetworkBackend):
    def __init__(self, deadline, clock=time.monotonic, inner=None):
        self.deadline, self.clock, self.inner = deadline, clock, inner or httpcore.SyncBackend()

    def budget(self, timeout, error):
        left = self.deadline - self.clock()
        if left <= 0:
            raise error("The fetch deadline has passed")
        return left if timeout is None else min(timeout, left)

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return DeadlineStream(self.inner.connect_tcp(host, port, self.budget(timeout, httpcore.ConnectTimeout),
                                                     local_address, socket_options), self)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("Unix sockets are not used")

    def sleep(self, seconds):
        self.inner.sleep(seconds)


class DeadlineTransport(httpx.HTTPTransport):
    """httpx's own transport (TLS verification, h11) over a pool whose sockets all share one absolute deadline."""
    def __init__(self, deadline, clock=time.monotonic, inner=None):
        super().__init__(trust_env=False)
        ssl_context = self._pool._ssl_context  # the verifying default context httpx built (httpx 0.28 / httpcore 1.0)
        self._pool.close()
        self._pool = httpcore.ConnectionPool(ssl_context=ssl_context, max_connections=1, max_keepalive_connections=0,
                                             network_backend=DeadlineBackend(deadline, clock, inner))


def read_body(response):
    """At most MAX_BYTES of raw bytes are read and at most MAX_BYTES are produced, before and after decompression."""
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        inflater = None
    elif encoding in ("gzip", "x-gzip"):
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        inflater = zlib.decompressobj(zlib.MAX_WBITS)
    else:
        fail("unsupported_content", "The page uses an unsupported content encoding", 415)
    raw, body = 0, bytearray()
    for chunk in response.iter_raw():
        data = chunk[:MAX_BYTES - raw]
        raw += len(data)
        if inflater is None:
            body += data[:MAX_BYTES - len(body)]
        else:
            while data and len(body) < MAX_BYTES:
                try:
                    body += inflater.decompress(data, MAX_BYTES - len(body))
                except zlib.error:
                    fail("fetch_failed", "The page could not be decoded", 502)
                data = inflater.unconsumed_tail
        if raw >= MAX_BYTES or len(body) >= MAX_BYTES:
            break
    return bytes(body)


class Limits:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.calls = defaultdict(deque)
        self.slots = threading.BoundedSemaphore(CONCURRENT)

    def admit(self, owner):
        now = self.clock()
        with self.lock:
            window = self.calls[owner]
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= PER_MINUTE:
                fail("rate_limited", "Too many metadata requests", 429)
            window.append(now)
        if not self.slots.acquire(blocking=False):
            fail("rate_limited", "Too many metadata requests", 429)

    def release(self):
        self.slots.release()


def fetch(url, *, lookup=socket.getaddrinfo, transport=None, clock=time.monotonic, backend=None):
    """GET with every hop re-validated and pinned. Returns (final URL, decoded HTML prefix).

    One absolute deadline (TOTAL_SECONDS) covers DNS and every socket operation of every hop: connect, TLS, request
    write, header and body reads (review r1). `transport`/`backend` are test seams; production uses DeadlineTransport."""
    deadline = clock() + TOTAL_SECONDS
    transport = transport or DeadlineTransport(deadline, clock, backend)
    current = url
    with httpx.Client(trust_env=False, follow_redirects=False, transport=transport, timeout=TOTAL_SECONDS) as client:
        for _ in range(MAX_REDIRECTS + 1):
            scheme, host, port = target(current)
            address = resolve(host, port, lookup, timeout=deadline - clock())
            parts = urlsplit(current)
            literal = "[" + address + "]" if ":" in address else address
            pinned = urlunsplit((scheme, literal + ":" + str(port), parts.path or "/", parts.query, ""))
            if deadline - clock() <= 0:
                fail("fetch_failed", "The page took too long", 502)
            name = "[" + host + "]" if ":" in host else host
            default = 443 if scheme == "https" else 80
            headers = {"Host": name if port == default else f"{name}:{port}", "User-Agent": USER_AGENT,
                       "Accept": "text/html,application/xhtml+xml;q=0.9", "Accept-Encoding": "gzip, deflate"}
            try:
                with client.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as response:
                    if response.status_code in REDIRECTS and response.headers.get("location"):
                        current = urljoin(current, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        fail("fetch_failed", "The page answered with an error", 502)
                    kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if kind not in HTML_TYPES:
                        fail("unsupported_content", "Only HTML pages can be read", 415)
                    return current, decode(read_body(response), response.headers.get("content-type", ""))
            except httpx.HTTPError:
                fail("fetch_failed", "The page could not be fetched", 502)
    fail("fetch_failed", "Too many redirects", 502)


def decode(body, content_type):
    match = re.search(r"charset=([\w.-]+)", content_type, re.I) or re.search(rb'<meta[^>]+charset=["\']?([\w.-]+)', body[:4096], re.I)
    charset = match.group(1) if match else "utf-8"
    charset = charset.decode("ascii", "ignore") if isinstance(charset, bytes) else charset
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class Meta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values, self.title, self.in_title = defaultdict(list), "", False

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value for key, value in attrs if value is not None}
        if tag == "meta":
            name = (attrs.get("name") or attrs.get("property") or "").strip().lower()
            content = " ".join((attrs.get("content") or "").split())
            if name and content:
                self.values[name].append(content)
        elif tag == "title" and not self.title:
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title = " ".join((self.title + " " + data).split())


def first(values, *names):
    for name in names:
        for value in values.get(name, []):
            if value:
                return value
    return None


def person(value):
    if "," in value:
        family, given = (part.strip() for part in value.split(",", 1))
        if family and given:
            return {"family": family[:200], "given": given[:200]}
    return {"literal": value[:300]}


def issued(value):
    match = re.match(r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?", value or "")
    if not match:
        return None
    return {"date-parts": [[int(part) for part in match.groups() if part is not None]]}


def metadata(html):
    """Fields found in citation_*, Dublin Core, OpenGraph/article meta, then <title>; each validated on its own."""
    from .research import Bibliography
    parser = Meta()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    values = parser.values
    title = first(values, "citation_title", "og:title", "dc.title", "twitter:title") or parser.title or None
    site = first(values, "og:site_name", "application-name")
    authors = values.get("citation_author") or values.get("dc.creator") or [
        value for value in values.get("article:author", []) + values.get("author", []) if not value.startswith(("http://", "https://"))]
    journal = first(values, "citation_journal_title", "citation_conference_title")
    doi = first(values, "citation_doi", "prism.doi", "dc.identifier")
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", doi, flags=re.I) if doi else None
    candidate = {"type": "article-journal" if journal else "webpage", "title": title[:1000] if title else None,
                 "author": [person(value) for value in authors[:100]] or None,
                 "issued": issued(first(values, "citation_publication_date", "citation_date", "article:published_time", "dc.date", "date")),
                 "container-title": (journal or site or None), "publisher": first(values, "citation_publisher", "dc.publisher"),
                 "DOI": doi}
    bibliography = {"type": candidate["type"]}
    for key, value in candidate.items():
        if key == "type" or value is None:
            continue
        try:
            Bibliography.model_validate({"type": candidate["type"], key: value})
            bibliography[key] = value
        except Exception:
            continue
    result = {"bibliography": Bibliography.model_validate(bibliography).model_dump(by_alias=True, exclude_none=True)}
    if title:
        result["title"] = title[:300]
    if site:
        result["siteName"] = site[:300]
    return result


def register(app, authorized, settings, response):
    limits = Limits()
    app.state.url_metadata_limits = limits

    @app.post(settings.base_path + "/api/research/url-metadata")
    def url_metadata(body: dict, request: Request):
        # Authorize (session, approval, role, Origin, CSRF) in a short transaction; never hold it during the fetch.
        with authorized(request, True) as store:
            owner = store.owner
        if not enabled():
            fail("not_ready", "URL metadata is switched off", 503)
        parsed = Body.model_validate(body)
        target(parsed.url)
        limits.admit(owner)
        try:
            final, html = fetch(parsed.url)
        finally:
            limits.release()
        return response({"url": final, **metadata(html)})
