"""#68 G4 read-only post-check (design g4 r3 §3, D4). Guest, root; once per stage; changes nothing:

    ssh … 'sudo -n python3 -I -B - <stage> <sha256 of g3-expected.json>' < g4-post.py      # stage: b1 | b2

b1: optional, while the g4 unit waits for the idle stop (the turn returned, the gen 9 Pod may still run).
b2: after the unit ended (the natural idle stop of gen 9).
Reads, never writes: `.codex`, the G2 record (stat and sha256; compared, never printed) and staging against the
accepted G3a reference (the G3a run.json on the guest: `.codex` dev / ino / mtime_ns, the record's dev / ino / mode /
uid / gid / nlink / size / mtime_ns / sha256); the two kept directories and `.git` / `.agents` / `.aws` with the E2
fields (the label printed, not compared; g3-post r2); `v6pre-write.txt` (stat only); `g4-src` (an empty dir of uid
10001 or absent); no other name; the owner's Pods; the registry; every owner's binding; the ledgers (resources-6 at
its pin; isolation-42 field names allocation / item / status of the last claim only, never usage or text); the G4
run record (pass / attempt classes). `look` and `same_fields` are g3-post.py r2's functions, byte-identical (pinned).
Output: one JSON object of names, booleans, counts, ino numbers and lookup rc; `failed` lists every expectation that
did not hold; `pass` is true only when that list is empty and every lookup succeeded.
"""
import datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import sys

OWNER = '00000000-0000-4000-8000-000000005805'
STATE = '75780e81-ef7f-4aec-a59c-ed9d71f4061a'
STATE_HEX = '75780e81ef7f4aeca59ced9d71f4061a'
STATE_ROOT = '/var/lib/anxious-lookout/user-runners/%s/native/data' % STATE_HEX
W = STATE_ROOT + '/workspace'
EV = '/root/native68/evidence'
EXPECTED_FILE = '/root/native68/g3-expected.json'
K3S = '/usr/local/bin/k3s'
STAGES = ('b1', 'b2')
GEN = 9
RESOURCES_6 = '51e7ae651e8dcb79213082362fef2bd6873c11f2000b9567719974c61b6eafeb'
LABEL = 'system_u:object_r:container_file_t:s0:c576,c788'  # the E2 value, reference only (g3-post r2)
KEPT = {'codex-dir-kept-20261110T090017Z': (131192, '2026-11-09T14:17:24.293441Z'),
        'codex-dir-kept-20261110T170732Z': (131200, '2026-11-10T10:23:29.233552Z')}
OTHERS = {'.git': (131193, '2026-11-09T14:17:24.293441Z'), '.agents': (131194, '2026-11-09T14:17:24.293441Z'),
          '.aws': (131196, '2026-11-09T14:17:24.294441Z')}
KNOWN = 'v6pre-write.txt'
G4_NAME = 'g4-src'
RECORD, STAGING = 'dot-codex-placeholder.json', '.placeholder-staging'
RECORD_FIELDS = ('dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns')
HEX64 = re.compile(r'[0-9a-f]{64}')


def utc(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def look(p, digest=False):
    try:
        s = os.lstat(p)
    except FileNotFoundError:
        return {'exists': False}
    except OSError as e:
        return {'error': type(e).__name__}
    o = {'exists': True, 'dev': '%d:%d' % (os.major(s.st_dev), os.minor(s.st_dev)), 'ino': s.st_ino,
         'type': 'dir' if stat.S_ISDIR(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'other',
         'uid': s.st_uid, 'gid': s.st_gid, 'mode': oct(stat.S_IMODE(s.st_mode)), 'nlink': s.st_nlink,
         'size': s.st_size, 'mtime': utc(s.st_mtime), 'mtime_ns': s.st_mtime_ns}
    try:
        o['selinux'] = os.getxattr(p, 'security.selinux', follow_symlinks=False).rstrip(b'\0').decode('ascii', 'replace')
    except OSError as e:
        o['selinux'] = 'error: %s' % type(e).__name__
    if stat.S_ISDIR(s.st_mode):
        try:
            o['entries'] = len(os.listdir(p))
        except OSError as e:
            o['entries_error'] = type(e).__name__
    if digest and stat.S_ISREG(s.st_mode):
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                o['sha256'] = hashlib.sha256(os.read(fd, 65537)).hexdigest() if os.fstat(fd).st_size <= 65536 else 'error: size'
            finally:
                os.close(fd)
        except OSError as e:
            o['sha256'] = 'error: %s' % type(e).__name__
    return o


def same_fields(entry, ino, mtime):
    return (entry.get('exists') is True and entry.get('dev') == '7:10' and entry.get('ino') == ino
            and entry.get('type') == 'dir' and entry.get('uid') == 10001 and entry.get('gid') == 10001
            and entry.get('mode') == '0o755' and entry.get('mtime') == mtime and entry.get('entries') == 0
            and isinstance(entry.get('selinux'), str) and entry['selinux'] != ''
            and not entry['selinux'].startswith('error'))  # r2: the label is read and printed, never compared


def run(cmd, inp=None, timeout=60):
    try:
        p = subprocess.run(cmd, input=inp, stdin=None if inp is not None else subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, type(e).__name__


def sql(query):
    script = 'BEGIN READ ONLY;\n%s\nCOMMIT;\n' % query
    rc, text = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-X', '-U',
                    'postgres', '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], inp=script)
    rows = []
    for line in text.splitlines() if rc == 0 else []:
        if line.strip() not in ('', 'BEGIN', 'COMMIT'):
            try:
                rows.append(json.loads(line))
            except ValueError:
                rows.append('unparsed')
    return rc, rows


LEDGER_EVIDENCE = ('claim id recorded by the G4 run', 'the claim present exactly once', 'the claim is v6 / G4 / settled',
                   'the claim belongs to the G4 run read', 'isolation-42 without the claim equals before',
                   'resources-6 equals before')


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def ledger_evidence(docs, g4, run_name):
    """Booleans only: the G4 run's recorded claim id and before-digests against the ledgers now (= g4-turn's
    ledgers_changed_only_by_g4, from the recorded digests). `docs`: the parsed ledgers (memory only). `run_name`: the
    name of the G4 evidence directory that was read (r2, review code r1: the expected `run` comes from there, never
    from the claim itself)."""
    g4 = g4 or {}
    claim_id = g4.get('g4Claim')
    before = g4.get('ledgersBefore') or {}
    iso, res = docs.get('isolation-42'), docs.get('resources-6')
    out = dict.fromkeys(LEDGER_EVIDENCE, False)
    out['claim id recorded by the G4 run'] = isinstance(claim_id, str) and bool(claim_id)
    if not isinstance(iso, dict) or not isinstance(res, dict) or not out['claim id recorded by the G4 run']:
        return out
    mine = [c for c in iso.get('claims', []) if isinstance(c, dict) and c.get('id') == claim_id]
    out['the claim present exactly once'] = len(mine) == 1
    out['the claim is v6 / G4 / settled'] = len(mine) == 1 and (
        mine[0].get('allocation'), mine[0].get('item'), mine[0].get('status')) == ('v6', 'G4', 'settled')
    out['the claim belongs to the G4 run read'] = (len(mine) == 1 and isinstance(run_name, str)
                                                   and re.fullmatch(r'g4-turn-\d{8}T\d{6}Z', run_name) is not None
                                                   and mine[0].get('run') == run_name)
    rest = dict(iso, claims=[c for c in iso.get('claims', []) if not (isinstance(c, dict) and c.get('id') == claim_id)])
    out['isolation-42 without the claim equals before'] = canonical(rest) == (before.get('isolation-42') or {}).get(
        'canonical')
    out['resources-6 equals before'] = canonical(res) == (before.get('resources-6') or {}).get('canonical')
    return out


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def evaluate(stage, o, expected, ref):
    """The stage's expectations: the list of names of every one that did not hold. `ref` = the G3a run record."""
    failed = []

    def need(name, ok):
        if not ok:
            failed.append(name)
    e = o['entries']
    need('workspace listing', o['workspace_listing'].get('rc') == 0)
    codex, rec, staging = e['.codex'], o['g2_names'][RECORD], o['g2_names'][STAGING]
    ref_codex, ref_rec = (ref or {}).get('codex') or {}, (ref or {}).get('record') or {}
    need('.codex empty 0755 dir of uid 10001', codex.get('exists') is True and codex.get('type') == 'dir'
         and codex.get('mode') == '0o755' and codex.get('uid') == 10001 and codex.get('entries') == 0)
    need('.codex dev/ino/mtime equal to G3a', [codex.get('ino'), codex.get('mtime_ns'), o['codexDevNumber']]
         == [ref_codex.get('ino'), ref_codex.get('mtime_ns'), ref_codex.get('dev')] and ref_codex.get('ino') is not None)
    now = dict({k: rec.get(k) for k in RECORD_FIELDS if k != 'dev'}, dev=o['recordDevNumber'], sha256=rec.get('sha256'))
    need('record %s equal to G3a' % '/'.join(RECORD_FIELDS + ('sha256',)),
         set(ref_rec) == set(RECORD_FIELDS) | {'sha256'} and all(now[k] == ref_rec[k] for k in ref_rec))
    need('staging empty 0700 dir', staging.get('exists') is True and staging.get('type') == 'dir'
         and staging.get('mode') == '0o700' and staging.get('entries') == 0)
    kept = sorted(n for n in e if n.startswith('codex-dir-kept-'))
    need('exactly the two kept directories', kept == sorted(KEPT))
    for name, (ino, mtime) in sorted(KEPT.items()) + sorted(OTHERS.items()):
        need('%s as accepted in E2' % name, same_fields(e.get(name, {}), ino, mtime))
    known = o['known']
    need('%s present, regular, uid 10001' % KNOWN, known.get('exists') is True and known.get('type') == 'file'
         and known.get('uid') == 10001)
    g4 = o['g4src']
    # r1 (review code r0 major 2): present and empty is required (design §4 (c)); absence is recorded, never a pass.
    need('g4-src an empty dir of uid 10001', g4.get('exists') is True and g4.get('type') == 'dir'
         and g4.get('uid') == 10001 and g4.get('entries') == 0)
    need('no other name', o['other_names_count'] == 0)
    rows = o['registry']['rows']
    row = rows[0] if o['registry']['rc'] == 0 and len(rows) == 1 and isinstance(rows[0], dict) else {}
    need('registry on generation %d of this state, not ambiguous' % GEN,
         row.get('state') == STATE and row.get('gen') == GEN and row.get('ambiguous') is False)
    if stage == 'b2':
        need('owner Pods 0', o['owner_pods'] == {'rc': 0, 'count': 0})
        need('registry idle_stopped, pod null, active 0', row.get('phase') == 'idle_stopped' and row.get('pod') is None
             and row.get('active') == 0)
    else:
        need('owner Pods at most 1', o['owner_pods'].get('rc') == 0 and o['owner_pods'].get('count') in (0, 1))
    need('ledger resources-6 at its pin', o['ledgers'].get('resources-6') == RESOURCES_6)
    # r1 (review code r0 major 1): the same evidence as g4-turn (its recorded claim id and before-digests).
    for key in LEDGER_EVIDENCE:
        need('ledger evidence: %s' % key, o['ledgerEvidence'].get(key) is True)
    b = o['bindings']
    need('bindings lookup', b['rc'] == 0 and b['rows'] is not None)
    if b['rc'] == 0 and b['rows'] is not None:
        by = b['rows']
        need('…5805 binding on the new plan and image', by.get(OWNER) == {
            'state': STATE, 'planSha256': expected['newPlanSha256'], 'image': expected['newImage']})
        need('the other owners at their pins', sorted(x for x in by if x != OWNER) == sorted(expected['others'])
             and all(by[x] == {'state': p['state'], 'planSha256': p['planSha256'], 'image': p['image']}
                     and o['fences'].get(x) == [p['generation'], p['revision']]
                     for x, p in expected['others'].items()))
    return failed


def main(argv):
    o = {'owner': OWNER, 'version': 'g4-post-r2', 'time': utc(datetime.datetime.now().timestamp())}
    if len(argv) != 3 or argv[1] not in STAGES or not HEX64.fullmatch(argv[2]):
        print(json.dumps({'usage': 'g4-post.py b1|b2 <sha256 of g3-expected.json>', 'pass': False}))
        return
    stage, o['stage'] = argv[1], argv[1]
    lookups_ok = True
    try:
        with open(EXPECTED_FILE, 'rb') as f:
            data = f.read()
        expected = json.loads(data)
        o['expected'] = {'sha256ok': hashlib.sha256(data).hexdigest() == argv[2]}
    except (OSError, ValueError) as e:
        expected, o['expected'] = None, {'error': type(e).__name__}
    if not o['expected'].get('sha256ok'):
        o['failed'], o['pass'] = ['g3-expected.json is not the reviewed file'], False
        print(json.dumps(o, sort_keys=True, indent=1))
        return
    try:
        names = sorted(os.listdir(W))
        o['workspace_listing'] = {'rc': 0, 'count': len(names)}
    except OSError as e:
        names, o['workspace_listing'], lookups_ok = [], {'rc': type(e).__name__}, False
    watched = ['.codex', '.git', '.agents', '.aws'] + [n for n in names if n.startswith('codex-dir-kept-')]
    o['entries'] = {n: look(os.path.join(W, n)) for n in watched}
    o['known'] = {k: v for k, v in look(os.path.join(W, KNOWN)).items() if k not in ('selinux',)}
    o['g4src'] = look(os.path.join(W, G4_NAME))
    o['other_names_count'] = len([n for n in names if n not in watched and n not in (KNOWN, G4_NAME)])
    o['g2_names'] = {RECORD: look(os.path.join(STATE_ROOT, RECORD), digest=True),
                     STAGING: look(os.path.join(STATE_ROOT, STAGING))}
    for key, path in (('codexDevNumber', os.path.join(W, '.codex')), ('recordDevNumber', os.path.join(STATE_ROOT, RECORD))):
        try:
            o[key] = os.lstat(path).st_dev
        except OSError:
            o[key] = None
    o['recordInvariantFields'] = list(RECORD_FIELDS) + ['sha256']
    rc, text = run([K3S, 'kubectl', '-n', 'codex-runner', 'get', 'pods', '-l', 'anxious.io/owner=' + OWNER, '-o', 'name'])
    o['owner_pods'] = {'rc': rc, 'count': len([l for l in text.splitlines() if l.strip()]) if rc == 0 else None}
    rc, rows = sql("SELECT json_build_object('state',state_id,'phase',phase,'gen',generation,'rev',revision,'pod',"
                   "pod_uid,'active',active_requests,'ambiguous',ambiguous) FROM runner_runtime.registry WHERE "
                   "owner_id='%s';" % OWNER)
    o['registry'] = {'rc': rc, 'rows': rows}
    rc, rows = sql("SELECT json_build_object('owner',b.owner_id,'state',b.state_id,'plan',b.plan,'gen',r.generation,"
                   "'rev',r.revision) FROM runner_bindings.bindings b LEFT JOIN runner_runtime.registry r ON "
                   "r.owner_id=b.owner_id ORDER BY 1::text;")
    by, fences = None, {}
    if rc == 0 and all(isinstance(r, dict) and isinstance(r.get('plan'), dict) for r in rows):
        by = {r['owner']: {'state': r['state'], 'planSha256': digest(r['plan']), 'image': r['plan'].get('image')}
              for r in rows}
        fences = {r['owner']: [r['gen'], r['rev']] for r in rows}
    o['bindings'], o['fences'] = {'rc': rc, 'rows': by}, fences
    o['ledgers'], docs = {}, {}
    for name in ('isolation-42', 'resources-6'):
        try:
            with open(os.path.join(EV, 'ledger-%s.json' % name), 'rb') as f:
                raw = f.read()
            o['ledgers'][name] = hashlib.sha256(raw).hexdigest()
            docs[name] = json.loads(raw)
        except (OSError, ValueError) as e:
            o['ledgers'][name] = 'error: %s' % type(e).__name__
    ref = None
    try:
        runs = sorted(d for d in os.listdir(EV) if re.fullmatch(r'g3-g3a-\d{8}T\d{6}Z', d))
        with open(os.path.join(EV, runs[0], 'run.json')) as f:
            ref = json.load(f) if len(runs) == 1 else None
    except (OSError, ValueError, IndexError):
        ref = None
    o['g3aRecord'] = {'found': ref is not None, 'pass': (ref or {}).get('g3a') == 'pass'}
    g4, run_name = None, None
    try:
        runs = sorted(d for d in os.listdir(EV) if re.fullmatch(r'g4-turn-\d{8}T\d{6}Z', d))
        with open(os.path.join(EV, runs[0], 'run.json')) as f:
            g4 = json.load(f) if len(runs) == 1 else None
        run_name = runs[0] if g4 is not None else None
        o['g4Run'] = {'found': g4 is not None, 'g4': (g4 or {}).get('g4'),
                      'attempts': [a.get('cls') for a in (g4 or {}).get('attempts', [])]}
    except (OSError, ValueError, IndexError):
        o['g4Run'] = {'found': False}
    o['ledgerEvidence'] = ledger_evidence(docs, g4, run_name)
    o['failed'] = evaluate(stage, o, expected, ref)
    if not o['g3aRecord']['pass']:
        o['failed'].append('the G3a run record passed')
    o['lookupsOk'] = lookups_ok and all('error' not in json.dumps(v) for v in (o['entries'], o['g2_names'], o['known'],
                                                                              o['g4src'], o['ledgers']))
    o['pass'] = not o['failed'] and o['lookupsOk']
    if 'sha256' in o['g2_names'][RECORD]:
        o['g2_names'][RECORD]['sha256'] = 'recorded' if HEX64.fullmatch(o['g2_names'][RECORD]['sha256']) else 'error'
    print(json.dumps(o, sort_keys=True, indent=1))


if __name__ == '__main__':
    main(sys.argv)
