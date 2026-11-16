#!/usr/local/bin/python -I
"""Sandbox CLI: only stdlib, fixed grant/schema paths, HTTPS443, no retries."""
import http.client
import json
import os
import re
import signal
import ssl
import stat
import sys
from urllib.parse import urlsplit
import uuid

GRANT = '/state/research-api/turn.json'
SCHEMA = '/usr/local/share/anxious-research/operations.json'
OPERATIONS = frozenset({'research_list', 'research_get', 'research_versions', 'research_version_get',
    'research_relations', 'research_material_save', 'research_material_update', 'research_document_save',
    'research_document_update', 'research_relation_save', 'research_relation_update', 'research_archive',
    'research_post_save', 'research_post_draft_get'})
TOKEN = re.compile(r'rturn1\.[0-9a-f-]{36}\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}')


def tls_context():
    # Explicit platform trust; ignore SSL_CERT_FILE/SSL_CERT_DIR/SSLKEYLOGFILE.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile='/etc/ssl/certs/ca-certificates.crt')
    return context


def timeout_signal(*args):
    raise TimeoutError()


def safe(value):
    if isinstance(value, str):
        return TOKEN.sub('[REDACTED]', value)
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, dict):
        return {safe(key): safe(item) for key, item in value.items()}
    return value


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def read_grant():
    descriptor = os.open(GRANT, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 4096):
            raise ValueError()
        value = json.load(source, object_pairs_hook=unique)
    if set(value) != {'token', 'sessionId', 'requestId', 'apiOrigin', 'basePath', 'expiresAt'}:
        raise ValueError()
    if TOKEN.fullmatch(value['token']) is None:
        raise ValueError()
    _, conversation, request, _ = value['token'].split('.')
    if (str(uuid.UUID(conversation)) != value['sessionId']
            or str(uuid.UUID(request)) != value['requestId']):
        raise ValueError()
    origin = urlsplit(value['apiOrigin'])
    if (origin.scheme != 'https' or not origin.hostname or origin.port not in {None, 443}
            or origin.path or origin.username or origin.password or origin.query or origin.fragment
            or re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', value['basePath']) is None):
        raise ValueError()
    return value, origin


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    previous = signal.signal(signal.SIGALRM, timeout_signal)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        if argv and argv[0] == 'schema':
            if len(argv) > 2 or (len(argv) == 2 and argv[1] not in OPERATIONS):
                raise ValueError()
            with open(SCHEMA) as source:
                schemas = json.load(source)
            print(json.dumps(schemas[argv[1]] if len(argv) == 2 else schemas, ensure_ascii=True))
            return 0
        if len(argv) != 1 or argv[0] not in OPERATIONS:
            raise ValueError()
        data = sys.stdin.buffer.read(1_000_001)
        if len(data) > 1_000_000:
            raise ValueError()
        arguments = json.loads(data, object_pairs_hook=unique)
        if not isinstance(arguments, dict) or safe(arguments) != arguments:
            raise ValueError()
        grant, origin = read_grant()
        connection = http.client.HTTPSConnection(origin.hostname, 443, timeout=20,
                                                 context=tls_context())
        try:
            connection.request('POST', grant['basePath'] + '/api/research/agent/v1/' + argv[0],
                body=json.dumps(arguments).encode(), headers={'Authorization': 'Bearer ' + grant['token'],
                    'Content-Type': 'application/json', 'Accept': 'application/json'})
            response = connection.getresponse()
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError()
            result = json.loads(raw, object_pairs_hook=unique)
            if not isinstance(result, dict):
                raise ValueError()
            print(json.dumps(safe(result), ensure_ascii=True))
            return 0 if 200 <= response.status < 300 else 1
        finally:
            connection.close()
    except Exception:
        print(json.dumps({'error': {'code': 'research_api_unavailable',
            'message': 'Research API did not confirm success; check versions before retrying'}}))
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    sys.exit(main())
