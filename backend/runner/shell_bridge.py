"""Fixed stdio MCP adapter. It can only ask the private broker; no local exec."""
import json
import os
import socket
import struct
import sys

MAX_REPLY = 8 * 1024 * 1024
TOOL = {'name': 'shell', 'description': 'Run Bash in the isolated workspace executor. cwd is workspace-relative. Commands have wall-time and output limits; background processes are terminated.',
        'inputSchema': {'type': 'object', 'properties': {'command': {'type': 'string', 'minLength': 1, 'maxLength': 16384},
            'cwd': {'type': 'string', 'default': '.'}}, 'required': ['command'], 'additionalProperties': False}}


def failure(code):
    return {'status': 'rejected', 'error': {'code': code}}


def exchange(path, request, uid, timeout):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(path)
        _, peer, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer != uid:
            raise ValueError('Unexpected peer')
        encoded = json.dumps(request, ensure_ascii=True).encode() + b'\n'
        if len(encoded) > 120000:
            raise ValueError('Oversized request')
        connection.sendall(encoded)
        with connection.makefile('rb') as stream:
            line = stream.readline(MAX_REPLY + 1)
        if len(line) > MAX_REPLY or not line.endswith(b'\n'):
            raise ValueError('Incomplete reply')
        result = json.loads(line)
        if type(result) is not dict:
            raise ValueError('Invalid reply')
        return result


def call(arguments):
    try:
        return exchange(os.environ['SHELL_BROKER_SOCKET'],
                        {'lease': os.environ['SHELL_BRIDGE_LEASE'], 'arguments': arguments}, 10001, 325)
    except Exception:
        return failure('shell_unavailable')


def main():
    for line in sys.stdin.buffer:
        if len(line) > 120000:
            return
        try:
            message = json.loads(line)
            if type(message) is not dict or 'id' not in message:
                continue
            method, params = message.get('method'), message.get('params', {})
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'isolated-shell', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [TOOL]}
            elif method == 'tools/call':
                output = call(params.get('arguments')) if params.get('name') == 'shell' else failure('tool_not_allowed')
                result = {'content': [{'type': 'text', 'text': json.dumps(output, ensure_ascii=True)}],
                          'isError': output.get('status') != 'completed'}
            elif method == 'ping':
                result = {}
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': message['id'],
                                  'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
        except Exception:
            return  # Never echo private protocol data on stderr/stdout.


if __name__ == '__main__':
    main()
