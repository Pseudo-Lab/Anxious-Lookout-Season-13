"""API process entry (#62): one app, two listeners.

8080 is the public listener (Traefik only, by policy). 8081 is the internal listener for the
runner tool callback (runner Pods only, by policy) and serves nothing else. The public listener
does not serve /api/internal/ unless API_INTERNAL_CALLBACK_ON_PUBLIC=true, which exists only for
the switch of runner Pods created before #62 (their callback env is the 8080 URL).

Both listeners keep uvicorn's --no-proxy-headers behaviour. Plain `uvicorn app.main:create_app`
(compose stacks, tests) is unchanged: the gate below applies only to this entry.
"""
import json
import os
import socket

PUBLIC_PORT = 8080
INTERNAL_PORT = 8081
FLAG = 'API_INTERNAL_CALLBACK_ON_PUBLIC'
NOT_FOUND = json.dumps({'error': {'code': 'not_found', 'message': 'Not found'}}).encode()


def public_internal():
    value = os.environ.get(FLAG, 'false')
    if value not in {'true', 'false'}:
        raise RuntimeError(FLAG + ' must be true or false')
    return value == 'true'


def allowed(port, path, base_path, legacy_public):
    """Whether a request path may reach the app on the listener with this local port."""
    internal = base_path + '/api/internal'
    callback = internal + '/research/tools'
    if port == INTERNAL_PORT:
        return path == callback
    if port == PUBLIC_PORT:
        return legacy_public or not (path == internal or path.startswith(internal + '/'))
    return False


def gate(app, base_path, legacy_public):
    async def gated(scope, receive, send):
        kind = scope['type']
        if kind in {'http', 'websocket'}:
            server = scope.get('server') or (None, None)
            # uvicorn sets server from the accepted socket's local address, not from headers.
            if not allowed(server[1], scope['path'], base_path, legacy_public):
                if kind == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                    return
                await send({'type': 'http.response.start', 'status': 404, 'headers': [
                    (b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                    (b'content-length', str(len(NOT_FOUND)).encode())]})
                await send({'type': 'http.response.body', 'body': NOT_FOUND})
                return
        await app(scope, receive, send)
    return gated


def listener(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', port))
    sock.listen(2048)
    sock.set_inheritable(True)
    return sock


def main():
    import uvicorn
    from .main import create_app
    from .settings import Settings
    legacy_public = public_internal()
    settings = Settings.load()
    app = gate(create_app(settings), settings.base_path, legacy_public)
    config = uvicorn.Config(app, workers=1, access_log=False, proxy_headers=False)
    uvicorn.Server(config).run(sockets=[listener(PUBLIC_PORT), listener(INTERNAL_PORT)])


if __name__ == '__main__':
    main()
