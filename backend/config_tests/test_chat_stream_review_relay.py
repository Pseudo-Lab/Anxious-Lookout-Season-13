# #90 review d319fcb counterexamples (review: /tmp/issue90-d319/test_relay_edges.py), added unchanged as regression
# tests on PM request; only this header is new.
import asyncio
from app import chat_stream
from test_chat_stream_relay import Relay, SESSION, R1, R2, hello, sse, deps, upstreams, run, datas

def test_launch_deadline_with_periodic_idle(monkeypatch):
    monkeypatch.setattr(chat_stream, 'HEARTBEAT_SECONDS', .2)
    monkeypatch.setattr(chat_stream, 'LAUNCH_WAIT_SECONDS', .04)
    dep, _ = deps()
    body = [hello(activeRequestId=None, lastRequestId=R2)]
    for _ in range(100):
        body += [.01, sse('idle', {'requestId': R2, 'turnState': 'completed'})]
    relay = Relay(SESSION, None, dep, upstreams(*([body] * 6)))
    chunks = run(relay, {'requestId': R1, 'state': 'running'}, limit=200, seconds=.6)
    assert datas(chunks, 'sync') == [{'reason': 'mismatch'}], relay.stats

def test_status_changes_on_open_connection(monkeypatch):
    monkeypatch.setattr(chat_stream, 'HEARTBEAT_SECONDS', .02)
    monkeypatch.setattr(chat_stream, 'REVALIDATE_SECONDS', .03)
    state = {'available': True, 'reason': None}
    dep, calls = deps(rows=[{'requestId': R1, 'state': 'idle'}], status=lambda: dict(state))
    async def check():
        relay = Relay(SESSION, None, dep, upstreams([hello(), 2.0]))
        chunks = []
        async def consume():
            async for item in relay.events({'requestId': R1, 'state': 'idle'}):
                chunks.append(item)
                state.update(available=False, reason='unavailable')
        try:
            await asyncio.wait_for(consume(), .2)
        except asyncio.TimeoutError:
            pass
        assert calls['row'] >= 2
        assert any(not item['available'] for item in datas(chunks, 'runner')), datas(chunks, 'runner')
    asyncio.run(check())
