"""#68 G3 read-only post-check (design g3 r4 §3; extends the approved e2-post.py r0). Guest, root; once per stage;
changes nothing:

    ssh … 'sudo -n python3 -I -B - <stage> <sha256 of g3-expected.json>' < g3-post.py      # stage: g3-2 | g3a | g3b

Reads, never writes: the owner …5805 workspace names and the fixed fields of `.codex`, of every
`codex-dir-kept-*` entry, of `.git` / `.agents` / `.aws` and of the known name `v6pre-write.txt` (stat only; its
content is never read); the G2 record (stat and sha256; the sha256 is compared, never printed) and staging in the
state root; the owner's runner Pods; the registry row and every owner's binding (read-only transaction; plan
digests and images only, never plan content); the two ledger hashes; the G3a run record (g3b only: the .codex
dev / ino / mtime and the record's RECORD_FIELDS + sha256 that G3b must equal; r1, review code r0 major 2: dev, ino,
mode, uid, gid, nlink, size, mtime_ns, sha256; ctime is not compared, as in E2). Output: one JSON object of names,
booleans, counts, hashes, ino numbers and lookup rc (`recordInvariantFields` names the compared fields); `failed`
lists every expectation of the stage that did not hold, `pass` is true only when that list is empty and every
lookup succeeded.
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
STAGES = ('g3-2', 'g3a', 'g3b')
REGISTRY = {'g3-2': 6, 'g3a': 7, 'g3b': 8}
LEDGERS = {'isolation-42': '9a1a8173e3aebf45729cfed5b024d6732366886ecc8dd01bcaaaf6dbdfa98d3e',
           'resources-6': '51e7ae651e8dcb79213082362fef2bd6873c11f2000b9567719974c61b6eafeb'}
# The E2-accepted fields (e2-post-1.txt, 2026-11-10; ctime not compared).
# r2 (G3a post 2026-11-10, design g3 r4 §3): the SELinux label is printed (recorded) but not compared. Each runner Pod
# start relabels the owner PV with its own MCS pair (design g2 ⑪), so after G3a it is c449,c803 where E2 saw
# c576,c788. LABEL is the E2 value, kept for reference only.
LABEL = 'system_u:object_r:container_file_t:s0:c576,c788'
KEPT = {'codex-dir-kept-20261110T090017Z': (131192, '2026-11-09T14:17:24.293441Z'),
        'codex-dir-kept-20261110T170732Z': (131200, '2026-11-10T10:23:29.233552Z')}
OTHERS = {'.git': (131193, '2026-11-09T14:17:24.293441Z'), '.agents': (131194, '2026-11-09T14:17:24.293441Z'),
          '.aws': (131196, '2026-11-09T14:17:24.294441Z')}
KNOWN = 'v6pre-write.txt'
RECORD, STAGING = 'dot-codex-placeholder.json', '.placeholder-staging'
EXCLUDED_INO = (131200, 131192)
RECORD_FIELDS = ('dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns')  # = g3-start.RECORD_FIELDS
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


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def same_fields(entry, ino, mtime):
    return (entry.get('exists') is True and entry.get('dev') == '7:10' and entry.get('ino') == ino
            and entry.get('type') == 'dir' and entry.get('uid') == 10001 and entry.get('gid') == 10001
            and entry.get('mode') == '0o755' and entry.get('mtime') == mtime and entry.get('entries') == 0
            and isinstance(entry.get('selinux'), str) and entry['selinux'] != ''
            and not entry['selinux'].startswith('error'))  # r2: the label is read and printed, never compared


def evaluate(stage, o, expected, g3a):
    """The stage's expectations (design g3 r2 §3): the list of names of every one that did not hold."""
    failed = []

    def need(name, ok):
        if not ok:
            failed.append(name)
    e = o['entries']
    need('workspace listing', o['workspace_listing'].get('rc') == 0)
    codex, rec, staging = e['.codex'], o['g2_names'][RECORD], o['g2_names'][STAGING]
    if stage == 'g3-2':
        need('.codex absent', codex == {'exists': False})
        need('record absent', rec == {'exists': False})
        need('staging absent', staging == {'exists': False})
    else:
        need('.codex empty 0755 dir of uid 10001, not an excluded ino',
             codex.get('exists') is True and codex.get('type') == 'dir' and codex.get('mode') == '0o755'
             and codex.get('uid') == 10001 and codex.get('entries') == 0 and codex.get('ino') not in EXCLUDED_INO)
        need('record regular 0600 uid 10001 one link at most 64 KiB',
             rec.get('exists') is True and rec.get('type') == 'file' and rec.get('mode') == '0o600'
             and rec.get('uid') == 10001 and rec.get('nlink') == 1 and isinstance(rec.get('size'), int)
             and rec['size'] <= 65536 and HEX64.fullmatch(str(rec.get('sha256'))) is not None)
        need('staging empty 0700 dir', staging.get('exists') is True and staging.get('type') == 'dir'
             and staging.get('mode') == '0o700' and staging.get('entries') == 0)
        ref = (g3a or {}).get('codex') or {}
        need('.codex ino is the G3a ino', isinstance((g3a or {}).get('ino'), int) and codex.get('ino') == g3a['ino'])
        if stage == 'g3b':
            need('.codex dev/ino/mtime equal to G3a', [codex.get('ino'), codex.get('mtime_ns')]
                 == [ref.get('ino'), ref.get('mtime_ns')] and o['codexDevNumber'] == ref.get('dev'))
            # r1 (review code r0 major 2): every RECORD_FIELDS value and the sha256, as g3-start recorded them after
            # G3a (dev as the st_dev number; ctime not compared).
            ref_rec = (g3a or {}).get('record') or {}
            now = dict({k: rec.get(k) for k in RECORD_FIELDS if k != 'dev'}, dev=o['recordDevNumber'],
                       sha256=rec.get('sha256'))
            need('record %s equal to G3a' % '/'.join(RECORD_FIELDS + ('sha256',)),
                 set(ref_rec) == set(RECORD_FIELDS) | {'sha256'} and all(now[k] == ref_rec[k] for k in ref_rec))
    kept = sorted(n for n in e if n.startswith('codex-dir-kept-'))
    need('exactly the two kept directories', kept == sorted(KEPT))
    for name, (ino, mtime) in sorted(KEPT.items()) + sorted(OTHERS.items()):
        need('%s as accepted in E2' % name, same_fields(e.get(name, {}), ino, mtime))
    known = o['known']
    need('%s present, regular, uid 10001' % KNOWN, known.get('exists') is True and known.get('type') == 'file'
         and known.get('uid') == 10001)
    need('no other name', o['other_names_count'] == 0)
    need('owner Pods 0', o['owner_pods'] == {'rc': 0, 'count': 0})
    rows = o['registry']['rows']
    need('registry idle_stopped on generation %d, pod null, active 0, not ambiguous' % REGISTRY[stage],
         o['registry']['rc'] == 0 and len(rows) == 1 and isinstance(rows[0], dict)
         and {k: rows[0].get(k) for k in ('state', 'phase', 'gen', 'pod', 'active', 'ambiguous')}
         == {'state': STATE, 'phase': 'idle_stopped', 'gen': REGISTRY[stage], 'pod': None, 'active': 0,
             'ambiguous': False})
    need('ledgers at their pins', o['ledgers'] == LEDGERS)
    b = o['bindings']
    need('bindings lookup', b['rc'] == 0 and b['rows'] is not None)
    if b['rc'] == 0 and b['rows'] is not None:
        by = b['rows']
        need('…5805 binding on the new plan and image', by.get(OWNER) == {
            'state': STATE, 'planSha256': expected['newPlanSha256'], 'image': expected['newImage']})
        need('the other owners at their pins', sorted(o for o in by if o != OWNER) == sorted(expected['others'])
             and all(by[x] == {'state': p['state'], 'planSha256': p['planSha256'], 'image': p['image']}
                     and o['fences'].get(x) == [p['generation'], p['revision']]
                     for x, p in expected['others'].items()))
    return failed


def main(argv):
    o = {'owner': OWNER, 'version': 'g3-post-r2', 'time': utc(datetime.datetime.now().timestamp())}
    if len(argv) != 3 or argv[1] not in STAGES or not HEX64.fullmatch(argv[2]):
        print(json.dumps({'usage': 'g3-post.py g3-2|g3a|g3b <sha256 of g3-expected.json>', 'pass': False}))
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
    o['other_names_count'] = len([n for n in names if n not in watched and n != KNOWN])
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
    o['ledgers'] = {}
    for name in ('isolation-42', 'resources-6'):
        try:
            with open(os.path.join(EV, 'ledger-%s.json' % name), 'rb') as f:
                o['ledgers'][name] = hashlib.sha256(f.read()).hexdigest()
        except OSError as e:
            o['ledgers'][name] = 'error: %s' % type(e).__name__
    g3a = None
    if stage != 'g3-2':
        try:
            runs = sorted(d for d in os.listdir(EV) if re.fullmatch(r'g3-g3a-\d{8}T\d{6}Z', d))
            with open(os.path.join(EV, runs[0], 'run.json')) as f:
                g3a = json.load(f) if len(runs) == 1 else None
        except (OSError, ValueError, IndexError):
            g3a = None
        o['g3aRecord'] = {'found': g3a is not None, 'pass': (g3a or {}).get('g3a') == 'pass'}
    o['failed'] = evaluate(stage, o, expected, g3a)
    if stage != 'g3-2' and not o['g3aRecord']['pass']:
        o['failed'].append('the G3a run record passed')
    o['lookupsOk'] = lookups_ok and all('error' not in json.dumps(v) for v in (o['entries'], o['g2_names'], o['known']))
    o['pass'] = not o['failed'] and o['lookupsOk']
    if 'sha256' in o['g2_names'][RECORD]:
        o['g2_names'][RECORD]['sha256'] = 'recorded' if HEX64.fullmatch(o['g2_names'][RECORD]['sha256']) else 'error'
    print(json.dumps(o, sort_keys=True, indent=1))


if __name__ == '__main__':
    main(sys.argv)
