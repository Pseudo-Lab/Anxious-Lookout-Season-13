# #90 review 01d30ce counterexamples (messages/back/2026-11-14-review-issue90-01d30ce-snapshot-memory.md, "Reproducer"),
# added unchanged as regression tests on PM request; only this header is new.
import tracemalloc
import uuid
from runner import stream
from test_runner_stream import new_hub,start_item,note,SESSION

def test_snapshot_iteration_memory(monkeypatch):
    monkeypatch.setenv('RUNNER_STREAM_TEXT','true')
    hub,_=new_hub();hub.verified();start_item(hub)
    for _ in range(12):note(hub,'item/agentMessage/delta',itemId='msg-1',delta='a'*32768)
    hub.flush(hub.active,final=True)
    sub,_=hub.subscribe(SESSION,None)
    tracemalloc.start()
    for encoded in sub.pending:
        assert len(encoded) <= stream.SNAPSHOT_DATA_LIMIT+200
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    print('drain peak',peak)
    # Allow even four frames for encoding/transport work, not merely the promised one.
    assert peak <= 4*stream.SNAPSHOT_DATA_LIMIT, peak

def test_slow_views_account_evicted_turns(monkeypatch):
    monkeypatch.setenv('RUNNER_STREAM_TEXT','true')
    clock=[1000.0];monkeypatch.setattr(stream.time,'monotonic',lambda:clock[0])
    hub=stream.Hub(None,background=False)
    subs=[]
    for _ in range(6):
        session=str(uuid.uuid4());hub.begin(session,str(uuid.uuid4()));hub.bind(thread='thread-1',turn_id='turn-1');hub.verified()
        for n in range(120):
            clock[0]+=1;start_item(hub,str(n),'commandExecution',command='x'*1900);hub.flush(hub.active,final=False)
        start_item(hub,'msg-1')
        for n in range(16):
            clock[0]+=4;note(hub,'item/agentMessage/delta',itemId='msg-1',delta='a'*32768);hub.flush(hub.active,final=False)
        sub,_=hub.subscribe(session,None);subs.append(sub)
        hub.complete('idle','completed',None)
        hub.expire()
    # The saved views hold old text/summary objects alive after the Turn leaves previous.
    views=[sub.pending.gi_frame.f_locals['view'] for sub in subs if sub.pending is not None]
    payload=sum(sum(sum(stream.utf8(s) for s in entry[4])+len(stream.data_bytes(entry[3])) for entry in view) for view in views)
    print('views',len(views),'previous',len(hub.previous),'view payload bytes',payload)
    assert payload <= 2*stream.TURN_BUDGET, payload
