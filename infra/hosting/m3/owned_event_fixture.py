"""Investigation-only ingress gate; not imported by production runner.

Bindings/epochs are trusted fixture inputs, never browser fields. One active turn.
Consume a tool identity before callback: unknown failures cannot be retried.
"""
from copy import deepcopy


class OwnedEvents:
    def __init__(self, thread, session, request, token, epoch, tools):
        self.turn = dict(thread=thread, session=session, request=request, token=token,
                         turnId=None, pendingTools=[])
        self.epoch = epoch
        self.tools = frozenset(tools)
        self.live = True
        self.seen_calls, self.seen_ids = set(), set()
        self.pending = []

    def start_response(self, epoch, response):
        if epoch != self.epoch or not self.live or self.turn['turnId'] is not None:
            raise ValueError('Stale admission')
        identity = response.get('turn', {}).get('id')
        if not isinstance(identity, str) or not identity:
            raise ValueError('Missing correlated turn')
        self.turn['turnId'] = identity
        pending, self.pending = self.pending, []
        return [v for message in pending if (v := self.accept(epoch, message)) is not None]

    def accept(self, epoch, message):
        if epoch != self.epoch or not self.live:
            return None
        method, params = message.get('method'), message.get('params', {})
        if not isinstance(params, dict):
            return None
        if params.get('threadId') != self.turn['thread']:
            return None  # Before reroute/model/error/storage/tool processing.
        if method in {'turn/started', 'turn/updated', 'turn/completed'}:
            # Native.receive reads nested turn.id. Never prefer an extra flat ID.
            if 'turnId' in params or not isinstance(params.get('turn'), dict):
                return None
            identity = params['turn'].get('id')
        elif method in {'item/tool/call', 'model/rerouted'}:
            if 'turn' in params:
                return None
            identity = params.get('turnId')
        else:
            return None
        if not isinstance(identity, str) or not identity:
            return None
        if self.turn['turnId'] is None:
            if method == 'item/tool/call' and len(self.pending) < 32:
                self.pending.append(deepcopy(message))
            return None
        if identity != self.turn['turnId']:
            return None
        if method == 'item/tool/call':
            call, rpc = params.get('callId'), message.get('id')
            if not isinstance(call, str) or not call or type(rpc) not in (str, int):
                return None
            if params.get('tool') not in self.tools:
                return None
            rpc_key = (type(rpc), rpc)
            if call in self.seen_calls or rpc_key in self.seen_ids:
                return None
            self.seen_calls.add(call)
            self.seen_ids.add(rpc_key)
        elif method not in {'turn/started', 'turn/updated', 'turn/completed', 'model/rerouted'}:
            return None
        if method == 'turn/completed':
            self.live = False
            self.pending.clear()
        return deepcopy(message)

    def disconnect(self):
        self.live = False
        self.pending.clear()
