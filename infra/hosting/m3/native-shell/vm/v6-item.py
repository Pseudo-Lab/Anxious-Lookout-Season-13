"""#68 V6 item unit — bundle A (v6-item design r4 1128641d…; PM: bundles A -> B -> C, one commit each). Guest, root:

    python3 -I -B /root/native68/v6-item.py look
    python3 -I -B /root/native68/v6-item.py bootstrap <sha256 of bootstrap-pa2.json>
    python3 -I -B /root/native68/v6-item.py check <item> <ref> <ref sha256> [--reserve <failed ref> <failed sha256>]

Bundle A is read-only and has no turn path. The entrypoint accepts only look / bootstrap / check; `run` and `restart`
refuse (rc 2) before any file is read, and they open only when turn_missing() is empty: every item's prompt and
judge pinned and bundles A, B and C present (PM condition 4). In bundle A every ITEMS entry is a placeholder (no
prompt, no judge): `check` runs the whole guard and precheck, then refuses the item (rc 3), so no item has a success
path here. Nothing in this file writes a file, the database, the cluster or the API.

What bundle A implements (design r4 sections):
- §5 `look`: the required guest files' sha256 and size, printed for the manifest (PM runs it once; read-only);
- §5 `bootstrap`: the manifest at its sha256; every guest file at its manifest hash; the accepted pas2 run at review's
  hashes (rc 0, observer validated with no reason, pa-start r6 / observer r6 scripts = gen 15, refs.g5t digest =
  g5t ledgersAfter); the failed pas run preserved (six pins, nothing else, rc 16, never the accepted run); pa-switch
  result 0; g5t / g5r rc 0; pa-doc (rc 0, doc pass, ownerInvariants held, window = the accepted pas2 hashes and the
  failed run's pins, ids = the fixture); then the ledger (c) and registry (gen 15) on the current state;
- §3 the reference and the guard (a)-(g) on ONE raw snapshot of both ledger files; gen = reference gen + 1 (the first
  item 16; a bootstrap whose next gen is not 16 refuses);
- §4 the allocation v6 19 (the pinned v6 table + G4 1 + G5 1, reserve 3), the reserve flag form, the default and
  reserve counters apart, failed / unknown claims counted;
- §1.1 item_observer_judge (pure; bundle B calls it after a turn; never validate());
- §5.1 the V6-10 fixture precheck (read-only: pa-doc's own public-doc look query and an anonymous GET).

Record contract that bundle B's units must write (read here as references): run.json `item`, `gen`, `claim` (id or
null), `ledgersAfter` (g4-turn ledger_digests), `verdictClass`; exit.json `rc`, `finalInvariants`, `sessionRevoked`;
a restart: run.json `gen`, `claim` null, `checked` {name, sha256, digest}.

rc: 0 look printed / bootstrap holds; 2 usage, or run / restart (no turn in bundle A); 3 any refusal, a placeholder
item, or a failed lookup (nothing changed).
"""
import hashlib
import http.client
import json
import os
import re
import stat
import sys
import types

G4B_PATH, G4B_SHA = '/root/native68/g4b-turn.py', '9b90a66e8652d401a4f9e7a273aafc5262ed06374427dc1e53622433ca0cb736'
PAO_PATH, PAO_SHA = '/root/native68/pa-observe.py', '16024ba51a197ff121f322ec79575ae18582177817476a58a1179fbd4529cf3f'
PA_START_SHA = 'b8ce94897c8ac8411b7d708c36031b9c45dc5a43eb2e96126113501e85a4b3ea'  # the pas2 run's recorded script (pa-start r6: GEN 15, ACTION pas2)
CONTENT_PATH, CONTENT_SHA = '/root/native68/pa-doc-content.json', 'c5e66f195ab916dacab7b3c8d739bd8d3ef6a27a693d01cfcdfa36129d3f0eac'
BOOTSTRAP_PATH = '/root/native68/bootstrap-pa2.json'
BUNDLE = 'isolation-42'
RES6_PIN = '51e7ae651e8dcb79213082362fef2bd6873c11f2000b9567719974c61b6eafeb'
FIRST_GEN, BOOT_GEN = 16, 15
BUNDLES_PRESENT = ('A',)  # bundle B adds 'B' with the prompts and judges, bundle C adds 'C'
ITEMS = ('V6-1', 'V6-2', 'V6-3', 'V6-4', 'V6-5', 'V6-6', 'V6-7', 'V6-8', 'V6-10', 'X2')
V6_ITEMS = {'T5': 1, 'V6-1': 1, 'V6-2': 4, 'V6-3': 1, 'V6-4': 1, 'V6-5': 1, 'V6-6': 1, 'V6-7': 1, 'V6-8': 1,
            'V6-10': 1, 'X2': 1}  # v6-turns.py ALLOCATION (design §4); any other base table refuses
PROMPTS = {item: None for item in ITEMS}  # placeholders: no text exists in bundle A, so none can be sent
JUDGES = {item: None for item in ITEMS}
V6_TOTAL, RESERVE_TOTAL = 19, 3
# §5: the accepted pas2 run (review pa-pas2-accepted), the failed pas run (pa-start r6 pins), pa-doc and the fixture
# (review pa-doc-accepted).
PAS2_RUN = 'pa-start-pas2-20261111T165217Z'
PAS2_PINS = {'run.json': 'fe0ad820832b0b60ba6a69afc0a57887fb1e4148de2d902de8473f90931b401b', 'exit.json': 'fbf022a12c5c87f38ebf9c072e63d3e4647c7ba2f20ee375492bbb9e53539a97', 'observer.out': '205b480e309433045ea1347d2bcc238659f28d546fa3c36cf93688bcd7f2bfe4'}
FAILED_RUN = 'pa-start-pas-20261111T123436Z'
FAILED_PINS = {
    'exit.json': '72dfc1a79b30556923f7f2dc9f636a85c7299cbf021130e8aecb070260399910',
    'k4-pids.json': '3507403a21467ed1c36f4580519d099dac7167b303b4278a05c0771a26293687',
    'observer.err': 'a2ec3d4074abc856eb968b8430ebecb32889ec207e53c66e48ba8337dfe43aff',
    'observer.out': 'd139723f605053bd5ad9ed253d052703322fad2a79df8f72ae8870025437f455',
    'out.txt': '6d735563928f00e52512ca163265b0f0330100a8c27e09b28af7fadbe74d33a9',
    'run.json': 'c7753896b09586a0ad47ef8960cb0ac920c0fcba272beec8d5353f593f80944e',
}
PA_DOC_RUN, PA_DOC_RUN_SHA = 'pa-doc-20261111T173309Z', '607f8e1584ee3b9ce1f705fba27777a8cd4deb799deed14610bee6d7c0a9f802'
FIXTURE = {'documentId': '8e6eaf1f-5f33-428b-9763-f8383a6a89ae', 'versionId': '25c7390b-c51f-495f-994c-1288a83d3927', 'publicationId': '2a691178-5d53-4cab-8925-cb9a6afedc19'}
GUEST = (('g5t', r'g5-turn-\d{8}T\d{6}Z', ('run.json', 'exit.json')),
         ('g5r', r'g5-restart-g5r-\d{8}T\d{6}Z', ('run.json', 'exit.json')),
         ('pa-switch', r'pa-switch-\d{8}T\d{6}Z', ('run.json',)),
         ('pas2', r'pa-start-pas2-\d{8}T\d{6}Z', ('run.json', 'exit.json', 'observer.out', 'k4-pids.json')),
         ('failed', r'pa-start-pas-\d{8}T\d{6}Z', tuple(sorted(FAILED_PINS))),
         ('pa-doc', r'pa-doc-\d{8}T\d{6}Z', ('run.json', 'exit.json')))
ITEM_RUN = re.compile(r'v6-item-(%s)-(\d{8}T\d{6}Z)' % '|'.join(re.escape(i) for i in ITEMS))
RESTART_RUN = re.compile(r'v6-item-restart-(\d{8}T\d{6}Z)')
HEX = re.compile(r'[0-9a-f]{64}')
DECIMAL = re.compile(r'0|[1-9][0-9]*')
EVENT_PREFIX = ('F\t', 'A\t', 'S\t', 'E\t', 'X\t')
RESERVE_RC = (4, 6, 8, 9)
NEVER_RC = (5, 12, 15, 16)
LOOK_QUERY = ("SELECT json_build_object('count', count(*), 'ids', coalesce(json_agg(document_id ORDER "
              "BY document_id), '[]'::json)) FROM research.publication_heads WHERE owner_id='%s' AND kind='document' "
              "AND publication_id IS NOT NULL;")  # pa-doc.py published(), unchanged (a test pins the text)

G4B = G4 = P = V6 = OBS = None


class Refused(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def load_pinned(path, sha, name):
    data = open(path, 'rb').read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit('%s is not the pinned file; nothing done' % path)
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(data, path, 'exec'), module.__dict__)
    return module


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def turn_missing():
    """What a turn action still lacks; bundle A: everything (PM condition 4: no item turn before A, B, C)."""
    missing = ['bundle %s' % b for b in ('A', 'B', 'C') if b not in BUNDLES_PRESENT]
    missing += ['%s prompt' % i for i in ITEMS if not isinstance(PROMPTS.get(i), str) or not PROMPTS[i]]
    missing += ['%s judge' % i for i in ITEMS if not callable(JUDGES.get(i))]
    return missing


# Evidence (read-only) ---------------------------------------------------------------------------------------------
def evidence_names():
    try:
        return os.listdir(P.EVIDENCE)
    except OSError:
        raise Refused('the evidence directory is unreadable') from None


def read_bytes(folder, file):
    try:
        with open(os.path.join(P.EVIDENCE, folder, file), 'rb') as stream:
            return stream.read()
    except OSError:
        raise Refused('%s/%s is missing or unreadable' % (folder, file)) from None


def read_json(folder, file, sha=None):
    """One read: the JSON object of evidence/<folder>/<file>; with sha, the bytes must hash to it."""
    data = read_bytes(folder, file)
    if sha is not None and sha_bytes(data) != sha:
        raise Refused('%s/%s is not at its pin' % (folder, file))
    try:
        value = json.loads(data)
    except ValueError:
        raise Refused('%s/%s is not JSON' % (folder, file)) from None
    if not isinstance(value, dict):
        raise Refused('%s/%s is not a JSON object' % (folder, file))
    return value


def units():
    """v6-item-* evidence directories (item runs and restarts) in time order."""
    found = []
    for name in evidence_names():
        m = ITEM_RUN.fullmatch(name) or RESTART_RUN.fullmatch(name)
        if m:
            found.append((m.group(m.lastindex), name))
    return [name for _, name in sorted(found)]


def one_run(names, pattern):
    found = sorted(d for d in names if re.fullmatch(pattern, d))
    if len(found) != 1:
        raise Refused('expected exactly one %s run, found %d' % (pattern, len(found)))
    return found[0]


def ledger_snapshot():
    """ONE read of each ledger file (load_ledger's checks: a root 0600 regular file whose bundle and limits match);
    returns ({bundle: raw sha256}, {bundle: document}). Every guard check uses this one snapshot."""
    raw, docs = {}, {}
    for bundle, (path, limits) in P.LEDGERS.items():
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                raise Refused('ledger %s is not a root 0600 file' % bundle)
            with open(path, 'rb') as stream:
                data = stream.read()
            doc = json.loads(data)
        except (OSError, ValueError):
            raise Refused('ledger %s is unreadable' % bundle) from None
        if not isinstance(doc, dict) or doc.get('bundle') != bundle or doc.get('limits') != limits \
                or not isinstance(doc.get('claims'), list):
            raise Refused('ledger %s does not match its bundle' % bundle)
        raw[bundle], docs[bundle] = sha_bytes(data), doc
    return raw, docs


# §4 the allocation ----------------------------------------------------------------------------------------------
def build_table(v6_allocation):
    """The pinned v6 table of v6-turns.py + G4 1 + G5 1 (as g5-turn's build_allocation_g5): v6 19, of which 3 are
    reserve claims with no item allowance. Any other base table refuses (SystemExit, rc 3)."""
    name, total, items = v6_allocation[BUNDLE]
    if (name, total, items) != ('v6', 16, V6_ITEMS):
        raise SystemExit('the v6 allocation is not the pinned table; nothing done')
    items = dict(items, G4=1, G5=1)
    if sum(items.values()) + RESERVE_TOTAL != V6_TOTAL:
        raise SystemExit('the v6 allocation is not 19; nothing done')
    return name, V6_TOTAL, items


def reserve_form_problem(claims):
    """r2 §4: a v6 claim's `reserve` is absent (then no failedRef / failedSha), or exactly JSON true with string
    failedRef and a 64-hex failedSha; anything else fails the whole ledger."""
    for c in claims:
        if not isinstance(c, dict) or c.get('allocation') != 'v6':
            continue
        if 'reserve' not in c:
            if 'failedRef' in c or 'failedSha' in c:
                return 'a v6 claim has failedRef / failedSha without reserve'
        elif c['reserve'] is not True:
            return 'a v6 claim has a reserve flag that is not exactly true'
        elif not (isinstance(c.get('failedRef'), str) and isinstance(c.get('failedSha'), str)
                  and HEX.fullmatch(c['failedSha'])):
            return 'a reserve claim lacks failedRef / failedSha'
    return None


def allocation_problem(ledger, table, item, reserve):
    """§4 on the snapshot: None or the refusal. The default and reserve counters are separate; failed and unknown
    claims count (any claim of allocation v6, whatever its status)."""
    claims = ledger['claims']
    problem = reserve_form_problem(claims)
    if problem:
        return problem
    name, total, items = table
    if item not in ITEMS or item not in items:
        return 'no allowance for %s' % item
    v6 = [c for c in claims if isinstance(c, dict) and c.get('allocation') == name]
    default = sum(1 for c in v6 if c.get('item') == item and 'reserve' not in c)
    reserve_item = sum(1 for c in v6 if c.get('item') == item and c.get('reserve') is True)
    reserve_all = sum(1 for c in v6 if c.get('reserve') is True)
    if len(v6) >= total:
        return 'the v6 allocation (%d) is used' % total
    if not reserve and default >= items[item]:
        return 'the default allowance of %s (%d) is used; a reserve needs --reserve and a go that says so' % (
            item, items[item])
    if reserve:
        if default < items[item]:
            return 'a reserve while the default allowance of %s remains' % item
        if reserve_item:
            return 'the reserve of %s is already used' % item
        if reserve_all >= RESERVE_TOTAL:
            return 'the %d reserves are used' % RESERVE_TOTAL
    turns, inp, out = V6.used(ledger)
    limits = ledger['limits']
    if turns >= limits['turns']:
        return 'the %s bundle turn limit is reached' % BUNDLE
    if limits['input'] - inp < V6.START_INPUT:
        return 'the %s bundle has less than %d input left' % (BUNDLE, V6.START_INPUT)
    if limits['output'] - out < V6.START_OUTPUT:
        return 'the %s bundle has less than %d output left' % (BUNDLE, V6.START_OUTPUT)
    return None


# §5 bootstrap-pa2 -----------------------------------------------------------------------------------------------
def guest_dirs(names):
    dirs = {key: one_run(names, pattern) for key, pattern, _ in GUEST}
    if dirs['pas2'] != PAS2_RUN:
        raise Refused('the pas2 run is not the accepted %s' % PAS2_RUN)
    if dirs['failed'] != FAILED_RUN:
        raise Refused('the pas run is not exactly the preserved %s' % FAILED_RUN)
    if dirs['pa-doc'] != PA_DOC_RUN:
        raise Refused('the pa-doc run is not the accepted %s' % PA_DOC_RUN)
    return dirs


def look():
    """§5 the read-only guest look for the manifest: each required file's sha256 and size (one read each)."""
    dirs = guest_dirs(evidence_names())
    files = {}
    for key, _, names in GUEST:
        for file in names:
            data = read_bytes(dirs[key], file)
            files['%s/%s' % (dirs[key], file)] = {'sha256': sha_bytes(data), 'size': len(data)}
    return {'look': 'bootstrap-pa2', 'dirs': dirs, 'files': files}


def manifest_at(manifest_sha):
    try:
        with open(BOOTSTRAP_PATH, 'rb') as stream:
            data = stream.read()
    except OSError:
        raise Refused('bootstrap-pa2.json is missing or unreadable') from None
    if sha_bytes(data) != manifest_sha:
        raise Refused('bootstrap-pa2.json is not the reviewed manifest')
    try:
        manifest = json.loads(data)
    except ValueError:
        raise Refused('bootstrap-pa2.json is not JSON') from None
    if not isinstance(manifest, dict) or set(manifest) != {'version', 'accepted', 'guest', 'host', 'expect'} \
            or manifest['version'] != 'bootstrap-pa2' or not all(
                isinstance(manifest[k], dict) for k in ('accepted', 'guest', 'host', 'expect')):
        raise Refused('bootstrap-pa2.json is not the manifest form')
    return manifest


def bootstrap_check(manifest_sha):
    """§5: {'gen': 15, 'digest': the bound isolation-42 canonical} or Refused (rc 3)."""
    manifest = manifest_at(manifest_sha)
    accepted, guest, expect = manifest['accepted'], manifest['guest'], manifest['expect']
    pas2_acc, doc_acc = accepted.get('pas2'), accepted.get('paDoc')
    if not isinstance(pas2_acc, dict) or pas2_acc.get('run') != PAS2_RUN or pas2_acc.get('run') == FAILED_RUN or any(
            pas2_acc.get(f) != pin for f, pin in PAS2_PINS.items()):
        raise Refused('the manifest does not name the accepted pas2 run at review\'s hashes')
    if not isinstance(doc_acc, dict) or doc_acc.get('run') != PA_DOC_RUN or doc_acc.get('run.json') != PA_DOC_RUN_SHA:
        raise Refused('the manifest does not name the accepted pa-doc run')
    if expect.get('gen') != BOOT_GEN or expect.get('resources6') != RES6_PIN or not isinstance(expect.get('digest'), str):
        raise Refused('the manifest expect is not gen %d with the resources-6 pin and a digest' % BOOT_GEN)
    dirs = guest_dirs(evidence_names())
    wanted = {'%s/%s' % (dirs[key], file) for key, _, files in GUEST for file in files}
    if set(guest) != wanted or not all(isinstance(v, str) and HEX.fullmatch(v) for v in guest.values()):
        raise Refused('the manifest guest list is not exactly the required files with sha256 values')
    for label in sorted(wanted):
        folder, file = label.split('/', 1)
        if sha_bytes(read_bytes(folder, file)) != guest[label]:
            raise Refused('%s is not at its manifest hash' % label)
    for file, pin in PAS2_PINS.items():
        if guest['%s/%s' % (PAS2_RUN, file)] != pin:
            raise Refused('the manifest pas2 %s is not the accepted hash' % file)
    for file, pin in FAILED_PINS.items():
        if guest['%s/%s' % (FAILED_RUN, file)] != pin:
            raise Refused('the failed pas run %s is not at its preserved pin' % file)
    if guest['%s/run.json' % PA_DOC_RUN] != PA_DOC_RUN_SHA:
        raise Refused('the manifest pa-doc run.json is not the accepted hash')
    try:
        held = sorted(os.listdir(os.path.join(P.EVIDENCE, FAILED_RUN)))
    except OSError:
        raise Refused('the failed pas run is unreadable') from None
    if held != sorted(FAILED_PINS):
        raise Refused('the failed pas run holds other files')
    if read_json(FAILED_RUN, 'exit.json', FAILED_PINS['exit.json']).get('rc') != 16:
        raise Refused('the failed pas run is not rc 16')
    for key in ('g5t', 'g5r'):
        if read_json(dirs[key], 'exit.json', guest['%s/exit.json' % dirs[key]]).get('rc') != 0:
            raise Refused('the %s run did not end rc 0' % key)
    switch = read_json(dirs['pa-switch'], 'run.json', guest['%s/run.json' % dirs['pa-switch']])
    if switch.get('result') != 0 or switch.get('cleanupFailed') != []:
        raise Refused('the pa-switch run is not result 0 with no cleanup failure')
    pas2 = read_json(PAS2_RUN, 'run.json', PAS2_PINS['run.json'])
    observer = pas2.get('observer') if isinstance(pas2.get('observer'), dict) else {}
    if read_json(PAS2_RUN, 'exit.json', PAS2_PINS['exit.json']).get('rc') != 0 \
            or observer.get('state') != 'validated' or observer.get('reasons') != []:
        raise Refused('the pas2 run is not rc 0 with its observer validated')
    if pas2.get('action') != 'pas2' or pas2.get('script') != PA_START_SHA or pas2.get('observe') != PAO_SHA:
        raise Refused('the pas2 run was not made by pa-start r6 (gen %d) with observer r6' % BOOT_GEN)
    g5t = read_json(dirs['g5t'], 'run.json', guest['%s/run.json' % dirs['g5t']])
    digest = ((g5t.get('ledgersAfter') or {}).get(BUNDLE) or {}).get('canonical')
    refs = observer.get('refs') if isinstance(observer.get('refs'), dict) else {}
    if (not digest or digest != expect['digest'] or (refs.get('g5t') or {}).get('digest') != digest
            or (refs.get('g5t') or {}).get('name') != dirs['g5t'] or (refs.get('g5r') or {}).get('name') != dirs['g5r']
            or (refs.get('g5t') or {}).get('sha256') != guest['%s/run.json' % dirs['g5t']]
            or (refs.get('g5r') or {}).get('sha256') != guest['%s/run.json' % dirs['g5r']]):
        raise Refused('the pas2 refs are not the g5t / g5r runs with the g5t ledgersAfter digest')
    doc = read_json(PA_DOC_RUN, 'run.json', PA_DOC_RUN_SHA)
    if read_json(PA_DOC_RUN, 'exit.json', guest['%s/exit.json' % PA_DOC_RUN]).get('rc') != 0 \
            or doc.get('doc') != 'pass' or (doc.get('ownerInvariants') or {}).get('held') is not True:
        raise Refused('the pa-doc run is not a pass with its owner invariants held')
    if doc.get('window') != {'pas2': PAS2_RUN, 'runSha256': PAS2_PINS['run.json'],
                             'exitSha256': PAS2_PINS['exit.json'], 'failedRun': {'name': FAILED_RUN,
                                                                                  'pins': FAILED_PINS}}:
        raise Refused('the pa-doc window is not the accepted pas2 run and the preserved failed run')
    if {k: doc.get(k) for k in FIXTURE} != FIXTURE:
        raise Refused('the pa-doc ids are not the pinned V6-10 fixture')
    return {'gen': BOOT_GEN, 'digest': digest}


# §3 the reference and the guard -----------------------------------------------------------------------------------
def unit_end(name):
    """(run.json, exit.json) of a unit; a missing exit.json refuses (§3 a / b)."""
    return read_json(name, 'run.json'), read_json(name, 'exit.json')


def later_problem(later, claims):
    """§3 (b): after the reference only rc 3 units with no claim in run.json and none in the ledger."""
    for name in later:
        try:
            record, end = unit_end(name)
        except Refused:
            return 'a later unit %s has no readable run.json / exit.json' % name
        if end.get('rc') != 3 or record.get('claim') is not None or any(
                isinstance(c, dict) and c.get('run') == name for c in claims):
            return 'the reference is not the latest unit (%s follows it)' % name
    return None


def reference(ref, ref_sha, reserve, claims, all_units):
    """(kind, gen, bound digest, record) or Refused: §3 (a), (d), (e) and §4 (R-a)-(R-c)."""
    if ref == 'bootstrap-pa2':
        if reserve:
            raise Refused('a reserve takes the failed item run as its reference, never bootstrap-pa2')
        boot = bootstrap_check(ref_sha)
        return 'bootstrap', boot['gen'], boot['digest'], {}
    if ref not in all_units:
        raise Refused('the reference %s is not a v6-item unit' % ref)
    record = read_json(ref, 'run.json', ref_sha)                                                      # (a)
    end = read_json(ref, 'exit.json')
    restart = RESTART_RUN.fullmatch(ref) is not None
    gen = record.get('gen')
    digest = ((record.get('checked') or {}).get('digest') if restart
              else ((record.get('ledgersAfter') or {}).get(BUNDLE) or {}).get('canonical'))
    if type(gen) is not int or gen < FIRST_GEN or not isinstance(digest, str):
        raise Refused('the reference lacks its gen (>= %d) or its ledger digest' % FIRST_GEN)
    rc = end.get('rc')
    if type(rc) is not int or rc in NEVER_RC or end.get('finalInvariants') is not True:                   # (e)
        raise Refused('the reference (rc %r, finalInvariants %r) is never allowed' % (rc, end.get('finalInvariants')))
    mine = [c for c in claims if isinstance(c, dict) and c.get('run') == ref]
    if restart:
        if record.get('claim') is not None or mine:                                                     # (d)
            raise Refused('a restart reference holds a claim')
    else:
        if record.get('item') != ITEM_RUN.fullmatch(ref).group(1):
            raise Refused('the reference run.json names another item')
        if (not record.get('claim') or len(mine) != 1 or mine[0].get('id') != record['claim']
                or mine[0].get('status') != 'settled'):
            raise Refused('the reference claim is not exactly one and settled')
        if end.get('sessionRevoked') is not True:
            raise Refused('the reference session was not revoked')
    if reserve:
        if restart:
            raise Refused('a reserve takes a failed item run, not a restart')
        if not (rc in RESERVE_RC or (rc in (0, 14) and record.get('verdictClass') == 'not run')):     # (R-b)
            raise Refused('the failed run (rc %r, %r) is not a reserve reason' % (rc, record.get('verdictClass')))
    elif rc not in (0, 14):
        raise Refused('the reference rc %r is not 0 or 14' % rc)
    return ('restart' if restart else 'item'), gen, digest, record


def guard(item, ref, ref_sha, reserve, table):
    """§3 (a)-(g) on ONE ledger snapshot; returns the facts or raises Refused."""
    if item not in ITEMS:
        raise Refused('unknown item %s' % item)
    if reserve and (reserve[0] != ref or reserve[1] != ref_sha):
        raise Refused('--reserve must name <ref> and <ref sha256> themselves (the failed run is the latest unit)')
    raw, docs = ledger_snapshot()
    claims = docs[BUNDLE]['claims']
    all_units = units()
    kind, gen, digest, record = reference(ref, ref_sha, reserve, claims, all_units)
    later = all_units if kind == 'bootstrap' else all_units[all_units.index(ref) + 1:]
    problem = later_problem(later, claims)                                                            # (b)
    if problem:
        raise Refused(problem)
    if reserve and record.get('item') != item:
        raise Refused('a reserve for another item')
    if G4.canonical(docs[BUNDLE]) != digest:                                                          # (c)
        raise Refused('the ledger is not the reference\'s bound digest')
    if raw['resources-6'] != RES6_PIN:
        raise Refused('resources-6 is not at its raw pin')
    problem = V6.unsettled_problem(docs)                                                              # (f)
    if problem:
        raise Refused('base gate: %s' % problem)
    problem = allocation_problem(docs[BUNDLE], table, item, bool(reserve))                            # (g)
    if problem:
        raise Refused(problem)
    if kind == 'bootstrap' and gen + 1 != FIRST_GEN:
        raise Refused('the first item would be gen %d, not %d' % (gen + 1, FIRST_GEN))
    return {'kind': kind, 'gen': gen + 1, 'digest': digest, 'ledgers': G4.ledger_digests(docs)}


def owner_idle(gen):
    state = P.registry()
    if state.get('phase') != 'idle_stopped' or state.get('pod') is not None or state.get('gen') != gen:
        raise Refused('…5805 is not idle_stopped at gen %d without a Pod' % gen)
    if P.runner_pods():
        raise Refused('a runner Pod exists')


# §1.1 the item turn's observer judgement (pure) ------------------------------------------------------------------
def is_int(value):
    return type(value) is int  # bool is not an int here


def capture_problems_item(summary, lines):
    """r1 (review bundle A major 2): the capture summary is complete and agrees with the captured lines before any
    reused judge sees it. state 'stopped'; exit exactly the int 0; lost and capped exactly False; lines = the number
    of captured lines; endSeen exactly True; exactly one READY line 'READY\\t<decimal>\\t5' whose value equals
    clock[1], with clock = three ints before <= READY <= after (the runtime clock check, read from the record)."""
    found = []
    if summary.get('state') != 'stopped':
        found.append('the observer is not stopped (%r)' % (summary.get('state'),))
    if not (is_int(summary.get('exit')) and summary['exit'] == 0):
        found.append('the observer exit is not the int 0 (%r)' % (summary.get('exit'),))
    for key in ('lost', 'capped'):
        if summary.get(key) is not False:
            found.append('the capture field %s is not exactly false (%r)' % (key, summary.get(key)))
    if not (is_int(summary.get('lines')) and summary['lines'] == len(lines)):
        found.append('the summary line count %r is not the %d captured lines' % (summary.get('lines'), len(lines)))
    if summary.get('endSeen') is not True:
        found.append('the summary does not record an END line')
    ready = [l for l in lines if l.startswith('READY\t')]
    parts = ready[0].split('\t') if len(ready) == 1 else []
    value = int(parts[1]) if len(parts) == 3 and DECIMAL.fullmatch(parts[1]) and parts[2] == '5' else None
    if len(ready) != 1:
        found.append('%d READY lines (exactly one needed)' % len(ready))
    elif value is None:
        found.append('a malformed READY line')
    clock = summary.get('clock')
    if not (isinstance(clock, (list, tuple)) and len(clock) == 3 and all(is_int(c) for c in clock)
            and clock[0] <= clock[1] <= clock[2] and value is not None and clock[1] == value):
        found.append('the runtime clock check is missing, failed or not the READY value')
    return found


def k4_problems(k4, driver):
    """r1 (review bundle A major 1): judge_k4 checks the trace against the pids written in the record, so the record
    itself must first be K4's: exactly 20 valid, unique pids per syscall (execve / execveat, no pid in both), exactly
    40 statuses all the int 0, and a valid driver identity equal to the one the lineage uses."""
    found = []
    if not (isinstance(driver, (list, tuple)) and len(driver) == 2 and all(is_int(v) for v in driver)
            and driver[0] > 0 and driver[1] >= 0):
        found.append('the K4 driver identity is missing or malformed')
    if not isinstance(k4, dict):
        return found + ['K4 not recorded']
    pids = k4.get('pids')
    if not (isinstance(pids, dict) and set(pids) == {'execve', 'execveat'}):
        found.append('the K4 pid sets are not exactly execve / execveat')
    else:
        for way in ('execve', 'execveat'):
            got = pids[way]
            if not (isinstance(got, list) and len(got) == 20 and all(is_int(p) and p > 0 for p in got)
                    and len(set(got)) == 20):
                found.append('the K4 %s pids are not 20 valid unique pids' % way)
        if not found and set(pids['execve']) & set(pids['execveat']):
            found.append('a K4 pid is in both sets')
    statuses = k4.get('statuses')
    if not (isinstance(statuses, list) and len(statuses) == 40 and all(is_int(s) and s == 0 for s in statuses)):
        found.append('the K4 statuses are not exactly 40 zeros')
    recorded = k4.get('driver')
    if not (isinstance(recorded, (list, tuple)) and isinstance(driver, (list, tuple))
            and list(recorded) == list(driver)):
        found.append('the K4 record\'s driver is not the driver the lineage uses')
    return found


def item_observer_judge(summary, lines, r1, driver, k4, time_ns, problems):
    """(clean, reasons, detail) for one item turn. Inputs, as bundle B passes them from pa-observe r6:
    summary = Observer.summary(); lines = Observer.lines (stdout); r1 = snapshot_r1(); driver = Observer.driver_id;
    k4 = Observer.run_k4()'s record or None; time_ns = time_namespace(r1)'s result or None; problems = the message of
    every ObserverError the caller caught (start incl. the clock check, snapshot_r1, time_namespace, run_k4, stop) and
    any capture failure, in order. A missing or malformed input, any problem, or an exception here is a reason: the
    judgement never raises and never comes out clean by default. validate() (the pas2 validation) is not used."""
    reasons, detail = [], {'attach': [], 'endCount': None, 'rejected': 0, 'events': None}
    try:
        if not isinstance(problems, list) or not all(isinstance(p, str) for p in problems):
            reasons.append('the problem list is malformed')
        else:
            reasons += ['observer: %s' % p for p in problems]
        if not isinstance(summary, dict) or not isinstance(lines, list) or not all(isinstance(l, str) for l in lines):
            return False, reasons + ['the observer capture is missing or malformed'], detail
        reasons += capture_problems_item(summary, lines)
        if OBS.layout_of(lines) != 5:
            reasons.append('the observer output is not layout 5')
        ends = [l for l in lines if l.startswith('END\t')]
        end_count = None
        if len(ends) != 1:
            reasons.append('%d END lines (exactly one needed)' % len(ends))
        elif not DECIMAL.fullmatch(ends[0][4:]):
            reasons.append('a malformed END line')
        else:
            end_count = int(ends[0][4:])
            if summary.get('endLine') != ends[0]:
                reasons.append('the summary END line differs from the captured one')
        ready = [l for l in lines if l.startswith('READY\t')]
        head = ready[:1] or ['READY\t0\t5']
        rejected = [l for l in lines if l.startswith(EVENT_PREFIX) and len(OBS.parse(head + [l])) != 1]
        detail['rejected'] = len(rejected)
        if rejected:
            reasons.append('%d event line(s) the parser rejects' % len(rejected))
        events = OBS.parse(lines)
        detail.update(events=len(events), endCount=end_count)
        if end_count is not None and end_count != len(events):
            reasons.append('END %d != %d parsed events' % (end_count, len(events)))
        reasons += OBS.clean_exit({'exit': summary.get('exit'), 'lost': summary.get('lost'),
                                   'capped': summary.get('capped'), 'endSeen': end_count is not None,
                                   'endCount': end_count}, len(events))
        detail['attach'] = [{'t': e['t'], 'pid': e['pid']} for e in events if e['k'] == 'A']
        if detail['attach']:
            reasons.append('%d attach event(s) into the container cgroup (no K session in an item turn)'
                           % len(detail['attach']))
        if not isinstance(r1, list) or not r1:
            reasons.append('no R1 snapshot')
            r1 = []
        elif not all(isinstance(e, (list, tuple)) and len(e) == 5 and all(is_int(v) for v in e) for e in r1):
            reasons.append('an R1 entry is not (pid, ticks, userns, mntns, pidns)')
            r1 = []
        else:
            try:
                detail['pidns'] = OBS.container_pidns(r1)
            except OBS.ObserverError as error:
                reasons.append(error.message)
        pids = {entry[0] for entry in r1}
        if not (isinstance(time_ns, dict) and isinstance(time_ns.get('host'), str)
                and isinstance(time_ns.get('runner'), dict) and pids and set(time_ns['runner']) == pids
                and all(v == time_ns['host'] for v in time_ns['runner'].values())):
            reasons.append('the time namespace check is missing or failed')
        nodes, parent_of, roots, unattributed = OBS.lineage(events, r1, driver, {}, anchors={})
        detail['roots'] = sorted(set(roots.values()))
        detail['unattributed'] = len(unattributed)
        if set(roots.values()) - {'R0', 'R1'}:
            reasons.append('a root other than R0 / R1')
        if unattributed:
            reasons.append('%d unattributed node(s) (no exact edge to R0 / R1)' % len(unattributed))
        k4_bad = k4_problems(k4, driver)
        reasons += k4_bad
        if not k4_bad:  # judge_k4 only on a record that is K4's own (20 + 20 pids, 40 zero statuses, this driver)
            reasons += OBS.judge_k4(events, k4, parent_of, roots)
    except Exception as error:  # a malformed record is never clean
        reasons.append('the judgement failed on malformed input (%s)' % type(error).__name__)
    return not reasons, reasons, detail


# §5.1 the V6-10 precheck (read-only) -----------------------------------------------------------------------------
def fixture_precheck(look_rows, public_get):
    """look_rows() -> pa-doc's look result; public_get(path) -> (status, body). Refused on any difference."""
    try:
        with open(CONTENT_PATH, 'rb') as stream:
            data = stream.read()
    except OSError:
        raise Refused('pa-doc-content.json is missing') from None
    if sha_bytes(data) != CONTENT_SHA:
        raise Refused('pa-doc-content.json is not the reviewed file')
    content = json.loads(data)
    if look_rows() != {'count': 1, 'ids': [FIXTURE['documentId']]}:
        raise Refused('the public-doc look is not exactly the fixture document')
    status, body = public_get('/api/public/documents/' + FIXTURE['documentId'])
    body = body if isinstance(body, dict) else {}
    text = lambda v: sha_bytes(v.encode('utf-8')) if isinstance(v, str) else None
    if (status != 200 or (body.get('documentId'), body.get('versionId'), body.get('id')) != (
            FIXTURE['documentId'], FIXTURE['versionId'], FIXTURE['publicationId'])
            or (text(body.get('title')), text(body.get('content'))) != (text(content['title']),
                                                                       text(content['content']))):
        raise Refused('the anonymous public read is not the pinned fixture')
    return {'look': 'count 1, the fixture id', 'public': 200}


def public_look():
    rows = P.sql('v6item-look', LOOK_QUERY % P.NEW, 3)
    if len(rows) != 1:
        raise Refused('the public-doc look answered %d rows' % len(rows))
    return rows[0]


def anonymous_get(path):
    """Host and Accept only: no cookie, no session."""
    connection = http.client.HTTPConnection(P.API_HOST, P.API_PORT, timeout=60)
    try:
        connection.request('GET', path, headers={'Host': P.HOST_HEADER, 'Accept': 'application/json'})
        response = connection.getresponse()
        status, raw = response.status, response.read(1 << 20)
    except Exception:
        return None, None
    finally:
        connection.close()
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


# Actions ----------------------------------------------------------------------------------------------------------
def say(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=False))


def action_look():
    try:
        say(look())
    except Refused as refused:
        say({'look': 'refused', 'reason': refused.message})
        return 3
    return 0


def action_bootstrap(manifest_sha):
    """§5 on its own: the manifest and the guest files, then (c) and the registry at gen 15 on the current state."""
    try:
        boot = bootstrap_check(manifest_sha)
        raw, docs = ledger_snapshot()
        if G4.canonical(docs[BUNDLE]) != boot['digest'] or raw['resources-6'] != RES6_PIN:
            raise Refused('the ledgers are not the bootstrap digest / the resources-6 pin')
        owner_idle(boot['gen'])
    except (Refused, P.Stop) as refused:
        say({'bootstrap': 'refused', 'reason': refused.message})
        return 3
    say({'bootstrap': 'holds', 'gen': boot['gen'], 'firstItemGen': boot['gen'] + 1, 'digest': boot['digest']})
    return 0


def action_check(item, ref, ref_sha, reserve):
    """The guard, the owner precheck and (V6-10) the fixture precheck, then the placeholder refusal: in bundle A no
    item is runnable, so check never ends rc 0."""
    try:
        table = build_table(V6.ALLOCATION)
    except SystemExit as refused:
        say({'check': 'refused', 'stage': 'table', 'reason': str(refused)})
        return 3
    try:
        facts = guard(item, ref, ref_sha, reserve, table)
        owner_idle(facts['gen'] - 1)
        if item == 'V6-10':
            facts['fixture'] = fixture_precheck(public_look, anonymous_get)
    except (Refused, P.Stop) as refused:
        say({'check': 'refused', 'stage': 'guard', 'reason': refused.message})
        return 3
    out = {'check': 'refused', 'stage': 'item', 'guard': 'pass', 'item': item, 'gen': facts['gen'],
           'reference': facts['kind'], 'reserve': bool(reserve), 'fixture': facts.get('fixture')}
    if PROMPTS.get(item) is None or JUDGES.get(item) is None:
        out['reason'] = ('%s is a placeholder in bundle A (no pinned prompt / judge); no item turn before bundles A, '
                         'B and C are approved and committed' % item)
        say(out)
        return 3
    out['reason'] = 'bundle A has no turn path'
    say(out)
    return 3


ACTIONS = ('look', 'bootstrap', 'check')
USAGE = ('usage: v6-item.py look | bootstrap <sha256 of bootstrap-pa2.json> | check <item> <ref> <ref sha256> '
         '[--reserve <failed ref> <failed sha256>]')


def parse_args(argv):
    """(action, rest) or None. check: (item, ref, sha, reserve or None)."""
    if len(argv) == 2 and argv[1] == 'look':
        return 'look', ()
    if len(argv) == 3 and argv[1] == 'bootstrap' and HEX.fullmatch(argv[2]):
        return 'bootstrap', (argv[2],)
    if len(argv) in (5, 8) and argv[1] == 'check' and argv[2] in ITEMS and HEX.fullmatch(argv[4]):
        if len(argv) == 5:
            return 'check', (argv[2], argv[3], argv[4], None)
        if argv[5] == '--reserve' and HEX.fullmatch(argv[7]):
            return 'check', (argv[2], argv[3], argv[4], (argv[6], argv[7]))
    return None


def main(argv):
    global G4B, G4, P, V6, OBS
    if len(argv) > 1 and argv[1] not in ACTIONS:
        if argv[1] in ('run', 'restart'):
            print('v6-item.py: %s is not available: %s' % (argv[1], ', '.join(turn_missing()) or 'no turn path'))
        else:
            print(USAGE)
        return 2
    args = parse_args(argv)
    if args is None:
        print(USAGE)
        return 2
    if os.geteuid() != 0:
        print('run as root')
        return 2
    try:  # a pin refusal before anything is read: rc 3, nothing done
        G4B = load_pinned(G4B_PATH, G4B_SHA, 'g4b_turn')
        G4 = G4B.load_g4(G4B.G4_PATH, G4B.G4_SHA)
        P = G4.load_pinned(G4B.BASE_PATH, G4B.BASE_SHA, 'v6pre_turns')
        V6 = G4.load_pinned(G4B.V6_PATH, G4B.V6_SHA, 'v6_turns')
        OBS = load_pinned(PAO_PATH, PAO_SHA, 'pa_observe')
        V6.P = P
    except SystemExit as refused:
        print(refused)
        return 3
    action, rest = args
    if action == 'look':
        return action_look()
    if action == 'bootstrap':
        return action_bootstrap(*rest)
    return action_check(*rest)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
