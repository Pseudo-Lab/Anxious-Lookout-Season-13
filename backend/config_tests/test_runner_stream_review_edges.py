# #90 review stage-1 counterexamples (review: /tmp/issue90-stage1/test_review_edges.py), added unchanged as regression
# tests on PM request; only this header is new.
import os
import uuid
from test_runner_stream import new_hub, start_item, note, events, FakeAuth, SESSION
from test_runner_stream_app import bare_native, scripted_turn
from runner.stream import Hub, TURN_BUDGET

def test_previous_turns_are_globally_bounded(monkeypatch):
    monkeypatch.setenv('RUNNER_STREAM_TEXT','false')
    hub=Hub(None,background=False)
    for i in range(4):
        hub.begin(str(uuid.uuid4()),str(uuid.uuid4()))
        hub.complete('idle','completed',None)
    assert len(hub.previous) <= 1, f'previous turns retained: {len(hub.previous)}'

def test_raw_command_is_charged_before_retention(monkeypatch):
    monkeypatch.setenv('RUNNER_STREAM_TEXT','false')
    hub,_=new_hub()
    hub.verified()
    start_item(hub,'cmd','commandExecution',command='x'*(TURN_BUDGET+1))
    item=hub.active.items['cmd']
    assert hub.active.stopped == 'budget', f'retained reports {hub.active.retained()}, raw command length {len(item.summary_raw)}'

def test_final_summary_generation_change_is_fail_closed(monkeypatch):
    monkeypatch.setenv('RUNNER_STREAM_TEXT','true')
    auth=FakeAuth()
    calls=0
    original=auth.refresh_projection_secrets
    def rotate():
        nonlocal calls
        calls+=1
        if calls==3: auth.secrets.add('NEW_SECRET')
        original()
    auth.refresh_projection_secrets=rotate
    hub,_=new_hub(auth)
    hub.verified()
    start_item(hub,'cmd','commandExecution',command='echo NEW_SECRET')
    hub.flush(hub.active,final=True)
    data=b''.join(f for _,f in hub.active.events)
    assert b'NEW_SECRET' not in data, data

def test_unverified_snapshot_hides_output(monkeypatch):
    monkeypatch.setenv('RUNNER_STREAM_TEXT','true')
    hub,_=new_hub()
    start_item(hub,'cmd','commandExecution',command='echo unverified-command')
    note(hub,'item/commandExecution/outputDelta',itemId='cmd',delta='unverified-output')
    hub.flush(hub.active,final=False)
    _,frames=hub.subscribe(SESSION,None)
    assert b'unverified-output' not in b''.join(frames), b''.join(frames)

def test_completed_turn_does_not_finish_next_admission(monkeypatch):
    native=bare_native(monkeypatch=monkeypatch)
    original=native.stream.complete
    def admit_next_then_complete(*args):
        assert not native.lock.locked()
        native.stream.begin('next-session','next-request')
        original(*args)
    native.stream.complete=admit_next_then_complete
    scripted_turn(native,monkeypatch,'completed',[])
    assert native.stream.active is not None, 'old completion cleared the newly admitted turn'
