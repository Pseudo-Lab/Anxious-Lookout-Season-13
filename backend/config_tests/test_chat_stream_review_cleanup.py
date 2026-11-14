# #90 review 7d6f247 counterexample (review: /tmp/issue90-a1/test_response_cleanup.py), one change only: the inline
# copy of the /events body is replaced by app.chat_stream.relay_body, the body the endpoint now returns (the fix lives
# there, so the inline copy could not see it). Everything else is unchanged.
import asyncio
import httpx
from app.chat_stream import Relay, relay_body
from runner.stream import StoppableStreamingResponse
from test_chat_stream_relay import R1, SESSION, deps, hello, sse

def test_blocked_api_body_closes_upstream_before_slot_release():
    async def check():
        closed=[]; release=[]; blocked=asyncio.Event(); stop=asyncio.Event()
        async def source():
            try:
                yield hello()
                for n in range(100):
                    yield sse('item.delta', {'requestId': R1, 'text':'x'}, R1+':'+str(n+1))
                await asyncio.sleep(30)
            finally: pass
        class Source(httpx.AsyncByteStream):
            def __init__(self): self.iterator = source()
            def __aiter__(self): return self.iterator
            async def aclose(self):
                closed.append(True)
                await self.iterator.aclose()
        def handler(request):
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=Source())
        dep,_=deps()
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        relay=Relay(SESSION,None,dep,client,shutdown=stop)
        # Exact shape of conversations.events body: an outer async generator.
        body = lambda: relay_body(relay, {'requestId':R1,'state':'running'}, lambda: release.append(bool(closed)))
        async def send(message):
            if message['type']=='http.response.body' and b'item.delta' in message.get('body',b''):
                blocked.set();await asyncio.Event().wait()
        async def receive(): await asyncio.Event().wait()
        response=StoppableStreamingResponse(body(),stop)
        task=asyncio.create_task(response({'type':'http','state':{},'asgi':{'spec_version':'2.4'}},receive,send))
        await asyncio.wait_for(blocked.wait(),2);stop.set()
        await asyncio.wait_for(task,2)
        print('upstream closed at response return',closed,'at release',release)
        assert closed == [True], 'reader remains alive when response returns'
        await client.aclose()
    asyncio.run(check())


def test_upstream_closed_before_slot_release():
    """back's addition: the release (finished) sees the upstream already closed."""
    async def check():
        closed, release, blocked, stop = [], [], asyncio.Event(), asyncio.Event()

        async def source():
            yield hello()
            for n in range(100):
                yield sse('item.delta', {'requestId': R1, 'text': 'x'}, R1 + ':' + str(n + 1))
            await asyncio.sleep(30)

        class Source(httpx.AsyncByteStream):
            def __init__(self):
                self.iterator = source()

            def __aiter__(self):
                return self.iterator

            async def aclose(self):
                closed.append(True)
                await self.iterator.aclose()
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Source())))
        dep, _ = deps()
        relay = Relay(SESSION, None, dep, client, shutdown=stop)

        async def send(message):
            if message['type'] == 'http.response.body' and b'item.delta' in message.get('body', b''):
                blocked.set()
                await asyncio.Event().wait()

        async def receive():
            await asyncio.Event().wait()
        response = StoppableStreamingResponse(
            relay_body(relay, {'requestId': R1, 'state': 'running'}, lambda: release.append(bool(closed))), stop)
        task = asyncio.create_task(response({'type': 'http', 'state': {}, 'asgi': {'spec_version': '2.4'}}, receive, send))
        await asyncio.wait_for(blocked.wait(), 2)
        stop.set()
        await asyncio.wait_for(task, 2)
        await client.aclose()
        return closed, release
    assert asyncio.run(check()) == ([True], [True])


# --- #90 D3-d: the summary reason of a stream closed from outside ---------------------------------------------------

def _closed_body(stop_first):
    """A relay body closed from outside (aclose = the browser left, or the response stopped on shutdown)."""
    async def check():
        async def source():
            yield hello()
            await asyncio.sleep(30)
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=source())))
        dep, _ = deps()
        stop = asyncio.Event()
        stats, release = {}, []
        relay = Relay(SESSION, None, dep, client, stats, shutdown=stop)
        body = relay_body(relay, {'requestId': R1, 'state': 'running'}, lambda: release.append(stats.get('reason')))
        await body.__anext__()  # the first frame (runner status)
        if stop_first:
            stop.set()
        await body.aclose()
        await client.aclose()
        return stats.get('reason'), release
    return asyncio.run(check())


def test_a_client_close_is_recorded_as_client_closed():
    assert _closed_body(False) == ('client_closed', ['client_closed'])


def test_a_close_during_the_api_shutdown_is_recorded_as_shutdown():
    assert _closed_body(True) == ('shutdown', ['shutdown'])


def test_a_reason_set_by_the_relay_is_kept():
    async def check():
        dep, _ = deps()
        stats = {'reason': 'mismatch'}
        relay = Relay(SESSION, None, dep, httpx.AsyncClient(), stats)
        body = relay_body(relay, {'requestId': R1, 'state': 'running'}, lambda: None)
        await body.__anext__()
        await body.aclose()
        return stats['reason']
    assert asyncio.run(check()) == 'mismatch'
