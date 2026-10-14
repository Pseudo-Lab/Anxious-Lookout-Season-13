"""Synchronous HTTP socket budgets for the pinned HTTPX/httpcore transport.

Each request has its own context, including when a pooled connection is reused.
Only literal IP destinations are permitted, avoiding an unbounded DNS resolver.
"""
from contextvars import ContextVar
import ipaddress
import socket

import httpcore
import httpx

_budget = ContextVar('runner_http_budget')


def begin(end, clock):
    return _budget.set((end, clock))


def finish(token):
    _budget.reset(token)


def remaining(timeout=None):
    end, clock = _budget.get()
    value = end - clock()
    if value <= 0:
        raise httpcore.ReadTimeout('Request budget exhausted')
    return value if timeout is None else min(value, timeout)


class Stream(httpcore.NetworkStream):
    def __init__(self, stream):
        self.stream = stream

    def read(self, max_bytes, timeout=None):
        value = self.stream.read(max_bytes, timeout=remaining(timeout))
        remaining()  # Do not accept a result that arrived after the deadline.
        return value

    def write(self, buffer, timeout=None):
        # SyncBackend.write loops partial sends with one unchanged timeout. Use
        # one socket send at a time so each partial send gets remaining budget.
        sock = self.stream.get_extra_info('socket')
        try:
            while buffer:
                sock.settimeout(remaining(timeout))
                count = sock.send(buffer)
                if count <= 0:
                    raise httpcore.WriteError('Connection closed')
                buffer = buffer[count:]
                remaining()
        except socket.timeout:
            raise httpcore.WriteTimeout('Request budget exhausted') from None
        except OSError:
            raise httpcore.WriteError('Connection write failed') from None

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        stream = self.stream.start_tls(ssl_context, server_hostname, timeout=remaining(timeout))
        try:
            remaining()
            return Stream(stream)
        except Exception:
            stream.close()
            raise

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class Backend(httpcore.NetworkBackend):
    def __init__(self):
        self.backend = httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ipaddress.ip_address(host)  # No hostname/DNS or alternate addresses.
        stream = self.backend.connect_tcp(host, port, timeout=remaining(timeout),
            local_address=local_address, socket_options=socket_options)
        try:
            remaining()
            return Stream(stream)
        except Exception:
            stream.close()
            raise


class Transport(httpx.HTTPTransport):
    def __init__(self, context):
        super().__init__(verify=context, retries=0, trust_env=False)
        # Pinned HTTPX exposes no public network_backend constructor argument.
        # Its normal request/response mapping is retained; only the pool changes.
        self._pool.close()
        # No shared pool queue or idle-connection retry loop can consume a fresh
        # timeout budget. Every request gets a new connection, still no retries.
        self._pool = httpcore.ConnectionPool(ssl_context=context, retries=0, http2=False,
                                            max_connections=None, max_keepalive_connections=0,
                                            network_backend=Backend())
