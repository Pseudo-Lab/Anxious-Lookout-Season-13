# #90 review 8debb4f shutdown probe (review: /tmp/issue90-8de/shutdown_probe.py), two changes only: the relay is
# wrapped in StoppableStreamingResponse (the response class the real /events endpoint returns), and uvicorn runs with
# --http app.serve:StreamAwareH11 (the protocol app.serve.main() uses). A body generator suspended in a blocked ASGI
# send cannot stop itself, and uvicorn's close() flushes to a reader that never reads. Run by test_stream_shutdown_probe.py.
import asyncio, os, signal, socket, subprocess, sys, time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from starlette.responses import StreamingResponse
from app.chat_stream import Relay, install_shutdown
from runner.stream import StoppableStreamingResponse  # the response class /events uses (only change, see header)
from test_chat_stream_relay import deps, upstreams, hello, sse, SESSION, R1

@asynccontextmanager
async def lifespan(app):
    app.state.stop = asyncio.Event()
    restore = install_shutdown(app.state.stop, asyncio.get_running_loop())
    yield
    restore()
app = FastAPI(lifespan=lifespan)
@app.get('/')
async def events():
    dep, _ = deps(rows=[{'requestId': R1, 'state': 'idle'}])
    parts = [hello()]
    if os.environ['PROBE_MODE'] == 'slow':
        # Less than the runner's 6 MiB connection cap; each frame is < 72 KiB.
        parts += [sse('item.delta', {'requestId': R1, 'itemId': 'm', 'text': 'x'*7000}, R1+':'+str(i)) for i in range(1,701)]
    parts += [30.0]
    relay = Relay(SESSION, None, dep, upstreams(parts), shutdown=app.state.stop)
    return StoppableStreamingResponse(relay.events({'requestId': R1, 'state': 'idle'}), app.state.stop, media_type='text/event-stream')

if __name__ == '__main__':
    for mode in ['idle','slow']:
        env = {**os.environ, 'PROBE_MODE': mode}
        with open('/tmp/uvicorn-'+mode+'.log', 'w') as log:
            p = subprocess.Popen([sys.executable,'-m','uvicorn','shutdown_probe:app','--host','127.0.0.1','--port','18765','--no-access-log','--http','app.serve:StreamAwareH11'],env=env,stdout=log,stderr=log)
            sock = None
            try:
                for _ in range(100):
                    try:
                        sock=socket.socket(); sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024);sock.connect(('127.0.0.1',18765));break
                    except OSError:
                        sock.close();sock=None;time.sleep(.05)
                assert sock is not None
                sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
                sock.settimeout(3); assert b'200 OK' in sock.recv(1024)
                time.sleep(1)
                started=time.monotonic();p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=3)
                    print(mode,'exit',p.returncode,'seconds',time.monotonic()-started,flush=True)
                except subprocess.TimeoutExpired:
                    print(mode,'HUNG after 3 seconds with unread socket',flush=True)
                    sock.close();sock=None
                    p.wait(timeout=3)
                    print(mode,'exited after client disconnect',p.returncode,flush=True)
            finally:
                if sock is not None:sock.close()
                if p.poll() is None:p.kill();p.wait()
            print(open('/tmp/uvicorn-'+mode+'.log').read(),flush=True)
