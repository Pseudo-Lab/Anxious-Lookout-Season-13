"""#61 read-only postcheck after the W2 rollback. Prints fixed fields only; no Secret data.

Every lookup checks its return code: a failed lookup prints LOOKUP FAILED and makes the run exit 3, so a failure
can never read as "absent" or as a complete comparison. Comparison scope: RBAC/SA rules, subjects, roleRef,
labels; ResourceQuota/CNP spec; api Deployment image and replicas only; Secrets by UID and keys only.
"""
import json
import subprocess
import sys

EV = '/root/issue61/evidence/w2-20261101T150903Z'
K = ['/usr/local/bin/k3s', 'kubectl']
FAILED = []
SQL = b"""BEGIN READ ONLY;
SELECT 'registry '||owner_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-')
  FROM runner_runtime.registry WHERE owner_id::text LIKE '00000000-0000-4000-8000-0000000%' ORDER BY owner_id;
SELECT 'open stops '||count(*) FROM runner_termination.stops WHERE completed_at IS NULL;
SELECT 'grants061 '||has_column_privilege('anxious_runner_observer','research.conversations','error_code','UPDATE')
  ||' '||has_column_privilege('anxious_runner_observer','runner_runtime.admissions','completed_at','UPDATE');
COMMIT;"""
PODS = (('0048', 'runner-00000000000040008000000000000048', '3bb0fe46-af68-4d3a-9c67-35bce5f44cf7'),
        ('5802', 'runner-00000000000040008000000000005802', '4107e603-0c33-4fb3-9fc3-911212a716fb'),
        ('5804', 'runner-00000000000040008000000000005804', 'b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b'))


class LookupFailed(Exception):
    pass


def call(args, stdin=None):
    try:
        done = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except Exception:
        raise LookupFailed() from None
    if done.returncode != 0:
        raise LookupFailed()
    return done.stdout.decode()


def kget(kind, ns, name):
    """The object; None only when the API answered that it does not exist. A failed lookup raises."""
    out = call(K + ['get', kind, name, '-o', 'json', '--ignore-not-found'] + (['-n', ns] if ns else []))
    return json.loads(out) if out.strip() else None


def section(label, function):
    try:
        function()
    except (LookupFailed, ValueError, KeyError, TypeError, AttributeError):
        FAILED.append(label)
        print('LOOKUP FAILED: ' + label)


def compare_backup():
    record = json.load(open(EV + '/backup.json'))
    same = differ = 0
    for e in record:
        label = '%s %s/%s' % (e['kind'], e['namespace'] or '-', e['name'])
        try:
            live = kget(e['kind'], e['namespace'], e['name'])
        except LookupFailed:
            FAILED.append(label)
            print('LOOKUP FAILED: ' + label)
            continue
        if e['absent']:
            print('absent-before %s now %s' % (label, 'absent' if live is None else 'PRESENT uid=' + live['metadata']['uid']))
            continue
        if e['kind'] == 'Secret':
            print('secret %s uid %s (backup %s) keys %s' % (label, live and live['metadata']['uid'], e.get('uid'),
                                                           sorted((live or {}).get('data', {}).keys())))
            continue
        uid_ok = live is not None and live['metadata']['uid'] == e['object']['metadata']['uid']
        if e['kind'] == 'Deployment':
            pick = lambda o: {'image': o['spec']['template']['spec']['containers'][0]['image'],
                              'replicas': o['spec'].get('replicas')}
        elif e['kind'] == 'CiliumNetworkPolicy':
            pick = lambda o: o.get('spec')
        else:
            pick = lambda o: dict({key: o.get(key) for key in ('spec', 'rules', 'subjects', 'roleRef')},
                                  labels=o['metadata'].get('labels'))
        ok = uid_ok and pick(e['object']) == pick(live)
        same += ok
        differ += not ok
        print('%s %s uid=%s' % ('same' if ok else 'DIFFERS', label, live and live['metadata']['uid']))
    print('restored objects equal to backup: %d, differing: %d' % (same, differ))


def api():
    d = kget('deployment', 'm2-hosting', 'api')
    print('api generation=%s observed=%s image=%s replicas=%s ready=%s updated=%s' % (
        d['metadata']['generation'], d['status'].get('observedGeneration'),
        d['spec']['template']['spec']['containers'][0]['image'], d['status'].get('replicas'),
        d['status'].get('readyReplicas'), d['status'].get('updatedReplicas')))
    rs = json.loads(call(K + ['-n', 'm2-hosting', 'get', 'rs', '-l', 'app=hosting-api', '-o', 'json']))
    for r in rs['items']:
        print('rs %s desired=%s ready=%s image=%s' % (r['metadata']['name'], r['spec'].get('replicas'),
              r['status'].get('readyReplicas'), r['spec']['template']['spec']['containers'][0]['image']))
    ep = kget('endpoints', 'm2-hosting', 'api')
    print('endpoints api ready=%s notready=%s' % (
        [a['ip'] for s in ep.get('subsets', []) for a in s.get('addresses', [])],
        [a['ip'] for s in ep.get('subsets', []) for a in s.get('notReadyAddresses', [])]))


def namespace():
    ns = kget('namespace', None, 'runner-control')
    print('namespace runner-control %s' % ('absent' if ns is None else 'PRESENT uid=%s phase=%s' % (
        ns['metadata']['uid'], (ns.get('status') or {}).get('phase'))))


def fixtures():
    for owner, name, uid in PODS:
        p = kget('pod', 'codex-runner', name)
        print('pod %s uid=%s phase=%s (want uid %s)' % (owner, p and p['metadata']['uid'],
                                                       p and p['status'].get('phase'), uid))


def database():
    print(call(K + ['-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'hosting',
                    '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], stdin=SQL).strip())


def quota():
    q = kget('resourcequota', 'codex-runner', 'runner-bootstrap-no-pods')
    print('quota pods hard=%s used=%s' % (q['spec']['hard'].get('count/pods'), q['status'].get('used', {}).get('count/pods')))


for label, function in (('backup comparison', compare_backup), ('api', api), ('namespace', namespace),
                        ('fixtures', fixtures), ('database', database), ('quota', quota)):
    section(label, function)
print('lookups failed: %d%s' % (len(FAILED), (' (' + '; '.join(FAILED) + ')') if FAILED else ''))
sys.exit(3 if FAILED else 0)
