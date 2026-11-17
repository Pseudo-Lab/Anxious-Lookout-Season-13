#!/usr/bin/env python3 -I
"""External PAT client. Public HTTPS only; no token argv/env, proxy, redirect or retry."""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import sys
from urllib.parse import urlsplit
from uuid import UUID

TOKEN = re.compile(r'rpat1\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}')
CREDENTIALS = re.compile(r'(?:rpat1\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}|rturn1\.[0-9a-f-]{36}\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43})')
OPERATIONS = frozenset({'research_list', 'research_get', 'research_versions', 'research_version_get',
    'research_relations', 'research_material_save', 'research_material_update', 'research_document_save',
    'research_document_update', 'research_relation_save', 'research_relation_update', 'research_archive',
    'research_post_save', 'research_post_draft_get', 'research_lint'})


def safe(value):
    if isinstance(value, str): return CREDENTIALS.sub('[REDACTED]', value)
    if isinstance(value, list): return [safe(item) for item in value]
    if isinstance(value, dict): return {safe(key): safe(item) for key, item in value.items()}
    return value


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError()
        result[key] = value
    return result


def read_token(filename):
    path = Path(filename)
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise ValueError()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError()
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor) as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024):
                raise ValueError()
            token = source.read(1025).strip()
        if not TOKEN.fullmatch(token) or str(UUID(token.split('.')[1])) != token.split('.')[1]:
            raise ValueError()
        return token
    finally:
        os.close(directory)


def origin_host(origin):
    parsed = urlsplit(origin)
    host = parsed.hostname
    authority = '[' + host + ']' if host and ':' in host else host
    if (parsed.scheme != 'https' or not host or not host.isascii() or parsed.netloc != authority
            or parsed.port is not None or parsed.path or parsed.query or parsed.fragment
            or parsed.username or parsed.password or any(ord(c) <= 32 or c in '\\%' for c in origin)):
        raise ValueError()
    return host


def tls_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Compiled platform paths, never the SSL_CERT_FILE/DIR or KEYLOG environment.
    defaults = ssl.get_default_verify_paths()
    cafile, capath = defaults.openssl_cafile, defaults.openssl_capath
    cafile = cafile if cafile and os.path.isfile(cafile) else None
    capath = capath if capath and os.path.isdir(capath) else None
    if not (cafile or capath): raise ValueError()
    context.load_verify_locations(cafile=cafile, capath=capath)
    return context


def exchange(origin, path, *, token=None, body=None):
    connection = http.client.HTTPSConnection(origin_host(origin), 443, timeout=20, context=tls_context())
    headers = {'Accept': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    if body is not None: headers['Content-Type'] = 'application/json'
    try:
        connection.request('POST' if body is not None else 'GET', path,
                           body=json.dumps(body).encode() if body is not None else None, headers=headers)
        response = connection.getresponse()
        raw = response.read(8_000_001)
        if len(raw) > 8_000_000: raise ValueError()
        result = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(result, dict): raise ValueError()
        return response.status, safe(result)
    finally:
        connection.close()


def expired(*args): raise TimeoutError()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown options may accidentally contain a pasted secret. Do not let
        # argparse echo argv or its diagnostic into an agent transcript.
        raise ValueError()


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--base-path', default='')
    parser.add_argument('--credential-file')
    parser.add_argument('operation')
    parser.add_argument('schema_operation', nargs='?')
    parser.add_argument('--path')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        args = parser.parse_args(argv)
        origin_host(args.origin)
        if re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', args.base_path) is None: raise ValueError()
        if args.operation == 'schema':
            if args.path or (args.schema_operation is not None and args.schema_operation not in OPERATIONS): raise ValueError()
            status, result = exchange(args.origin, args.base_path + '/api/research/client/operations.json')
            if 200 <= status < 300 and args.schema_operation: result = result[args.schema_operation]
        elif args.operation == 'public':
            if args.schema_operation or not args.path or not re.fullmatch(r'/api/public/(?:documents|releases|posts|post-tags)(?:/[A-Za-z0-9_-]+)*', args.path): raise ValueError()
            status, result = exchange(args.origin, args.base_path + args.path,
                token=read_token(args.credential_file) if args.credential_file else None)
        else:
            if args.operation not in OPERATIONS or args.schema_operation or args.path or not args.credential_file: raise ValueError()
            data = sys.stdin.buffer.read(1_000_001)
            if len(data) > 1_000_000: raise ValueError()
            body = json.loads(data, object_pairs_hook=unique)
            if not isinstance(body, dict) or safe(body) != body: raise ValueError()
            status, result = exchange(args.origin, args.base_path + '/api/research/agent/v1/' + args.operation,
                                      token=read_token(args.credential_file), body=body)
        print(json.dumps(result, ensure_ascii=True))
        return 0 if 200 <= status < 300 else 1
    except Exception:
        print(json.dumps({'error': {'code': 'research_api_unavailable',
            'message': 'Request did not confirm success; check history before retrying'}}))
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    sys.exit(main())
