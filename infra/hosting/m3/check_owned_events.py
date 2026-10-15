"""Docker-only synthetic ingress tests using existing Native.reply_tool."""
import json
import queue
import httpx
from runner.app import Native
from owned_event_fixture import OwnedEvents
from stock_wire import Client

calls, replies, effects = [], [], []
original_client = httpx.Client


def callback(request):
    calls.append((request.headers['Authorization'], json.loads(request.content)))
    return httpx.Response(200, json={'saved': True})


httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(callback), **kwargs)
native = Native.__new__(Native)
native.auth, native.blocked_reason, native.completed = None, None, {}
native.messages = queue.Queue()
native.block_model = lambda: effects.append('model-block')
native.callback_url, native.send = 'https://fixture.invalid/tools', replies.append
gate = OwnedEvents('web-thread', 'web-session', 'web-request', 'web-token', 2, ['save'])


def tool(thread='web-thread', turn='web-turn', call='one', rpc=7):
    return dict(id=rpc, method='item/tool/call', params=dict(threadId=thread, turnId=turn,
                callId=call, tool='save', arguments={'content': 'synthetic'}))


def deliver(message, epoch=2):
    accepted = gate.accept(epoch, message)
    if accepted:
        if accepted['method'] == 'item/tool/call':
            native.reply_tool(accepted, gate.turn)
        else:
            native.messages.put(accepted)
            native.receive(gate.turn)
            effects.append(accepted['method'])


# Server method+id is not a response to same numeric client RPC id.
wire = Client.__new__(Client)
wire.next_id, wire.events, wire.errors, wire.send = 7, [], [], lambda value: None
wire_inbox = queue.Queue()
wire_inbox.put(tool(rpc=7))
wire_inbox.put({'id': 7, 'result': {'turn': {'id': 'web-turn'}}})
wire.receive = lambda: wire_inbox.get_nowait()
assert wire.rpc('turn/start', {}) == {'turn': {'id': 'web-turn'}}
assert wire.events == [tool(rpc=7)]
deliver(tool())
assert not calls
deliver(tool(thread='original-thread', call='foreign'))
for accepted in gate.start_response(2, {'turn': {'id': 'web-turn'}}):
    native.reply_tool(accepted, gate.turn)
assert calls == [('Bearer web-token', {'sessionId': 'web-session', 'requestId': 'web-request',
                                     'name': 'save', 'arguments': {'content': 'synthetic'}})]
deliver(tool())  # Exact duplicate.
deliver(tool(call='one', rpc=8))  # Same call, different server request id.
deliver(tool(call='two', rpc=7))  # Same server id, different call.
deliver(tool(call='prior-epoch', rpc=9), epoch=1)
deliver(tool(turn='previous-turn', call='old-turn', rpc=10))
for method in ('model/rerouted', 'turn/completed', 'turn/updated'):
    deliver({'method': method, 'params': {'threadId': 'original-thread', 'turnId': 'original-turn',
                                        'turn': {'id': 'original-turn', 'model': 'wrong'}}})
assert len(calls) == len(replies) == 1 and not effects and gate.live
invalid = [
    {'turnId': 'web-turn', 'turn': {'id': 'previous-turn', 'model': 'wrong'}},
    {'turnId': 'web-turn'}, {'turn': None}, {'turn': 'web-turn'},
    {'turn': {}}, {'turn': {'id': ''}}, {'turn': {'id': 7}},
    {'turn': {'id': True}}, {'turn': {'id': ['web-turn']}},
    {'turn': {'id': 'previous-turn', 'model': 'wrong'}},
    {'turnId': 'web-turn', 'turn': {'id': 'web-turn'}},
]
for method in ('turn/started', 'turn/updated', 'turn/completed'):
    for params in invalid:
        deliver({'method': method, 'params': {'threadId': 'web-thread', **params}})
for method in ('item/tool/call', 'model/rerouted'):
    for params in ({'turnId': 'web-turn', 'turn': {'id': 'previous-turn'}},
                   {'turnId': ''}, {'turnId': 7}, {'turnId': True}, {}):
        deliver({'id': 19, 'method': method, 'params': {'threadId': 'web-thread', **params}})
assert len(calls) == len(replies) == 1 and not effects and gate.live and not native.completed
deliver({'method': 'turn/started', 'params': {'threadId': 'web-thread', 'turn': {'id': 'web-turn'}}})
deliver({'method': 'turn/updated', 'params': {'threadId': 'web-thread', 'turn': {'id': 'web-turn'}}})
assert effects == ['turn/started', 'turn/updated']
deliver({'method': 'turn/completed', 'params': {'threadId': 'web-thread', 'turn': {'id': 'web-turn'}}})
deliver(tool(call='late', rpc=11))
assert len(calls) == len(replies) == 1 and effects == ['turn/started', 'turn/updated', 'turn/completed']
assert native.completed[('web-thread', 'web-turn')]['id'] == 'web-turn'
gate.disconnect()
deliver(tool(call='disconnected', rpc=12))
new = OwnedEvents('other-thread', 'other-session', 'other-request', 'other-token', 3, ['save'])
new.start_response(3, {'turn': {'id': 'other-turn'}})
assert new.accept(2, tool(thread='other-thread', turn='other-turn')) is None
assert new.accept(3, tool()) is None  # Foreign message against a live new turn.
message = new.accept(3, tool(thread='other-thread', turn='other-turn'))
native.reply_tool(message, new.turn)
assert calls[-1][0] == 'Bearer other-token' and calls[-1][1]['sessionId'] == 'other-session'
assert len(calls) == len(replies) == 2 and native.blocked_reason is None
# Unknown callback outcome consumes the call; no automatic storage replay.
def uncertain(request):
    raise httpx.ReadTimeout('Synthetic unknown storage outcome')
httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(uncertain), **kwargs)
message = tool(thread='other-thread', turn='other-turn', call='unknown', rpc=8)
native.reply_tool(new.accept(3, message), new.turn)
assert replies[-1]['result']['success'] is False and new.accept(3, message) is None
assert len(calls) == 2 and len(replies) == 3
print(json.dumps(dict(synthetic=True, callbacks=2, exactSessionAuthority=True,
                     foreignEffects=0, staleCallbacks=0, duplicateCallbacks=0,
                     conflictingIdentityAccepted=False, previousTurnModelBlock=False,
                     unknownOutcomeReplay=False, productionGatewayImplemented=False)))
