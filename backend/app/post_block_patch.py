"""Id-addressed edits of a stored post-blocks document (#107).

Operations are applied in order to a copy of the base. The whole result then
passes the same canonicalize as a full-document save; an error path is mapped
back to the operation that introduced the failing node (or emptied/filled the
failing container), so an agent can fix the operation it sent.
"""
from copy import deepcopy
import re
from .post_blocks import CHILDREN, canonicalize, invalid, pointer
from .research import ResearchError

ROOT = ('contentInput', 'operations')
LIMIT = 200
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
FIELDS = {'replace': ('id', 'block'), 'remove': ('id',)}


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


def parse(path):
    return [part.replace('~1', '/').replace('~0', '~') for part in path.split('/')[1:]] if path else []


def touched(document, marks):
    """id(node) -> latest operation that changed anything inside that node (itself included)."""
    result = {}
    def walk(value):
        latest = marks.get(id(value), -1)
        children = value if isinstance(value, list) else [value.get(key) for key in CHILDREN] if isinstance(value, dict) else []
        for child in children:
            if isinstance(child, (dict, list)): latest = max(latest, walk(child))
        if isinstance(value, dict) and 'id' in value and latest >= 0: result[id(value)] = latest
        return latest
    walk(document['blocks'])
    return result


def locate(document, parts, marks):
    """Map a result-document path to the deepest operation-owned object on it.

    Errors on untouched paths that depend on a sibling (row width, columnWidths)
    go to the latest operation that changed inside the deepest enclosing node.
    """
    current, best, nodes, within = document, None, [], None
    for depth in range(len(parts) + 1):
        if id(current) in marks:
            best = (marks[id(current)], depth, isinstance(current, dict))
        if isinstance(current, dict) and 'id' in current: nodes.append(current)
        if depth == len(parts): break
        part = parts[depth]
        if isinstance(current, dict) and part in current: current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current): current = current[int(part)]
        else: break
    if best is None:
        inside = touched(document, marks)
        within = next((inside[id(node)] for node in reversed(nodes) if id(node) in inside), None)
        return ROOT if within is None else (*ROOT, within)
    index, depth, node = best
    return (*ROOT, index, 'block', *parts[depth:]) if node else (*ROOT, index)


def duplicates(document, marks):
    """Report a repeated id at the copy an operation introduced (latest operation first)."""
    seen = {}
    pending = [(document['blocks'], ['blocks'])]
    while pending:
        container, path = pending.pop()
        if not isinstance(container, list): continue
        for index, node in enumerate(container):
            if not isinstance(node, dict): continue
            here = [*path, str(index)]
            if isinstance(node.get('id'), str): seen.setdefault(node['id'], []).append(here)
            pending.extend((node.get(key), [*here, key]) for key in CHILDREN)
    for paths in seen.values():
        if len(paths) > 1:
            mapped = [locate(document, [*path, 'id'], marks) for path in paths]
            owned = [path for path in mapped if len(path) > 3 and path[3] == 'block']
            invalid(max(owned, key=lambda path: path[2]) if owned else mapped[-1])


def apply(base, operations):
    check(operations)
    document = deepcopy(base)
    marks, alive = {}, []  # id() keys stay unique only while every keyed object is alive.
    for index, operation in enumerate(operations):
        key = anchor(operation)
        found = find(document, operation[key])
        if found is None: invalid((*ROOT, index, key))
        container, position = found
        if operation['op'] == 'remove':
            alive.append(container.pop(position))
            marks[id(container)] = index
            continue
        block = deepcopy(operation['block'])
        alive.append(block)
        marks[id(block)] = index
        if operation['op'] == 'replace':
            alive.append(container[position]); container[position] = block
        else:
            container.insert(position + (key == 'afterId'), block)
            marks[id(container)] = index
    duplicates(document, marks)
    try:
        return canonicalize(document, ())
    except ResearchError as error:
        if isinstance(error.details, dict) and isinstance(error.details.get('path'), str):
            error.details['path'] = pointer(locate(document, parse(error.details['path']), marks))
        raise
