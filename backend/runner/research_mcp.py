"""Image-owned stdio MCP client. Its grant and socket are outside the shell sandbox.

Standalone stdlib script: Codex launches it with python -I; no user imports,
config, inherited API credentials, endpoints or executable choices.
"""
import json
import os
import socket
import stat
import sys

MAX_MESSAGE = 4 * 1024 * 1024
PROTOCOL = '2024-11-05'


def read_message(stream):
    line = stream.readline(MAX_MESSAGE + 1)
    if not line:
        raise EOFError()
    if len(line) > MAX_MESSAGE or not line.endswith(b'\n'):
        raise ValueError('message_limit')
    return json.loads(line)


def write_message(stream, value):
    data = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode() + b'\n'
    if len(data) > MAX_MESSAGE:
        raise ValueError('message_limit')
    stream.write(data)
    stream.flush()


def read_grant(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096:
            raise ValueError('grant')
        data = os.read(fd, 4097)
    finally:
        os.close(fd)
    value = json.loads(data)
    if (set(value) != {'socket', 'capability'} or not isinstance(value['socket'], str)
            or not isinstance(value['capability'], str) or len(value['capability']) != 64):
        raise ValueError('grant')
    return value


def exchange(grant, operation, **fields):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(25)
        connection.connect(grant['socket'])
        with connection.makefile('rwb') as stream:
            write_message(stream, {'capability': grant['capability'], 'operation': operation, **fields})
            return read_message(stream)


def answer(message, grant):
    method, params = message.get('method'), message.get('params') or {}
    if method == 'initialize':
        return {'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'anxious-research', 'version': '1'}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        result = exchange(grant, 'catalog')
        if 'error' in result:
            raise ValueError('catalog')
        return {'tools': result['tools']}
    if method == 'tools/call':
        result = exchange(grant, 'call', callId=str(message['id']),
                          name=params.get('name'), arguments=params.get('arguments', {}))
        return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=True)}],
                'isError': 'error' in result, 'structuredContent': result}
    raise LookupError()


def serve(grant, source, destination):
    while True:
        try:
            message = read_message(source)
        except EOFError:
            return
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise ValueError('protocol')
        if 'id' not in message:
            continue
        try:
            result = answer(message, grant)
            response = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
        except LookupError:
            response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Method unavailable'}}
        except Exception:
            response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32000, 'message': 'Research operation unavailable'}}
        write_message(destination, response)


if __name__ == '__main__':
    try:
        serve(read_grant(sys.argv[1]), sys.stdin.buffer, sys.stdout.buffer)
    except Exception:
        sys.exit(1)  # No credential/path/exception text on stdio or stderr.
