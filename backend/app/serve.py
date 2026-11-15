"""API process entry (#62): one app, two listeners.

8080 is the public listener (Traefik only, by policy). 8081 is the internal listener for the
runner tool callback (runner Pods only, by policy) and serves nothing else. The public listener
does not serve the /api/internal prefix unless API_INTERNAL_CALLBACK_ON_PUBLIC=true, which exists
only for the switch of runner Pods created before #62 (their callback env is the 8080 URL).
Even then a request that carries any proxy header is refused: Traefik always adds
X-Forwarded-For, X-Real-Ip and X-Forwarded-Server (measured with the pinned Traefik 3.7.8), and a
runner callback sends none. The prefix has no trailing slash because uvicorn decodes %2F, %5C and
%00 into the path that the app routes on, while Traefik matches them undecoded (#62 review).

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
# Any of these means the request came through a proxy (Traefik), never from a runner Pod.
PROXY_HEADERS = {b'forwarded', b'x-forwarded-for', b'x-forwarded-host', b'x-forwarded-port',
                 b'x-forwarded-proto', b'x-forwarded-server', b'x-real-ip'}


def public_internal():
    value = os.environ.get(FLAG, 'false')
    if value not in {'true', 'false'}:
        raise RuntimeError(FLAG + ' must be true or false')
    return value == 'true'


def allowed(port, path, base_path, legacy_public, proxied):
    """Whether a request may reach the app on the listener with this local port."""
    internal = base_path + '/api/internal'
    callback = internal + '/research/tools'
    if port == INTERNAL_PORT:
        return path == callback
    if port == PUBLIC_PORT:
        if path.startswith(internal):
            return legacy_public and not proxied and path == callback
        return True
    return False


def gate(app, base_path, legacy_public):
    async def gated(scope, receive, send):
        kind = scope['type']
        if kind in {'http', 'websocket'}:
            server = scope.get('server') or (None, None)
            # uvicorn sets server from the accepted socket's local address, not from headers.
            proxied = any(name.lower() in PROXY_HEADERS for name, _ in scope.get('headers', ()))
            if not allowed(server[1], scope['path'], base_path, legacy_public, proxied):
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


def stream_aware_h11():
    """#90: uvicorn's h11 protocol, except that at server shutdown a connection whose response is an event stream
    (marked by runner.stream.StoppableStreamingResponse) is aborted rather than closed. close() flushes the write
    buffer first, so a browser that stopped reading would hold the shutdown until SIGKILL. Every other connection
    shuts down exactly as before (uvicorn already picks h11 here: httptools is not installed)."""
    from uvicorn.protocols.http.h11_impl import H11Protocol
    from runner.stream import STREAM_ABORT_MARK

    class StreamAwareH11(H11Protocol):
        def shutdown(self):
            cycle = self.cycle
            state = cycle.scope.get('state') if cycle is not None else None
            if cycle is not None and not cycle.response_complete and isinstance(state, dict) and state.get(STREAM_ABORT_MARK):
                self.transport.abort()
                return
            super().shutdown()
    return StreamAwareH11


def main():
    import uvicorn
    from .main import create_app
    from .settings import Settings
    legacy_public = public_internal()
    settings = Settings.load()
    app = gate(create_app(settings), settings.base_path, legacy_public)
    config = uvicorn.Config(app, workers=1, access_log=False, proxy_headers=False, http=stream_aware_h11())
    uvicorn.Server(config).run(sockets=[listener(PUBLIC_PORT), listener(INTERNAL_PORT)])


if __name__ == '__main__':
    main()


def __getattr__(name):
    # `uvicorn --http app.serve:StreamAwareH11` (the #90 shutdown probe) imports the same class main() uses.
    if name == 'StreamAwareH11':
        return stream_aware_h11()
    raise AttributeError(name)
