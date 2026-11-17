"""Read-only HTTPS readiness request for a separately approved existing idle Pod.

Self-contained stdlib source can be supplied on stdin to the Pod's Python. This
module never executes kubectl, creates a Pod or acquires credentials.
"""
import argparse
import hashlib
import http.client
import json
import re
import signal
import socket
import ssl
from urllib.parse import urlsplit

CONNECT_HOST = 'traefik.kube-system.svc.cluster.local'


def service_host(namespace, service):
    if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value) for value in (namespace, service)):
        raise ValueError('Invalid approved Service identity')
    return service + '.' + namespace + '.svc.cluster.local'


def pinned_connection(host, *, timeout, context, connect_host=CONNECT_HOST):
    connection = http.client.HTTPSConnection(host, 443, timeout=timeout, context=context)
    def connect(address, timeout, source_address=None):
        if address != (host, 443) or source_address is not None:
            raise ValueError()
        return socket.create_connection((connect_host, 443), timeout=timeout)
    connection._create_connection = connect
    return connection


def destination(origin, base):
    parsed = urlsplit(origin)
    authority = '[' + parsed.hostname + ']' if parsed.hostname and ':' in parsed.hostname else parsed.hostname
    if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.isascii()
            or parsed.port is not None or parsed.path or parsed.query or parsed.fragment
            or parsed.geturl() != origin or any(ord(char) <= 32 or char in '\\%' for char in origin)
            or parsed.netloc != authority
            or parsed.username or parsed.password or re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', base) is None):
        raise ValueError('Canonical approved HTTPS origin required')
    return parsed.hostname, base + '/api/health'


def probe(origin, base, connect_host=CONNECT_HOST):
    host, path = destination(origin, base)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile='/etc/ssl/certs/ca-certificates.crt')
    connection = pinned_connection(host, timeout=10, context=context, connect_host=connect_host)
    try:
        connection.connect()
        peer = connection.sock.getpeername()
        fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        connection.request('GET', path, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(1025)
        ready = len(raw) <= 1024 and json.loads(raw).get('status') == 'ok'
        return {'tls_verified': True, 'transport_service': connect_host, 'peer': peer[0], 'port': peer[1],
                'certificate_sha256': fingerprint, 'http': response.status, 'ready': ready}
    finally:
        connection.close()


def expired(*args):
    raise TimeoutError()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--base-path', default='')
    parser.add_argument('--traefik-namespace', default='kube-system')
    parser.add_argument('--traefik-service', default='traefik')
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(20)
    try:
        result = probe(args.origin, args.base_path, service_host(args.traefik_namespace, args.traefik_service))
        print(json.dumps(result, sort_keys=True))
        return 0 if result['http'] == 200 and result['ready'] else 1
    except Exception as error:
        print(json.dumps({'passed': False, 'failure_type': type(error).__name__}))
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
