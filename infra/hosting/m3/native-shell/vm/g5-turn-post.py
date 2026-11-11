"""#68 G5 turn read-only post-check (g5-post.py 1f4398f6…, committed a1322b6, with the turn stages; G5 design r2
§4.4). Guest, root; once per stage; changes nothing:

    ssh … 'sudo -n python3 -I -B - <stage> <sha256 of g5-expected.json>' < g5-turn-post.py      # stage: b1 | b2 | r13

b1:  optional, while the g5-turn unit waits for the idle stop (the turn returned; the gen 12 Pod may still run).
b2:  after the g5-turn unit ended (the natural idle stop of gen 12).
r13: after the g5-restart unit ended (gen 13 idle_stopped).
What changes against g5-post.py: the stages and their generations; the ledgers are judged by the G5 turn's claim
(g4b-post.py's ledger_evidence with the G5 names: the claim id the g5-turn run recorded, present once, v6 / G5 /
settled, run = that evidence directory; isolation-42 without it and resources-6 equal to the run's ledgersBefore),
not by fixed pins; at b1 the owner may have one Pod and the registry is not required idle; and (PM, review g5s
acceptance) a read-only preservation check of the g5s evidence: evidence/g5-start-g5s-*/run.json and exit.json
readable, exit rc 0, and run.json codexHomeReads = three reads (1, 2, 3) each without a problem (booleans only).
CODEX_HOME at every stage: config.toml and rules absent, exactly one kept file, the one E3 renamed (the other
entries counted, never judged: a pass does not prove them unchanged). b2 / r13 with config.toml absent after the
turn's thread/start is (a′) fix evidence together with the g5-turn and g5-restart unit verdicts.
Everything else is g5-post.py: the G3a reference, the kept directories and .git / .agents / .aws, v6pre-write.txt,
g4-src, no other workspace name, the owner's Pods, the registry, every owner's binding; the same byte-identical
helpers. Output: one JSON object of names, booleans, counts, ino numbers and lookup rc; `failed` lists every
expectation that did not hold; `pass` is true only when that list is empty and every lookup succeeded.
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
CODEX_HOME = STATE_ROOT + '/codex'
EV = '/root/native68/evidence'
EXPECTED_FILE = '/root/native68/g5-expected.json'
K3S = '/usr/local/bin/k3s'
STAGES = {'b1': 12, 'b2': 12, 'r13': 13}  # stage -> registry generation
KEPT_REF = {'dev': 1802, 'ino': 131205, 'type': 'file', 'uid': 10001, 'gid': 10001, 'mode': 0o600, 'nlink': 1, 'size': 54, 'mtime_ns': 1794342704373636030}
KEPT_NAME = re.compile(r'config\.toml\.kept-\d{8}T\d{6}Z')
LABEL = 'system_u:object_r:container_file_t:s0:c576,c788'  # the E2 value, reference only (g3-post r2)
KEPT = {'codex-dir-kept-20261110T090017Z': (131192, '2026-11-09T14:17:24.293441Z'),
        'codex-dir-kept-20261110T170732Z': (131200, '2026-11-10T10:23:29.233552Z')}
OTHERS = {'.git': (131193, '2026-11-09T14:17:24.293441Z'), '.agents': (131194, '2026-11-09T14:17:24.293441Z'),
          '.aws': (131196, '2026-11-09T14:17:24.294441Z')}
KNOWN = 'v6pre-write.txt'
G4_NAME = 'g4-src'
G4SRC_REF = {'ino': 131208, 'mtime_ns': 1794342715963798350}  # G4 post b2 (1f4ff752…); reused, never changed
RECORD, STAGING = 'dot-codex-placeholder.json', '.placeholder-staging'
RECORD_FIELDS = ('dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns')
HEX64 = re.compile(r'[0-9a-f]{64}')


LEDGER_EVIDENCE = ('claim id recorded by the G5 run', 'the claim present exactly once', 'the claim is v6 / G5 / settled',
                   'the claim belongs to the G5 run read', 'isolation-42 without the claim equals before',
                   'resources-6 equals before')


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def ledger_evidence(docs, g4, run_name):
    """Booleans only: the G5 run's recorded claim id and before-digests against the ledgers now (= g5-turn's
    ledgers_changed_only_by_g5, from the recorded digests). `docs`: the parsed ledgers (memory only). `run_name`: the
    name of the G5 turn evidence directory that was read (r2, g4b-post r2: the expected `run` comes from there, never
    from the claim itself)."""
    g4 = g4 or {}
    claim_id = g4.get('g5Claim')
    before = g4.get('ledgersBefore') or {}
    iso, res = docs.get('isolation-42'), docs.get('resources-6')
    out = dict.fromkeys(LEDGER_EVIDENCE, False)
    out['claim id recorded by the G5 run'] = isinstance(claim_id, str) and bool(claim_id)
    if not isinstance(iso, dict) or not isinstance(res, dict) or not out['claim id recorded by the G5 run']:
        return out
    mine = [c for c in iso.get('claims', []) if isinstance(c, dict) and c.get('id') == claim_id]
    out['the claim present exactly once'] = len(mine) == 1
    out['the claim is v6 / G5 / settled'] = len(mine) == 1 and (
        mine[0].get('allocation'), mine[0].get('item'), mine[0].get('status')) == ('v6', 'G5', 'settled')
    out['the claim belongs to the G5 run read'] = (len(mine) == 1 and isinstance(run_name, str)
                                                   and re.fullmatch(r'g5-turn-\d{8}T\d{6}Z', run_name) is not None
                                                   and mine[0].get('run') == run_name)
    rest = dict(iso, claims=[c for c in iso.get('claims', []) if not (isinstance(c, dict) and c.get('id') == claim_id)])
    out['isolation-42 without the claim equals before'] = canonical(rest) == (before.get('isolation-42') or {}).get(
        'canonical')
    out['resources-6 equals before'] = canonical(res) == (before.get('resources-6') or {}).get('canonical')
    return out


def preserved_g5s(ev):
    """Read-only booleans: the g5s evidence (PM / review g5s acceptance memo)."""
    out = {'one g5s run': False, 'run.json readable': False, 'exit.json rc 0': False,
           'codexHomeReads three reads without a problem': False}
    try:
        found = sorted(d for d in os.listdir(ev) if re.fullmatch(r'g5-start-g5s-\d{8}T\d{6}Z', d))
    except OSError:
        return out
    out['one g5s run'] = len(found) == 1
    if len(found) != 1:
        return out
    try:
        with open(os.path.join(ev, found[0], 'run.json')) as f:
            rec = json.load(f)
        out['run.json readable'] = isinstance(rec, dict)
        reads = rec.get('codexHomeReads') if isinstance(rec, dict) else None
        out['codexHomeReads three reads without a problem'] = (
            isinstance(reads, list) and [r.get('read') for r in reads if isinstance(r, dict)] == [1, 2, 3]
            and all(r.get('problem') is None for r in reads))
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(ev, found[0], 'exit.json')) as f:
            out['exit.json rc 0'] = json.load(f).get('rc') == 0
    except (OSError, ValueError, AttributeError):
        pass
    return out


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




def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()



def component_reason(fd, part):
    """Why a path component could not be opened as a no-follow directory: its own no-follow lstat, relative to the
    parent fd (open with O_NOFOLLOW | O_DIRECTORY gives ENOTDIR for a symlink too, so errno alone cannot tell)."""
    try:
        mode = os.lstat(part, dir_fd=fd).st_mode
    except FileNotFoundError:
        return 'path_absent'
    except OSError:
        return 'path_error'
    return 'path_symlink' if stat.S_ISLNK(mode) else 'path_not_dir' if not stat.S_ISDIR(mode) else 'path_error'


def open_home(path):
    """(fd, reason). r1 (review major): walk the fixed absolute path one component at a time from `/`, each opened
    with O_DIRECTORY | O_NOFOLLOW relative to its parent's fd, so a symlink or a non-directory
    anywhere on the path, CODEX_HOME itself included, refuses the observation. The directory fds are never read for
    content; every later lookup is relative to the CODEX_HOME fd, so a path swap after the walk is not followed."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    parts = [p for p in path.split('/') if p]
    if not path.startswith('/') or any(p in ('.', '..') for p in parts):
        return None, 'path_error'
    fd = os.open('/', flags)
    for part in parts:
        try:
            nfd = os.open(part, flags, dir_fd=fd)
        except OSError:
            reason = component_reason(fd, part)
            os.close(fd)
            return None, reason
        os.close(fd)
        fd = nfd
    return fd, None




def ident(info):
    kind = 'file' if stat.S_ISREG(info.st_mode) else 'dir' if stat.S_ISDIR(info.st_mode) else \
        'symlink' if stat.S_ISLNK(info.st_mode) else 'other'
    return {'dev': info.st_dev, 'ino': info.st_ino, 'type': kind, 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode), 'nlink': info.st_nlink, 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns}


def codex_home_view():
    """Booleans and counts only (names stay in memory): config.toml (and whether it is the look r1 file), rules, the
    kept files (count, and whether the one is the file E3 renamed), the count of all other entries."""
    try:
        home, reason = open_home(CODEX_HOME)
    except OSError:
        return {'ok': False, 'reason': 'path_error'}
    if home is None:
        return {'ok': False, 'reason': reason}
    try:
        names = os.listdir(home)
        config = ident(os.lstat('config.toml', dir_fd=home)) if 'config.toml' in names else None
        kept = [ident(os.lstat(n, dir_fd=home)) for n in names if KEPT_NAME.fullmatch(n)]
        return {'ok': True, 'config': config is not None,
                'configIsLookR1': config is not None and all(config.get(k) == v for k, v in KEPT_REF.items()),
                'rules': 'rules' in names, 'kept': len(kept),
                'keptAsE3': len(kept) == 1 and all(kept[0].get(k) == v for k, v in KEPT_REF.items()),
                'others': len([n for n in names if n not in ('config.toml', 'rules') and not KEPT_NAME.fullmatch(n)])}
    except OSError:
        return {'ok': False, 'reason': 'lookup_error'}
    finally:
        os.close(home)


def evaluate(stage, o, expected, ref):
    """The stage's expectations: the list of names of every one that did not hold. `ref` = the G3a run record."""
    failed = []

    def need(name, ok):
        if not ok:
            failed.append(name)
    gen = STAGES[stage]
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
    need('g4-src exactly as G4 left it (an empty dir of uid 10001, the same ino and mtime_ns)', g4.get('exists') is True
         and g4.get('type') == 'dir' and g4.get('uid') == 10001 and g4.get('entries') == 0
         and all(g4.get(k) == v for k, v in G4SRC_REF.items()) and g4.get('dev') == codex.get('dev'))
    need('no other name', o['other_names_count'] == 0)
    h = o['codexHome']
    need('CODEX_HOME lookup', h.get('ok') is True)
    need('CODEX_HOME/rules absent', h.get('ok') is True and h.get('rules') is False)
    need('CODEX_HOME/config.toml absent', h.get('ok') is True and h.get('config') is False)
    need('exactly one config.toml.kept-<UTC>, the file E3 renamed', h.get('ok') is True and h.get('kept') == 1
         and h.get('keptAsE3') is True)
    rows = o['registry']['rows']
    row = rows[0] if o['registry']['rc'] == 0 and len(rows) == 1 and isinstance(rows[0], dict) else {}
    need('registry on generation %d of this state, not ambiguous' % gen,
         row.get('state') == STATE and row.get('gen') == gen and row.get('ambiguous') is False)
    if stage == 'b1':
        need('owner Pods at most 1', o['owner_pods'].get('rc') == 0 and o['owner_pods'].get('count') in (0, 1))
    else:
        need('owner Pods 0', o['owner_pods'] == {'rc': 0, 'count': 0})
        need('registry idle_stopped, pod null, active 0', row.get('phase') == 'idle_stopped' and row.get('pod') is None
             and row.get('active') == 0)
    for key in LEDGER_EVIDENCE:
        need('ledger evidence: %s' % key, o['ledgerEvidence'].get(key) is True)
    for key, value in sorted(o['g5sPreserved'].items()):
        need('g5s evidence preserved: %s' % key, value is True)
    b = o['bindings']
    need('bindings lookup', b['rc'] == 0 and b['rows'] is not None)
    if b['rc'] == 0 and b['rows'] is not None:
        by = b['rows']
        need('…5805 binding on the G5 plan and image', by.get(OWNER) == {
            'state': STATE, 'planSha256': expected['newPlanSha256'], 'image': expected['newImage']})
        need('the other owners at their pins', sorted(x for x in by if x != OWNER) == sorted(expected['others'])
             and all(by[x] == {'state': p['state'], 'planSha256': p['planSha256'], 'image': p['image']}
                     and o['fences'].get(x) == [p['generation'], p['revision']]
                     for x, p in expected['others'].items()))
    return failed


def main(argv):
    o = {'owner': OWNER, 'version': 'g5-turn-post-r0', 'time': utc(datetime.datetime.now().timestamp())}
    if len(argv) != 3 or argv[1] not in STAGES or not HEX64.fullmatch(argv[2]):
        print(json.dumps({'usage': 'g5-turn-post.py b1|b2|r13 <sha256 of g5-expected.json>', 'pass': False}))
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
        o['failed'], o['pass'] = ['g5-expected.json is not the reviewed file'], False
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
    o['codexHome'] = codex_home_view()
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
    turn, run_name = None, None
    try:
        runs = sorted(d for d in os.listdir(EV) if re.fullmatch(r'g5-turn-\d{8}T\d{6}Z', d))
        with open(os.path.join(EV, runs[0], 'run.json')) as f:
            turn = json.load(f) if len(runs) == 1 else None
        run_name = runs[0] if turn is not None else None
        o['g5Run'] = {'found': turn is not None, 'g5t': (turn or {}).get('g5t'),
                      'attempts': [a.get('cls') for a in (turn or {}).get('attempts', [])]}
    except (OSError, ValueError, IndexError):
        o['g5Run'] = {'found': False}
    o['ledgerEvidence'] = ledger_evidence(docs, turn, run_name)
    o['g5sPreserved'] = preserved_g5s(EV)
    ref = None
    try:
        runs = sorted(d for d in os.listdir(EV) if re.fullmatch(r'g3-g3a-\d{8}T\d{6}Z', d))
        with open(os.path.join(EV, runs[0], 'run.json')) as f:
            ref = json.load(f) if len(runs) == 1 else None
    except (OSError, ValueError, IndexError):
        ref = None
    o['g3aRecord'] = {'found': ref is not None, 'pass': (ref or {}).get('g3a') == 'pass'}
    o['failed'] = evaluate(stage, o, expected, ref)
    if not o['g3aRecord']['pass']:
        o['failed'].append('the G3a run record passed')
    o['lookupsOk'] = lookups_ok and o['codexHome'].get('ok') is True and all(
        'error' not in json.dumps(v) for v in (o['entries'], o['g2_names'], o['known'], o['g4src'], o['ledgers']))
    o['pass'] = not o['failed'] and o['lookupsOk']
    if 'sha256' in o['g2_names'][RECORD]:
        o['g2_names'][RECORD]['sha256'] = 'recorded' if HEX64.fullmatch(o['g2_names'][RECORD]['sha256']) else 'error'
    print(json.dumps(o, sort_keys=True, indent=1))


if __name__ == '__main__':
    main(sys.argv)
