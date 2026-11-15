# #90 review c894465 counterexample (review: /tmp/issue90-stage1/test_snapshot_budget.py), added unchanged as a
# regression test on PM request; only this header is new.
from test_runner_stream import new_hub,start_item,note,SESSION
from runner import stream
from runner.stream import TURN_BUDGET

def test_snapshot_counts_towards_budget(monkeypatch):
    monkeypatch.setenv('RUNNER_STREAM_TEXT','true')
    clock=[1000.0]
    monkeypatch.setattr(stream.time,'monotonic',lambda:clock[0])
    hub,_=new_hub();hub.verified()
    for n in range(120):
        clock[0]+=1
        start_item(hub,str(n),'commandExecution',command='x'*1900)
        hub.flush(hub.active,final=False)
    start_item(hub,'msg-1')
    for n in range(16):
        clock[0]+=4
        note(hub,'item/agentMessage/delta',itemId='msg-1',delta='a'*32768)
        hub.flush(hub.active,final=False)
    before=hub.active.retained()
    _,frames=hub.subscribe(SESSION,None)
    after=hub.active.retained()
    assert after<=TURN_BUDGET, f'before={before} after={after} budget={TURN_BUDGET} stopped={hub.active.stopped}'
