"""Fixed isolated stdio adapter; bootstrap once, then forward original frames.

Launched with python -I -B. No imports from model-writable locations, fork/exec,
descriptor handoff, context relabeling, callback URL or API credential handling.
"""
import json
import math
import os
import socket
import stat
import sys

LIMIT = 4 * 1024 * 1024


def pairs(entries):
    value = {}
    for key, item in entries:
        if key in value:
            raise ValueError('duplicate_key')
        value[key] = item
    return value


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('number')
    return result


def reject(_):
    raise ValueError('number')


def read(stream):
    data = stream.readline(LIMIT + 1)
    if not data:
        raise EOFError()
    if len(data) > LIMIT or not data.endswith(b'\n'):
        raise ValueError('message')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=reject, parse_float=finite)


def write(stream, value):
    data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode() + b'\n'
    if len(data) > LIMIT:
        raise ValueError('message')
    stream.write(data)
    stream.flush()


def bootstrap(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096):
            raise ValueError('bootstrap')
        value = json.loads(os.read(descriptor, 4097), object_pairs_hook=pairs)
    finally:
        os.close(descriptor)
    if (not isinstance(value, dict) or set(value) != {'socket', 'nonce'}
            or not isinstance(value['socket'], str) or not isinstance(value['nonce'], str)
            or len(value['nonce']) != 64 or not value['nonce'].isascii()):
        raise ValueError('bootstrap')
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.set_inheritable(False)
    connection.settimeout(25)
    try:
        connection.connect(value['socket'])
        stream = connection.makefile('rwb')
        write(stream, {'operation': 'register', 'nonce': value.pop('nonce')})
        if read(stream) != {'registered': True}:
            raise ValueError('registration')
        value.clear()  # No reusable grant, context or API token remains.
        return connection, stream
    except BaseException:
        connection.close()
        raise


def answer(message, stream):
    method = message.get('method')
    if method == 'initialize':
        return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'anxious-research', 'version': '2'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        write(stream, {'operation': 'catalog'})
        result = read(stream)
        if 'error' in result:
            raise ValueError('catalog')
        return result
    if method == 'tools/call':
        # The native _meta and original args pass unchanged. Never supply a
        # latest turn/request/capability or turn a JSON-RPC id into native callId.
        write(stream, {'operation': 'call', 'frame': message})
        result = read(stream)
        return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=True)}],
                'isError': 'error' in result, 'structuredContent': result}
    raise LookupError()


def serve(stream, source, destination):
    while True:
        try:
            message = read(source)
        except EOFError:
            return
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise ValueError('protocol')
        if 'id' not in message:
            continue
        try:
            result = answer(message, stream)
            response = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
        except LookupError:
            response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Method unavailable'}}
        write(destination, response)


if __name__ == '__main__':
    try:
        connection, stream = bootstrap(sys.argv[1])
        try:
            serve(stream, sys.stdin.buffer, sys.stdout.buffer)
        finally:
            stream.close()
            connection.close()
    except Exception:
        sys.exit(1)  # No private path, frame, nonce or exception text on stdio.
