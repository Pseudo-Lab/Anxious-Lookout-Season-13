"""#61 read-only postcheck after the W2 rollback. Prints fixed fields only; no Secret data."""
import json
import subprocess

EV = '/root/issue61/evidence/w2-20261101T150903Z'
K = ['/usr/local/bin/k3s', 'kubectl']


def kget(kind, ns, name):
    args = K + ['get', kind, name, '-o', 'json', '--ignore-not-found'] + (['-n', ns] if ns else [])
    out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30).stdout.decode()
    return json.loads(out) if out.strip() else None


def body(obj):
    keys = ('spec', 'rules', 'subjects', 'roleRef', 'metadata.labels')
    result = {}
    for key in ('spec', 'rules', 'subjects', 'roleRef'):
        if key in obj:
            result[key] = obj[key]
    result['labels'] = obj['metadata'].get('labels')
    if obj['kind'] == 'Deployment':
        spec = json.loads(json.dumps(obj['spec']))
        result['spec'] = spec  # template incl. image; replicas
    return result


record = json.load(open(EV + '/backup.json'))
same = differ = 0
for e in record:
    k = (e['kind'], e['namespace'], e['name'])
    live = kget(*k)
    label = '%s %s/%s' % (e['kind'], e['namespace'] or '-', e['name'])
    if e['absent']:
        print('absent-before %s now %s' % (label, 'absent' if live is None else 'PRESENT uid=' + live['metadata']['uid']))
        continue
    if e['kind'] == 'Secret':
        print('secret %s uid %s (backup %s) keys %s' % (label, live and live['metadata']['uid'], e.get('uid'),
                                                       sorted((live or {}).get('data', {}).keys())))
        continue
    uid_ok = live is not None and live['metadata']['uid'] == e['object']['metadata']['uid']
    want, got = body(e['object']), (body(live) if live else None)
    if e['kind'] == 'Deployment':  # server defaults may be added; compare the fields W2 changed
        want = {'image': e['object']['spec']['template']['spec']['containers'][0]['image'],
                'replicas': e['object']['spec'].get('replicas')}
        got = None if live is None else {'image': live['spec']['template']['spec']['containers'][0]['image'],
                                         'replicas': live['spec'].get('replicas')}
    if e['kind'] == 'CiliumNetworkPolicy':
        want, got = e['object'].get('spec'), (live or {}).get('spec')
    ok = uid_ok and want == got
    same += ok
    differ += not ok
    print('%s %s uid=%s %s' % ('same' if ok else 'DIFFERS', label, live and live['metadata']['uid'],
                               '' if ok else json.dumps({'uidOk': uid_ok})))
print('restored objects equal to backup: %d, differing: %d' % (same, differ))
d = kget('deployment', 'm2-hosting', 'api')
print('api generation=%s observed=%s image=%s replicas=%s ready=%s updated=%s' % (
    d['metadata']['generation'], d['status'].get('observedGeneration'), d['spec']['template']['spec']['containers'][0]['image'],
    d['status'].get('replicas'), d['status'].get('readyReplicas'), d['status'].get('updatedReplicas')))
rs = json.loads(subprocess.run(K + ['-n', 'm2-hosting', 'get', 'rs', '-l', 'app=hosting-api', '-o', 'json'],
                               stdout=subprocess.PIPE, timeout=30).stdout)
for r in rs['items']:
    print('rs %s desired=%s ready=%s image=%s' % (r['metadata']['name'], r['spec'].get('replicas'),
                                                 r['status'].get('readyReplicas'), r['spec']['template']['spec']['containers'][0]['image']))
ep = kget('endpoints', 'm2-hosting', 'api')
print('endpoints api ready=%s notready=%s' % ([a['ip'] for s in ep.get('subsets', []) for a in s.get('addresses', [])],
                                              [a['ip'] for s in ep.get('subsets', []) for a in s.get('notReadyAddresses', [])]))
print('namespace runner-control %s' % ('absent' if kget('namespace', None, 'runner-control') is None else 'PRESENT'))
for owner, uid in (('0048', '3bb0fe46-af68-4d3a-9c67-35bce5f44cf7'), ('5802', '4107e603-0c33-4fb3-9fc3-911212a716fb'),
                   ('5804', 'b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b')):
    p = kget('pod', 'codex-runner', 'runner-0000000000004000800000000000' + owner if owner != '0048' else
             'runner-00000000000040008000000000000048')
    print('pod %s uid=%s phase=%s (want uid %s)' % (owner, p and p['metadata']['uid'], p and p['status'].get('phase'), uid))
q = subprocess.run(K + ['-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'hosting',
                        '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], input=b"""BEGIN READ ONLY;
SELECT 'registry '||owner_id||'|'||phase||'|'||generation||'|'||revision||'|'||coalesce(pod_uid::text,'-') FROM runner_runtime.registry WHERE owner_id::text LIKE '00000000-0000-4000-8000-0000000%' ORDER BY owner_id;
SELECT 'open stops '||count(*) FROM runner_termination.stops WHERE completed_at IS NULL;
SELECT 'grants061 '||has_column_privilege('anxious_runner_observer','research.conversations','error_code','UPDATE')||' '||has_column_privilege('anxious_runner_observer','runner_runtime.admissions','completed_at','UPDATE');
COMMIT;""", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60).stdout.decode()
print(q.strip())
quota = kget('resourcequota', 'codex-runner', 'runner-bootstrap-no-pods')
print('quota pods hard=%s used=%s' % (quota['spec']['hard'].get('count/pods'), quota['status'].get('used', {}).get('count/pods')))
