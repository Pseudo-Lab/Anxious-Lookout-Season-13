"""Id-addressed edits of a stored post-blocks document (#107).

Operations are applied in order to a copy of the base and the whole result
passes the same canonicalize as a full-document save, so a successful save
validates once. Only when that fails are the operations replayed one by one to
find the causing operation (#107 R4 supplement): the first operation after
which the document fails validation and never passes again. A failure that a
later operation repairs (a move written as insert-then-remove) is not a cause.
The error is the first error after the last passing state; its path is the
causing operation's argument position: /contentInput/operations/N/block/...
inside the block it sent, otherwise /contentInput/operations/N.

Inside the owner's graph lock apply() only raises PatchFailure (the final
document's first error at /contentInput/operations). The API layers call
refine() after the transaction ends; the replay is bounded by work, not time.
"""
from copy import deepcopy
import re
from .post_blocks import CHILDREN, canonicalize, encoded, invalid, pointer
from .research import ResearchError

ROOT = ('contentInput', 'operations')
LIMIT = 200
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
FIELDS = {'replace': ('id', 'block'), 'remove': ('id',)}
# Replay work bound (PM R4 supplement 3): the serialized bytes of every state cause() validates,
# summed; the replay stops before the state that would pass it. 1 MiB base -> base + 15 steps.
# Deterministic (no clock). A rule-limit-dense 0.8 MB base (10,000 marked runs) took about
# 2.5 s in the unit image to reach the bound (32 MiB took 3.4 s under the earlier estimate).
CAUSE_BUDGET = 16 * 1024 * 1024


def anchor(operation):
    return 'id' if operation['op'] != 'insert' else 'afterId' if 'afterId' in operation else 'beforeId'


def check(operations):
    """Operation shapes with argument paths; node shapes are checked after application."""
    if not isinstance(operations, list) or not operations: invalid(ROOT)
    if len(operations) > LIMIT: invalid(ROOT, 'content_limit_exceeded')
    # Bound nesting/work before deepcopy or recursive walks (same bounds as canonicalize).
    pending = [(operations, 0)]; objects = 0
    while pending:
        value, depth = pending.pop(); objects += 1
        if depth > 40 or objects > 100000: invalid(ROOT, 'content_limit_exceeded')
        if isinstance(value, dict): pending.extend((v, depth+1) for v in value.values())
        elif isinstance(value, list): pending.extend((v, depth+1) for v in value)
    for index, operation in enumerate(operations):
        path = (*ROOT, index)
        if not isinstance(operation, dict): invalid(path)
        kind = operation.get('op')
        if kind not in ('replace', 'insert', 'remove'): invalid((*path, 'op'))
        if kind == 'insert':
            anchors = [key for key in ('afterId', 'beforeId') if key in operation]
            if len(anchors) != 1: invalid(path)
            fields = (anchors[0], 'block')
        else:
            fields = FIELDS[kind]
        for key in operation:
            if key != 'op' and key not in fields: invalid((*path, key))
        for key in fields:
            if key == 'block':
                if not isinstance(operation.get(key), dict): invalid((*path, key))
            elif not isinstance(operation.get(key), str) or not UUID.fullmatch(operation[key]): invalid((*path, key))
    return operations


def find(document, target):
    pending = [document['blocks']]
    while pending:
        container = pending.pop()
        if not isinstance(container, list): continue
        for index, node in enumerate(container):
            if isinstance(node, dict):
                if node.get('id') == target: return container, index
                pending.extend(node.get(key) for key in CHILDREN)
    return None


def step(document, operation, index):
    """Apply one operation in place; return the block it put into the document, if any."""
    key = anchor(operation)
    found = find(document, operation[key])
    if found is None: invalid((*ROOT, index, key))
    container, position = found
    if operation['op'] == 'remove':
        container.pop(position)
        return None
    block = deepcopy(operation['block'])
    if operation['op'] == 'replace': container[position] = block
    else: container.insert(position + (key == 'afterId'), block)
    return block


def origins(block):
    """id() of every object inside the operation's block -> its path relative to `block`.

    Valid while the block stays in the document (ids are unique among live objects).
    """
    result, pending = {}, [(block, ())]
    while pending:
        value, relative = pending.pop()
        result[id(value)] = relative
        items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
        for key, child in items:
            if isinstance(child, (dict, list)): pending.append((child, (*relative, str(key))))
    return result


def parse(path):
    return [part.replace('~1', '/').replace('~0', '~') for part in path.split('/')[1:]] if path else []


def locate(document, parts, index, origin):
    """Path inside the causing operation's block when the error lies in it, else the operation."""
    current, best = document, None
    for depth in range(len(parts) + 1):
        if id(current) in origin: best = (*origin[id(current)], *parts[depth:])
        if depth == len(parts): break
        part = parts[depth]
        if isinstance(current, dict) and part in current: current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current): current = current[int(part)]
        else: break
    return (*ROOT, index) if best is None else (*ROOT, index, 'block', *best)


def duplicate(document, target, index, origin):
    """Point a repeated id at the copy the causing operation sent.

    canonicalize reports a repeated id at its second occurrence in document order,
    which may be the original node when the copy was inserted before it.
    """
    matches, pending = [], [document['blocks']]
    while pending:
        container = pending.pop()
        if not isinstance(container, list): continue
        for child in container:
            if not isinstance(child, dict): continue
            if child.get('id') == target: matches.append(child)
            pending.extend(child.get(key) for key in CHILDREN)
    sent = [child for child in matches if id(child) in origin]
    return (*ROOT, index, 'block', *origin[id(sent[0])], 'id') if len(matches) > 1 and sent else None


def report(document, index, origin, error):
    """Keep the first error's status, code and details; only its path moves to the operation."""
    found = error.details.get('path') if isinstance(error.details, dict) else None
    parts = parse(found) if isinstance(found, str) else None
    # Only a repeated id that canonicalize itself rejected (its marker), never any path ending in /id.
    target = getattr(error, 'duplicate_id', None)
    path = duplicate(document, target, index, origin) if isinstance(target, str) else None
    if path is None:
        path = locate(document, parts, index, origin) if parts is not None else (*ROOT, index)
    details = error.details if isinstance(error.details, dict) else {'code': error.code}
    error.details = {**details, 'path': pointer(path)}
    return error


def cause(base, operations):
    """Replay with per-step validation (failure path only) and return the causing error.

    Returns None once the serialized bytes of the validated states would pass
    CAUSE_BUDGET (checked before each validation, so the work is deterministic).
    """
    document, found, spent = deepcopy(base), None, 0
    def affordable():
        nonlocal spent
        spent += len(encoded(document))
        return spent <= CAUSE_BUDGET
    if not affordable(): return None
    try: canonicalize(document, ())
    except ResearchError as error:
        # An invalid base has no causing operation unless a later step passes first.
        error.details = {**(error.details or {}), 'path': pointer(ROOT)}
        found = error
    for index, operation in enumerate(operations):
        block = step(document, operation, index)
        if not affordable(): return None
        try:
            canonicalize(document, ())
            found = None  # passes again: earlier failures were transient
        except ResearchError as error:
            if found is None: found = report(document, index, origins(block) if block is not None else {}, error)
    return found


class PatchFailure(ResearchError):
    """The final document's first error, raised under the lock; refine() finds the cause later."""

    def __init__(self, error, base, operations):
        details = error.details if isinstance(error.details, dict) else {'code': error.code}
        super().__init__(error.code, error.message, error.status, {**details, 'path': pointer(ROOT)})
        self.replay = (base, operations)


def refine(failure):
    """Causing-operation error within CAUSE_BUDGET, else the final first error at /contentInput/operations."""
    base, operations = failure.replay
    return cause(base, operations) or failure


def apply(base, operations):
    check(operations)
    document = deepcopy(base)
    for index, operation in enumerate(operations): step(document, operation, index)
    try:
        return canonicalize(document, ())
    except ResearchError as error:
        failure = PatchFailure(error, base, operations)
    raise failure
