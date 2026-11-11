"""#68 V6-10 precondition (public document fixture design r2 baf50db8…; PM decisions r1): one public document of owner
…5805 on the development VM only (S1), through the application's own API. Root on the guest:

    python3 -I -B /root/native68/pa-doc.py check   # read-only: the guard and the content pin; writes nothing
    systemd-run … python3 -I -B /root/native68/pa-doc.py doc   # once, after the P-A window closed and PM's go

A wrapper over the pinned v6pre-turns.py (6170fec8…): its Stop / say / record / sql / create_session / api /
revoke_session / registry / runner_pods / final_check and main are used unchanged; main gets this file's guard,
evidence opener and the one action `doc`. No SQL on the document / publication path (the session helper writes and
deletes its own auth.sessions row, as in every unit). No runner, no Pod, no model turn, no ledger change.

Steps (all cross-checks must hold):
- D1 POST /api/research/documents {title, content} (base api): 201; id; version (int); latestVersion.id; the title /
  content sha256 = the pinned ones;
- D2 GET …/{id}/publication-preview (base api): 200; documentId = D1 id; expectedVersion = D1 version; versionId = D1
  latestVersion.id; publishable true; previewToken 64 hex; title / content sha256 = the pinned ones;
- D3 POST …/{id}/publications with exactly D2's expectedVersion / versionId / previewToken (base api): 201; documentId
  = D1 id; versionId = D2's; a publication id; title / content sha256 = the pinned ones;
- the session is revoked;
- D4 GET /api/public/documents/{id} by a separate anonymous request (Host and Accept only: no Cookie, Authorization or
  CSRF header; never the base api, which always sends the cookie): 200; documentId / versionId / id = D1 / D2 / D3;
  title / content sha256 = the pinned ones;
- the read-only look (pa-look.py's public-doc query): exactly one published document of …5805, its id = D1's.

Failures: a non-expected status or a failed cross-check stops (rc 4) with no retry and no delete. A call with no status
(timeout, a lost connection) is UNKNOWN, never "not created": recorded as unknown, no retry, no delete. On every stop
before D4 the owner-scoped read-only look (documents and publication heads of …5805: ids) is recorded and the session
is revoked here; a revoke not proved is rc 70, which takes priority over the step's rc. An unknown session creation is
rc 4 (its row, if any, removed by the same revoke). The base main's own cleanup then runs as always (its revoke is a
no-op once revoked; its final invariants).
…5805's own invariants (r1, review major): the guard (under the base lock) records …5805's registry row, the runner Pod
list (empty) and the raw sha256 of both ledger files (a missing or unreadable ledger refuses, rc 3). The base main's
final_check is wrapped: the base result AND …5805's after-state (the registry row equal to the one before, no runner
Pod, both ledgers byte-equal); any lookup or read failure there is "not held". The wrapper runs on every path from the
base main's finally and is recorded (run.json ownerInvariants, a verdict line); a failure is rc 12 only when the run's
rc was 0 (the base rule), so 70 and 4 keep their priority.
rc: 0 created, published, every cross-check held, look count 1, …5805 unchanged; 3 refused (guard, content pin); 4 a
call failed, a cross-check failed or a result unknown; 70 session not revoked; 12 final invariants (the base's or
…5805's own); 9 unexpected; 130 interrupted. Never rerun on a nonzero rc. The document is never deleted or withdrawn
by this file.

r2 (design (b) r1 §6, PM: one commit with r1): the P-A window is the pas2 unit's, never the failed pas run's. The guard
accepts exactly one pa-start-pas2-* run with exit rc 0 and its observer validated, AND the failed pas run exactly as
preserved (the only pa-start-pas-* run, its six files at FAILED_PINS and nothing else, exit rc 16); the evidence
records the accepted pas2 run by its run.json / exit.json sha256 and the failed run by its pins.
"""
import hashlib
import http.client
import json
import os
import re
import sys
import time
import types

BASE_PATH, BASE_SHA = '/root/native68/v6pre-turns.py', '6170fec826400589ef0ac4ed38a4fd070f2abe2625edf6e1c8bb2887f7898e73'
CONTENT_PATH = '/root/native68/pa-doc-content.json'
CONTENT_SHA = 'c5e66f195ab916dacab7b3c8d739bd8d3ef6a27a693d01cfcdfa36129d3f0eac'
PUBLIC = '/api/public/documents/'
ACTION = 'doc'
PAS_RUN, PAS2_RUN, DOC_RUN = r'pa-start-pas-\d{8}T\d{6}Z', r'pa-start-pas2-\d{8}T\d{6}Z', r'pa-doc-\d{8}T\d{6}Z'
FAILED_RUN = 'pa-start-pas-20261111T123436Z'  # r2: the failed pas run (rc 16), preserved; its six file pins
FAILED_PINS = {
    'exit.json': '72dfc1a79b30556923f7f2dc9f636a85c7299cbf021130e8aecb070260399910',
    'k4-pids.json': '3507403a21467ed1c36f4580519d099dac7167b303b4278a05c0771a26293687',
    'observer.err': 'a2ec3d4074abc856eb968b8430ebecb32889ec207e53c66e48ba8337dfe43aff',
    'observer.out': 'd139723f605053bd5ad9ed253d052703322fad2a79df8f72ae8870025437f455',
    'out.txt': '6d735563928f00e52512ca163265b0f0330100a8c27e09b28af7fadbe74d33a9',
    'run.json': 'c7753896b09586a0ad47ef8960cb0ac920c0fcba272beec8d5353f593f80944e',
}
HEX64 = re.compile(r'[0-9a-f]{64}')
P = None
SCRIPT_SHA = None
FACTS = {}
BEFORE = {}
RUN = {'ev': None, 'baseFinal': None, 'window': None}


def load_pinned(path, sha, name):
    data = open(path, 'rb').read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit('%s is not the pinned file; nothing done' % path)
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(data, path, 'exec'), module.__dict__)
    return module


def sha_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest() if isinstance(value, str) else None


def fixed_content():
    """PM's fixed bytes: the file at its pin, exactly {title, content}, both non-empty strings."""
    try:
        data = open(CONTENT_PATH, 'rb').read()
    except OSError:
        raise P.Stop(3, 'pa-doc-content.json missing or unreadable; nothing done') from None
    if hashlib.sha256(data).hexdigest() != CONTENT_SHA:
        raise P.Stop(3, 'pa-doc-content.json is not the reviewed file; nothing done')
    doc = json.loads(data)
    if not (isinstance(doc, dict) and set(doc) == {'title', 'content'}
            and all(isinstance(doc[k], str) and doc[k] for k in doc)):
        raise P.Stop(3, 'pa-doc-content.json is not {title, content}; nothing done')
    return doc


def runs(pattern):
    return sorted(d for d in os.listdir(P.EVIDENCE) if re.fullmatch(pattern, d))


def published(code):
    """Read-only: …5805's published documents (count, ids) by pa-look.py's public-doc query, unchanged."""
    rows = P.sql('pa-doc-look', "SELECT json_build_object('count', count(*), 'ids', coalesce(json_agg(document_id ORDER "
                 "BY document_id), '[]'::json)) FROM research.publication_heads WHERE owner_id='%s' AND kind='document' "
                 "AND publication_id IS NOT NULL;" % P.NEW, code)
    if len(rows) != 1:
        raise P.Stop(code, 'publication look answered %d rows' % len(rows))
    return rows[0]


def owner_state(code):
    """Read-only, owner-scoped: …5805's documents and publication heads (ids), the partial state after a stop."""
    rows = P.sql('pa-doc-owner', "SELECT json_build_object('documents',(SELECT coalesce(json_agg(id ORDER BY id),"
                 "'[]'::json) FROM research.items WHERE owner_id='%s' AND kind='document'),'heads',(SELECT "
                 "coalesce(json_agg(json_build_object('document',document_id,'publication',publication_id) ORDER BY "
                 "document_id),'[]'::json) FROM research.publication_heads WHERE owner_id='%s'));" % (P.NEW, P.NEW), code)
    if len(rows) != 1:
        raise P.Stop(code, 'owner look answered %d rows' % len(rows))
    return rows[0]


def owner_view():
    """…5805's own state (r1): its registry row, the runner Pod names, the raw sha256 of both ledger files. Raises on
    any lookup or read failure (P.Stop from the base lookups, OSError from a missing / unreadable ledger)."""
    pods = P.runner_pods()
    ledgers = {}
    for name in sorted(P.LEDGERS):
        with open(P.LEDGERS[name][0], 'rb') as stream:
            ledgers[name] = hashlib.sha256(stream.read()).hexdigest()
    return {'registry': P.registry(), 'pods': sorted(p.get('metadata', {}).get('name', '?') for p in pods),
            'ledgers': ledgers}


def final_check_doc(verdicts):
    """…5805's after-invariants against BEFORE (recorded first), then the base final_check; both must hold (r1, review
    major). Called by the base main's finally on every path."""
    try:
        after, error = owner_view(), None
    except (P.Stop, OSError, ValueError) as failure:
        after, error = None, getattr(failure, 'message', None) or type(failure).__name__
    checks = {'before recorded': bool(BEFORE),
              'registry unchanged': after is not None and after['registry'] == BEFORE.get('registry'),
              'no runner Pod': after is not None and after['pods'] == [],
              'ledgers unchanged': after is not None and after['ledgers'] == BEFORE.get('ledgers')}
    held = all(checks.values())
    verdicts['…5805 invariants'] = 'ok' if held else 'FAILED (%s)' % (error or ', '.join(
        k for k, v in checks.items() if not v))
    if RUN['ev'] is not None:
        try:
            P.record(RUN['ev'], ownerInvariants={'before': BEFORE or None, 'after': after, 'error': error,
                                                 'checks': checks, 'held': held})
        except BaseException:
            P.say('ownerInvariants not recorded')
    P.say('…5805 invariants: %s' % json.dumps(checks, sort_keys=True, ensure_ascii=False))
    base_ok = RUN['baseFinal'](verdicts)  # after: if the base check raises, …5805's state is already recorded
    return bool(base_ok) and held


def failed_preserved():
    """r2: the only pa-start-pas-* run is the failed one, holding exactly its six files at their pins, exit rc 16."""
    if runs(PAS_RUN) != [FAILED_RUN]:
        return False
    folder = os.path.join(P.EVIDENCE, FAILED_RUN)
    try:
        if sorted(os.listdir(folder)) != sorted(FAILED_PINS):
            return False
        for name, pin in FAILED_PINS.items():
            with open(os.path.join(folder, name), 'rb') as stream:
                if hashlib.sha256(stream.read()).hexdigest() != pin:
                    return False
    except OSError:
        return False
    return P.exit_rc(FAILED_RUN) == 16


def guard_doc(action):
    """Read-only (rc 3): the P-A window closed (r2: exactly one pas2 run, exit rc 0, observer validated; the failed pas
    run preserved), no earlier pa-doc run, …5805 idle_stopped without a Pod, no runner Pod, none published, the content
    at its pin; r1: …5805's own state (registry row, Pod list, both ledgers' raw sha256) recorded as BEFORE, a lookup or
    read failure refused."""
    if action != ACTION:
        raise P.Stop(3, 'only doc exists here')
    if runs(DOC_RUN):
        raise P.Stop(3, 'pa-doc already ran; a second run is a PM decision')
    pas2 = runs(PAS2_RUN)
    try:
        raw = {f: open(os.path.join(P.EVIDENCE, pas2[0], f), 'rb').read() for f in ('run.json', 'exit.json')} \
            if len(pas2) == 1 else {}
        rec = json.loads(raw['run.json']) if raw else {}
    except (OSError, ValueError):
        raw, rec = {}, {}
    observer = rec.get('observer') if isinstance(rec, dict) else None
    if len(pas2) != 1 or not raw or P.exit_rc(pas2[0]) != 0 or not isinstance(observer, dict) \
            or observer.get('state') != 'validated':
        raise P.Stop(3, 'the P-A window is not closed: exactly one pas2 run with rc 0 and its observer validated needed')
    if not failed_preserved():
        raise P.Stop(3, 'the failed pas run is not exactly as preserved (only it, its six files at their pins, rc 16)')
    RUN['window'] = {'pas2': pas2[0], 'runSha256': hashlib.sha256(raw['run.json']).hexdigest(),
                     'exitSha256': hashlib.sha256(raw['exit.json']).hexdigest(),
                     'failedRun': {'name': FAILED_RUN, 'pins': FAILED_PINS}}
    state = P.registry()
    if state.get('phase') != 'idle_stopped' or state.get('pod') is not None:
        raise P.Stop(3, '…5805 is not idle_stopped without a Pod')
    look = published(3)
    if look.get('count') != 0 or look.get('ids') != []:
        raise P.Stop(3, '…5805 already has published documents (%s)' % json.dumps(look, sort_keys=True))
    fixed_content()
    try:
        view = owner_view()
    except (P.Stop, OSError, ValueError) as failure:
        raise P.Stop(3, '…5805 state before the action not readable (%s); nothing done'
                     % (getattr(failure, 'message', None) or type(failure).__name__)) from None
    if view['pods']:
        raise P.Stop(3, 'a runner Pod exists')
    if view['registry'] != state:
        raise P.Stop(3, '…5805 state changed during the guard; nothing done')
    BEFORE.clear()
    BEFORE.update(view)


def open_evidence_doc(action):
    ev = os.path.join(P.EVIDENCE, 'pa-doc-%s' % time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    os.mkdir(ev, 0o700)
    fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    P.OUT['stream'] = os.fdopen(fd, 'w')
    RUN['ev'] = ev
    P.record(ev, action=action, started=P.now_utc(), script=SCRIPT_SHA, base=BASE_SHA, content=CONTENT_SHA,
             ownerBefore=dict(BEFORE), window=RUN['window'])
    P.say('evidence: %s (script %s, base %s, content %s)' % (ev, SCRIPT_SHA[:8], BASE_SHA[:8], CONTENT_SHA[:8]))
    return ev


def anonymous_get(path):
    """(status, body, header names sent). Host and Accept only: no Cookie, Authorization or CSRF header."""
    headers = {'Host': P.HOST_HEADER, 'Accept': 'application/json'}
    connection = http.client.HTTPConnection(P.API_HOST, P.API_PORT, timeout=60)
    try:
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        status, raw = response.status, response.read(1 << 20)
    except Exception:
        return None, None, sorted(headers)
    finally:
        try:
            connection.close()
        except Exception:
            pass
    try:
        return status, json.loads(raw), sorted(headers)
    except ValueError:
        return status, None, sorted(headers)


def step(ev, name, status, ok, **kept):
    """Record one call (unknown when it has no status) and stop on anything but the expected result."""
    FACTS[name] = dict(kept, status=status, result='unknown' if status is None else ('ok' if ok else 'failed'))
    P.record(ev, steps=FACTS)
    P.say('%s: %s' % (name, json.dumps(FACTS[name], sort_keys=True)))
    if status is None:
        raise P.Stop(4, '%s: no status (timeout or lost connection): result UNKNOWN; not retried, nothing deleted'
                     % name)
    if not ok:
        raise P.Stop(4, '%s: not as expected (status %s); not retried, nothing deleted' % (name, status))


def same_text(body, title, text):
    return sha_text(body.get('title')) == title and sha_text(body.get('content')) == text


def calls(ev, doc):
    title, text = sha_text(doc['title']), sha_text(doc['content'])
    s, b = P.api('POST', P.BASE + '/documents', {'title': doc['title'], 'content': doc['content']})
    b = b if isinstance(b, dict) else {}
    ident, version = b.get('id'), b.get('version')
    latest = (b.get('latestVersion') or {}).get('id') if isinstance(b.get('latestVersion'), dict) else None
    step(ev, 'D1', s, s == 201 and isinstance(ident, str) and type(version) is int and isinstance(latest, str)
         and same_text(b, title, text), documentId=ident, version=version, latestVersionId=latest)
    s, b = P.api('GET', P.BASE + '/documents/%s/publication-preview' % ident)
    b = b if isinstance(b, dict) else {}
    token = b.get('previewToken')
    step(ev, 'D2', s, s == 200 and b.get('documentId') == ident and b.get('expectedVersion') == version
         and b.get('versionId') == latest and b.get('publishable') is True and isinstance(token, str)
         and bool(HEX64.fullmatch(token)) and same_text(b, title, text),
         documentId=b.get('documentId'), expectedVersion=b.get('expectedVersion'), versionId=b.get('versionId'),
         publishable=b.get('publishable'), tokenSha256=sha_text(token))
    publish = {'expectedVersion': b['expectedVersion'], 'versionId': b['versionId'], 'previewToken': token}
    version_id = b['versionId']
    s, b = P.api('POST', P.BASE + '/documents/%s/publications' % ident, publish)
    b = b if isinstance(b, dict) else {}
    publication = b.get('id')
    step(ev, 'D3', s, s == 201 and b.get('documentId') == ident and b.get('versionId') == version_id
         and isinstance(publication, str) and same_text(b, title, text),
         documentId=b.get('documentId'), versionId=b.get('versionId'), publicationId=publication)
    return ident, version_id, publication


def check_public(ev, ident, version_id, publication, doc):
    s, b, sent = anonymous_get(PUBLIC + ident)
    b = b if isinstance(b, dict) else {}
    step(ev, 'D4', s, s == 200 and b.get('documentId') == ident and b.get('versionId') == version_id
         and b.get('id') == publication and same_text(b, sha_text(doc['title']), sha_text(doc['content'])),
         headersSent=sent, documentId=b.get('documentId'), versionId=b.get('versionId'), publicationId=b.get('id'),
         titleSha256=sha_text(b.get('title')), contentSha256=sha_text(b.get('content')))


def action_doc(ev, verdicts):
    doc = fixed_content()
    try:
        try:
            P.create_session(ev)
        except P.Stop as stop:
            raise P.Stop(4, 'session: %s' % stop.message) from None
        ident, version_id, publication = calls(ev, doc)
    except P.Stop as stop:
        try:
            P.record(ev, ownerState=owner_state(4))  # the partial state, read-only; nothing is deleted
        except P.Stop as look:
            P.record(ev, ownerState='lookup failed: %s' % look.message)
        if not P.revoke_session(ev):
            raise P.Stop(70, 'session not revoked after: %s' % stop.message) from None
        raise
    if not P.revoke_session(ev):
        raise P.Stop(70, 'session not revoked after the publication')
    check_public(ev, ident, version_id, publication, doc)
    look = published(4)
    if look.get('count') != 1 or look.get('ids') != [ident]:
        raise P.Stop(4, 'after: the look is not exactly this one published document (%s)' % json.dumps(look))
    P.record(ev, documentId=ident, versionId=version_id, publicationId=publication, look=look, doc='pass')
    verdicts['pa-doc'] = ('created and published through the API: document %s, publication %s; the anonymous public '
                          'read matches; exactly one published document of …5805' % (ident, publication))


def check():
    """Read-only: the guard, nothing written."""
    try:
        guard_doc(ACTION)
    except P.Stop as stop:
        P.say('guard: %s' % stop.message)
        return stop.code
    P.say('guard: would pass; content %s' % CONTENT_SHA[:8])
    return 0


def main(argv):
    global P, SCRIPT_SHA
    if len(argv) != 2 or argv[1] not in ('check', ACTION):
        print('usage: pa-doc.py check|doc')
        return 2
    if os.geteuid() != 0:
        print('run as root')
        return 2
    SCRIPT_SHA = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    try:
        P = load_pinned(BASE_PATH, BASE_SHA, 'v6pre_turns')
    except SystemExit as refused:
        print(refused)
        return 3
    P.guard, P.open_evidence, P.ACTIONS = guard_doc, open_evidence_doc, {ACTION: action_doc}
    RUN['baseFinal'], P.final_check = P.final_check, final_check_doc  # r1: the base result AND …5805's invariants
    if argv[1] == 'check':
        return check()
    return P.main(argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
