"""Canonical post blocks, bounded semantic validation and deterministic text/diffs."""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import urlsplit
from pydantic import ValidationError
from .post_block_contract import Document
from .research import fail

ORDER = ['bold', 'italic', 'underline', 'strike', 'code', 'color']
CHILDREN = ('blocks', 'items', 'rows', 'cells', 'columns')
CONTROL = re.compile(r'[\x00-\x09\x0b-\x1f\x7f-\x9f]')


def pointer(parts):
    return ''.join('/' + str(part).replace('~', '~0').replace('/', '~1') for part in parts)


def invalid(path, code='invalid_block_document', status=422):
    fail(code, 'Invalid block document', status, {'path': pointer(path), 'code': code})


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value): return hashlib.sha256(encoded(value)).hexdigest()


def safe_link(value):
    if not value or re.search(r'[\s\x00-\x1f\x7f-\x9f\\]', value): return False
    if value.startswith('#'): return len(value) > 1
    try:
        url = urlsplit(value)
        if url.username is not None or url.password is not None: return False
        if url.scheme.lower() in ('http', 'https'): return bool(url.hostname)
        return url.scheme.lower() == 'mailto' and bool(url.path) and not url.netloc
    except ValueError: return False


def _shape_path(error, raw, root):
    current, path = raw, list(root)
    for part in error['loc']:
        if isinstance(current, dict) and part in current:
            current = current[part]; path.append(part)
        elif isinstance(current, list) and isinstance(part, int) and part < len(current):
            current = current[part]; path.append(part)
        elif error['type'] == 'missing' and part == error['loc'][-1]: path.append(part)
    if error['type'] in ('union_tag_invalid', 'union_tag_not_found'): path.append('type')
    return path


def canonicalize(raw, root=('content',)):
    # Bound raw nesting and work before JSON encoding or Pydantic recursion.
    pending = [(raw, 0)]; objects = 0
    while pending:
        value, depth = pending.pop(); objects += 1
        if depth > 40 or objects > 100000: invalid(root, 'content_limit_exceeded')
        if isinstance(value, dict): pending.extend((v, depth+1) for v in value.values())
        elif isinstance(value, list): pending.extend((v, depth+1) for v in value)
    try:
        if len(encoded(raw)) > 1048576: invalid(root, 'payload_too_large', 413)
    except (TypeError, ValueError, UnicodeError): invalid(root)
    if isinstance(raw, dict) and type(raw.get('version')) is int and raw['version'] != 1:
        invalid((*root, 'version'), 'unsupported_schema_version')
    try: value = Document.model_validate_json(json.dumps(raw)).model_dump(mode='json', exclude_none=True)
    except ValidationError as exc:
        paths = [_shape_path(error, raw, root) for error in exc.errors(include_input=False, include_context=False)]
        invalid(max(paths, key=lambda p: (len(p), p[-1:] == ['type'])))
    counts = Counter(); identities = set()
    def text(value, path, limit=None, multiline=True):
        value = value.replace('\r\n', '\n')
        if CONTROL.search(value) or (not multiline and '\n' in value): invalid(path)
        if limit is not None and len(value) > limit: invalid(path, 'content_limit_exceeded')
        counts['text'] += len(value)
        if counts['text'] > 200000: invalid(root, 'content_limit_exceeded')
        return value
    def runs(values, path, caption=False):
        result = []; total = 0
        for i, run in enumerate(values):
            rp = (*path, i); counts['runs'] += 1
            if counts['runs'] > 10000: invalid(root, 'content_limit_exceeded')
            run['text'] = text(run['text'], (*rp, 'text')); total += len(run['text'])
            seen = set(); marks = {}
            for j, mark in enumerate(run['marks']):
                kind = 'color' if isinstance(mark, dict) else mark
                if kind in seen: invalid((*rp, 'marks', j))
                seen.add(kind); marks[kind] = {'color': mark['color'].upper()} if kind == 'color' else kind
            run['marks'] = [marks[k] for k in ORDER if k in marks]
            if 'link' in run and (caption or not safe_link(run['link'])): invalid((*rp, 'link'))
            if result and {k:v for k,v in result[-1].items() if k!='text'} == {k:v for k,v in run.items() if k!='text'}:
                result[-1]['text'] += run['text']
            else: result.append(run)
            if len(result[-1]['text']) > 20000: invalid((*rp, 'text'), 'content_limit_exceeded')
        if caption and total > 2000: invalid(path, 'content_limit_exceeded')
        return result
    def walk(node, path, depth, list_depth=0, cell=False):
        identity = node['id']
        if identity in identities: invalid((*path, 'id'))
        identities.add(identity)
        if len(identities)>2000: invalid(path, 'content_limit_exceeded')
        kind = node.get('type')
        if kind == 'list': list_depth += 1
        if list_depth > 3 or depth > 8: invalid(path, 'content_limit_exceeded')
        if 'runs' in node: node['runs'] = runs(node['runs'], (*path,'runs'))
        if 'caption' in node: node['caption'] = runs(node['caption'], (*path,'caption'), True)
        if kind == 'code': node['text'] = text(node['text'], (*path,'text'))
        if kind == 'image':
            node['alt'] = text(node['alt'], (*path,'alt'), 1000, False)
            counts['images'] += 1
            if counts['images'] > 100: invalid(path,'content_limit_exceeded')
        if kind == 'list':
            if node['ordered']: node.setdefault('start', 1)
            elif 'start' in node: invalid((*path,'start'))
        if 'background' in node: node['background'] = node['background'].upper()
        if cell and kind == 'paragraph': node.pop('align', None)
        if kind == 'table':
            counts['tables'] += 1
            if counts['tables'] > 20: invalid(path,'content_limit_exceeded')
            width = len(node['rows'][0]['cells'])
            for i,row in enumerate(node['rows']):
                if i and row['header']: invalid((*path,'rows',i,'header'))
                if len(row['cells']) != width: invalid((*path,'rows',i,'cells'))
                counts['cells'] += len(row['cells'])
                if counts['cells'] > 1000: invalid(path,'content_limit_exceeded')
        if kind == 'columns':
            counts['columns'] += 1
            if counts['columns'] > 20: invalid(path,'content_limit_exceeded')
        for key in CHILDREN:
            for i,child in enumerate(node.get(key, [])):
                walk(child, (*path,key,i), depth+1, list_depth, cell=(key=='blocks' and 'type' not in node and 'width' not in node and 'align' in node))
    for i,node in enumerate(value['blocks']): walk(node, (*root,'blocks',i), 1)
    if len(image_ids(value)) > 50: invalid(root,'content_limit_exceeded')
    if len(encoded(value)) > 1048576: invalid(root,'payload_too_large',413)
    return value


def nodes(document):
    def walk(node):
        yield node
        for key in CHILDREN:
            for child in node.get(key,[]): yield from walk(child)
    for node in document['blocks']: yield from walk(node)


def image_ids(document):
    return sorted({node['imageId'] for node in nodes(document) if node.get('type')=='image'})


def project_node(node):
    kind = node.get('type')
    if kind in ('paragraph','heading'): return ''.join(r['text'] for r in node['runs'])
    if kind == 'divider': return ''
    if kind == 'code': return node['text']
    if kind in ('image','embed'):
        first = node['alt'] if kind=='image' else 'youtube:'+node['resourceId']
        caption = ''.join(r['text'] for r in node['caption'])
        return '\n'.join(v for v in (first,caption) if v)
    if kind=='table': return '\n'.join('\t'.join(project_node(cell) for cell in row['cells']) for row in node['rows'])
    if kind=='list': return '\n'.join('\n'.join(project_node(b) for b in item['blocks']) for item in node['items'])
    if kind=='columns': return '\n\n'.join(project_node(c) for c in node['columns'])
    return '\n\n'.join(project_node(b) for b in node.get('blocks',[]))


def text_projection(document): return '\n\n'.join(project_node(n) for n in document['blocks'])


def format_changes(base, candidate):
    left, right = list(nodes(base)), list(nodes(candidate))
    by_id = {n['id']:n for n in left}; used = set(); changes = []
    buckets = defaultdict(list)
    for node in left: buckets[(node.get('type'),project_node(node))].append(node)
    positions = {n['id']:i for i,n in enumerate(left)}
    def local(node): return {k:v for k,v in node.items() if k not in (*CHILDREN,'id')}
    for i,node in enumerate(right):
        old = by_id.get(node['id']); ambiguous = False
        if old is None:
            matches = [n for n in buckets[(node.get('type'),project_node(node))] if n['id'] not in used]
            if matches: old=matches[0]; ambiguous=len(matches)>1
        if old is None:
            changes.append({'baseBlockId':None,'candidateBlockId':node['id'],'kind':'added','changedProperties':['structure']}); continue
        used.add(old['id']); a,b = local(old),local(node)
        keys = sorted(k for k in a.keys()|b.keys() if a.get(k)!=b.get(k))
        moved = positions[old['id']] != i
        if keys or moved or ambiguous:
            kind = 'ambiguous' if ambiguous else 'layout' if any(k in keys for k in ('width','type','ordered','start')) else 'moved' if moved else 'format-only'
            changes.append({'baseBlockId':old['id'],'candidateBlockId':node['id'],'kind':kind,'changedProperties':keys or ['structure']})
    for node in left:
        if node['id'] not in used: changes.append({'baseBlockId':node['id'],'candidateBlockId':None,'kind':'removed','changedProperties':['structure']})
    return changes
