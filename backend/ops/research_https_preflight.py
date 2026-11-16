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
import ssl
from urllib.parse import urlsplit


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


def probe(origin, base):
    host, path = destination(origin, base)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile='/etc/ssl/certs/ca-certificates.crt')
    connection = http.client.HTTPSConnection(host, 443, timeout=10, context=context)
    try:
        connection.connect()
        peer = connection.sock.getpeername()
        fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        connection.request('GET', path, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(1025)
        ready = len(raw) <= 1024 and json.loads(raw).get('status') == 'ok'
        return {'tls_verified': True, 'peer': peer[0], 'port': peer[1],
                'certificate_sha256': fingerprint, 'http': response.status, 'ready': ready}
    finally:
        connection.close()


def expired(*args):
    raise TimeoutError()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--base-path', default='')
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(20)
    try:
        result = probe(args.origin, args.base_path)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['http'] == 200 and result['ready'] else 1
    except Exception as error:
        print(json.dumps({'passed': False, 'failure_type': type(error).__name__}))
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
