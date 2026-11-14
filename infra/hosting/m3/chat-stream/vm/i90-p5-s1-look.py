"""#90 P5 S1 look (review turns ack 2026-11-15), development VM only, READ ONLY: what the S1 reader received and what the
record holds, as bounded metadata. No session, runner entry, replay or turn; nothing is written on the guest. Run as
root with the reviewed bytes on stdin:

    sudo -n python3 -I -B - < i90-p5-s1-look.py

Inputs: the one `turns` evidence directory (its `client-S1-hop3.jsonl`, written by review's p5_client.py r1, and
`front-evidence.json`), the s0 run.json (the conversation), and the conversation's native record (one read-only SQL row).
Output, one JSON line per kind, fixed keys only, no text, no ids (salted or raw), no URL or credential:
- `reader`: per request, in stream order (request 0, 1, ...; the label is the order, never the hash): event counts
  (fixed list, unknown counted), item.started kinds in order, the item.completed summaries in order as
  (kind, textChars, textSha256, textObserved) - the text hash is the reader's own UTF-8 hash of the joined text
  (review: hashes are sensitive metadata, kept in the evidence as such), snapshot begin turnStates, turn.completed
  statuses; and the end record's reason;
- `record`: the native record's turns (status) and, for the LAST turn, its agentMessage items in order as
  (textChars, textSha256), computed in memory;
- `front`: front-evidence.json's S1 items (type, role, textChars);
- `pins`: for the r5 continuation decision - the turns and disable run names, the sha256 of their exit.json bytes,
  their rc, and the budget entries (n, step, outcome from fixed lists);
- `compare`: by the fixed selection rule (the reader's last completed request, no open snapshot, its final agentMessage by
  first appearance; the record's final agentMessage of the fresh S1 turn - exactly two turns): `equal` / `different` /
  `unknown` with a fixed reason, the selected positions and candidate counts,
  and the fixed note that the id intersection is not computable (the per-action salt was
  removed with the private directory) - text equality is not item correspondence by id.
rc: 0 reported; 3 refused (not root, evidence or record not found exactly once).
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys

OWNER = '00000000-0000-4000-8000-000000005805'
EVIDENCE = '/root/issue90/evidence'
K3S = '/usr/local/bin/k3s'
EVENTS = ('runner', 'idle', 'sync', 'snapshot.begin', 'snapshot.item', 'snapshot.end', 'turn.started', 'turn.completed',
          'turn.discard', 'item.started', 'item.status', 'item.delta', 'item.output', 'item.completed', 'stream.stopped',
          'comment_or_unknown')
KINDS = ('agentMessage', 'commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall', 'other')
TURN_STATUS = ('completed', 'interrupted', 'failed', 'inProgress')
END_REASONS = ('local_interrupt', 'local_deadline', 'local_close', 'peer_eof', 'probe_error', 'http_error')
NOTE = 'id intersection not computable (the per-action salt was removed); text equality is not item correspondence'


class Refused(Exception):
    pass


def out(kind, **fields):
    print(json.dumps({'kind': kind, **fields}, sort_keys=True), flush=True)


def one(pattern):
    found = sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(pattern, x))
    if len(found) != 1:
        raise Refused('not exactly one evidence directory')
    return os.path.join(EVIDENCE, found[0])


def read(path, limit=64 << 20):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise Refused('an input is not a regular file or too large')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        return stream.read(limit + 1)


def hexsha(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else None


def reader(path):
    """Per request in stream order (D scoping: a frame with `request` sets it; a request-less frame belongs to the
    last one). Per request: the agentMessage items in order of first appearance (item.started or snapshot.item kind),
    their summaries (item.completed, else the last snapshot.end item), whether a snapshot is left open."""
    requests, order, current, end = {}, [], None, None
    for line in read(path).splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if r.get('kind') == 'end':
            end = r.get('reason') if r.get('reason') in END_REASONS else 'unknown'
            continue
        if r.get('kind') != 'frame':
            continue
        if isinstance(r.get('request'), str):
            current = r['request']
            if current not in requests:
                order.append(current)
                requests[current] = {'events': {}, 'kinds': {}, 'agent': [], 'completed': {}, 'snapshotItems': {},
                                     'snapshot': [], 'open': False, 'turnCompleted': []}
        if current is None:
            continue
        slot = requests[current]
        event = r.get('event') if r.get('event') in EVENTS else 'unknown'
        slot['events'][event] = slot['events'].get(event, 0) + 1
        item = r.get('item') if isinstance(r.get('item'), str) else None
        if event in ('item.started', 'snapshot.item') and item and 'itemKind' in r:
            kind = r.get('itemKind') if r.get('itemKind') in KINDS else 'other'
            if item not in slot['kinds']:
                slot['kinds'][item] = kind
                if kind == 'agentMessage':
                    slot['agent'].append(item)
        elif event == 'item.completed' and item:
            slot['completed'][item] = {'textChars': r.get('textChars') if type(r.get('textChars')) is int else None,
                                       'textSha256': hexsha(r.get('textSha256')),
                                       'textObserved': r.get('textObserved') is True}
        elif event == 'snapshot.begin':
            slot['snapshot'].append(r.get('turnState') if r.get('turnState') in ('running', 'completed') else None)
            slot['open'] = True
        elif event == 'snapshot.end':
            slot['open'] = False
            for key, summary in (r.get('items') or {}).items() if isinstance(r.get('items'), dict) else []:
                if isinstance(summary, dict):
                    slot['snapshotItems'][key] = {'textChars': summary.get('textChars')
                                                  if type(summary.get('textChars')) is int else None,
                                                  'textSha256': hexsha(summary.get('textSha256')),
                                                  'textObserved': summary.get('textObserved') is True}
        elif event == 'turn.completed':
            slot['turnCompleted'].append(r.get('status') if r.get('status') in TURN_STATUS else None)
    listed = []
    for index, key in enumerate(order):
        slot = requests[key]
        summaries = [dict(position=i, source='item.completed' if item in slot['completed'] else
                          ('snapshot.end' if item in slot['snapshotItems'] else None),
                          **(slot['completed'].get(item) or slot['snapshotItems'].get(item) or {}))
                     for i, item in enumerate(slot['agent'])]
        listed.append({'request': index, 'events': slot['events'], 'agentMessages': len(slot['agent']),
                       'agentSummaries': summaries, 'snapshotTurnStates': slot['snapshot'],
                       'snapshotOpen': slot['open'], 'turnCompleted': slot['turnCompleted']})
    out('reader', requests=listed, end=end)
    return listed


def record(conversation):
    """The record's turns; the S1 turn is the last one, valid only when the record has exactly two turns (S0, S1) and
    that turn completed (review: no newer or previous turn may stand in for S1)."""
    text = ("BEGIN READ ONLY;\n/*i90p5s1look*/ SELECT json_build_object('r',native_record) FROM research.conversations "
            "WHERE id='%s' AND owner_id='%s';\nCOMMIT;\n" % (conversation, OWNER))
    try:
        done = subprocess.run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U',
                               'postgres', '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                              input=text.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
                              check=False)
        rows = [json.loads(x) for x in done.stdout.decode().splitlines() if x.strip() not in ('', 'BEGIN', 'COMMIT')]
    except Exception:
        raise Refused('database lookup failed') from None
    if done.returncode != 0 or len(rows) != 1 or not isinstance(rows[0].get('r'), dict):
        raise Refused('the conversation record is not there exactly once')
    turns = rows[0]['r'].get('turns') if isinstance(rows[0]['r'].get('turns'), list) else []
    statuses = [t.get('status') if isinstance(t, dict) and t.get('status') in TURN_STATUS else None for t in turns]
    last = turns[-1] if turns and isinstance(turns[-1], dict) else {}
    agent = []
    for item in last.get('items') if isinstance(last.get('items'), list) else []:
        if isinstance(item, dict) and item.get('type') == 'agentMessage' and isinstance(item.get('text'), str):
            agent.append({'position': len(agent), 'textChars': len(item['text']),
                          'textSha256': hashlib.sha256(item['text'].encode('utf-8')).hexdigest()})
    fresh = len(turns) == 2 and statuses[-1] == 'completed'
    out('record', turns=len(turns), statuses=statuses, s1TurnFresh=fresh, s1AgentMessages=agent)
    return agent, fresh


def pins():
    """For the r5 continuation decision (review: bind the accepted S1 evidence, the completed disable and the budget):
    the turns and disable run names (fixed patterns), the sha256 of their exit.json bytes, their rc, and the budget
    entries' steps / outcomes (fixed lists). Hashes and fixed values only."""
    value = {}
    for action in ('turns', 'disable'):
        found = sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-p5-%s-\d{8}T\d{6}Z' % action, x))
        if len(found) != 1:
            value[action] = {'runs': len(found)}
            continue
        try:
            raw = read(os.path.join(EVIDENCE, found[0], 'exit.json'))
            done = json.loads(raw)
            rc = done.get('rc') if isinstance(done, dict) and type(done.get('rc')) is int else None
        except Exception:
            raw, rc = None, None
        value[action] = {'run': found[0], 'exitSha256': hashlib.sha256(raw).hexdigest() if raw is not None else None,
                         'rc': rc}
    try:
        turns = json.loads(read(os.path.join(EVIDENCE, 'i90-p5-budget.json'))).get('turns')
        value['budget'] = [{'n': t.get('n') if type(t.get('n')) is int else None,
                            'step': t.get('step') if t.get('step') in ('S0', 'S1', 'S2', 'S3', 'S4a', 'S4b', 'S5', 'S6')
                            else 'unknown',
                            'outcome': t.get('outcome') if t.get('outcome') in ('accepted', 'reserved', 'send raised')
                            else 'other'} for t in turns if isinstance(t, dict)]
    except Exception:
        value['budget'] = None
    out('pins', **value)


def front(folder):
    try:
        value = json.loads(read(os.path.join(folder, 'front-evidence.json')))
        steps = [s for s in value.get('steps', []) if isinstance(s, dict) and s.get('step') == 'S1']
        items = [{'type': i.get('type') if i.get('type') in ('message', 'tool_call', 'notice') else 'other',
                  'role': i.get('role') if i.get('role') in ('user', 'assistant') else None,
                  'textChars': i.get('textChars') if type(i.get('textChars')) is int else None}
                 for i in (steps[-1].get('items') or [])] if steps else None
    except Exception:
        items = None
    out('front', S1Items=items)


def compare(listed, agent, fresh):
    """The selection rule (review): the reader's LAST request must have its turn.completed and no open snapshot; its
    final agentMessage (the last by first appearance) needs a summary with observed text; the record's final assistant
    is the last agentMessage of the fresh S1 turn. Anything missing or ambiguous: unknown with a fixed reason."""
    last = listed[-1] if listed else None
    why = None
    if not fresh:
        why = 'the record is not exactly S0 + one completed S1 turn'
    elif last is None or not last['turnCompleted']:
        why = 'no completed request on the reader'
    elif last['snapshotOpen']:
        why = 'a snapshot of the request is still open on the reader'
    elif not last['agentSummaries'] or not agent:
        why = 'no agentMessage on the reader or in the record'
    elif last['agentSummaries'][-1].get('source') is None or not last['agentSummaries'][-1].get('textObserved'):
        why = 'the reader has no summary with observed text for its final agentMessage'
    if why:
        out('compare', result='unknown', reason=why, note=NOTE)
        return
    mine, theirs = last['agentSummaries'][-1], agent[-1]
    equal = mine.get('textSha256') is not None and mine['textSha256'] == theirs['textSha256'] \
        and mine.get('textChars') == theirs['textChars']
    out('compare', result='equal' if equal else 'different', readerPosition=mine['position'],
        readerCandidates=len(last['agentSummaries']), recordPosition=theirs['position'], recordCandidates=len(agent),
        readerSource=mine['source'], rule='final agentMessage of the last completed request vs the final agentMessage '
        'of the fresh S1 turn', note=NOTE)


def main():
    if os.geteuid() != 0:
        out('refused', reason='not root')
        return 3
    try:
        turns = one(r'i90-p5-turns-\d{8}T\d{6}Z')
        s0 = one(r'i90-p5-s0-\d{8}T\d{6}Z')
        run = json.loads(read(os.path.join(s0, 'run.json')))
        conversation = run.get('conversation') if isinstance(run, dict) else None
        if not (isinstance(conversation, str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-'
                                                               r'[0-9a-f]{12}', conversation)):
            raise Refused('the s0 run.json has no conversation UUID')
        listed = reader(os.path.join(turns, 'client-S1-hop3.jsonl'))
        agent, fresh = record(conversation)
        front(turns)
        pins()
    except Refused as error:
        out('refused', reason=str(error))
        return 3
    except OSError:
        out('refused', reason='an input is missing or unreadable')
        return 3
    compare(listed, agent, fresh)
    return 0


if __name__ == '__main__':
    sys.exit(main())
