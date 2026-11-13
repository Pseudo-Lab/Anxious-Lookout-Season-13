"""#62 routing fixture: the real public-listener gate, switch flag on, behind real Traefik.

check-public-routes.sh routes a test-only Host here to prove the second layer: even if a route
forwarded the /api/internal prefix, the public listener refuses it, because Traefik's own proxy
headers mark the request as proxied and uvicorn's path decoding is the one the gate sees.
"""
import json

import uvicorn

from app import serve


async def echo(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return
    body = json.dumps({'service': 'gated-api', 'path': scope['path']}).encode()
    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
    await send({'type': 'http.response.body', 'body': body})


if __name__ == '__main__':
    config = uvicorn.Config(serve.gate(echo, '', True), access_log=False, proxy_headers=False, log_level='warning')
    uvicorn.Server(config).run(sockets=[serve.listener(serve.PUBLIC_PORT)])
