"""Offline D4 rollout renderer. No cluster, credential, Docker or systemd calls."""
import argparse
import hashlib
from copy import deepcopy
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID

ROOT = '/var/lib/anxious-lookout/issue16-d4'
STORAGE = '/var/lib/anxious-lookout/user-runners'
CONFIG = '/etc/anxious-lookout/issue16'
BIN = '/usr/local/lib/anxious-lookout/issue16'
NS = 'codex-runner'
KEYS = {'version', 'runtimeImage', 'runnerImage', 'provisionerImageId', 'postImagesStorageClass', 'api', 'hostApi',
        'namespaceUid', 'podCIDR', 'node', 'providerAccountId', 'allowlist', 'maxPods', 'idleSeconds', 'nativeSizeGiB', 'controlSizeGiB'}
IMAGE = r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}'


def require(ok):
    if not ok:
        raise ValueError('Rollout input refused')


def unique(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj)
        obj[key] = value
    return obj


def read(path):
    require(path.is_file() and path.stat().st_size <= 2_000_000)
    return json.loads(path.read_text(), object_pairs_hook=unique)


def canonical_uuid(value):
    require(isinstance(value, str) and str(UUID(value)) == value)


def checked(v):
    require(type(v) is dict and set(v) in (KEYS, KEYS | {'executor'}) and type(v['version']) is int and v['version'] == 1)
    sc = v['postImagesStorageClass']
    require(type(sc) is str and len(sc) <= 253 and all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in sc.split('.')))
    if 'executor' in v:
        e = v['executor']
        require(type(e) is dict and set(e) == {'image', 'wallSeconds', 'outputBytes'})
        require(isinstance(e['image'], str) and re.fullmatch(IMAGE, e['image']))
        require(type(e['wallSeconds']) is int and 1 <= e['wallSeconds'] <= 300)
        require(type(e['outputBytes']) is int and 1024 <= e['outputBytes'] <= 1048576)
    for key in ('runtimeImage', 'runnerImage'):
        require(isinstance(v[key], str) and re.fullmatch(IMAGE, v[key]))
    require(re.fullmatch(r'sha256:[a-f0-9]{64}', v['provisionerImageId']))
    for key in ('api', 'hostApi'):
        u = urlsplit(v[key])
        require(u.scheme == 'https' and u.hostname and not u.username and not u.password
                and not u.query and not u.fragment and u.path in ('', '/') and u.port)
    canonical_uuid(v['namespaceUid'])
    require(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', v['node']))
    network = ipaddress.ip_network(v['podCIDR'])
    require(network.version == 4 and not network.is_loopback and not network.is_link_local)
    require(isinstance(v['providerAccountId'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', v['providerAccountId']))
    for key, maximum in [('maxPods', 100), ('idleSeconds', 86400),
                         ('nativeSizeGiB', 999999), ('controlSizeGiB', 999999)]:
        require(type(v[key]) is int and 1 <= v[key] <= maximum)
    require(v['allowlist'] is None or (type(v['allowlist']) is list and 0 < len(v['allowlist']) <= 100
                                     and len(set(v['allowlist'])) == len(v['allowlist'])))
    for owner in v['allowlist'] or []:
        canonical_uuid(owner)
    return v


def pool(v, host=False):
    return dict(version=2, api=v['hostApi' if host else 'api'], namespace=NS,
                namespaceUid=v['namespaceUid'], podCIDR=v['podCIDR'],
                caFile='/private/ca.crt' if host else '/run/runner-kube/ca.crt',
                kubeTokenFile='/private/kube-token' if host else '/run/runner-kube/token')


def environment(v):
    env = {'CODEX_USER_RUNNERS_ENABLE': 'true', 'CODEX_USER_RUNNER_MAX_PODS': str(v['maxPods']),
           'CODEX_USER_RUNNER_IDLE_SECONDS': str(v['idleSeconds']),
           'CODEX_RUNNER_NATIVE_SIZE_GIB': str(v['nativeSizeGiB']),
           'CODEX_RUNNER_CONTROL_SIZE_GIB': str(v['controlSizeGiB'])}
    if v['allowlist'] is not None:
        env['CODEX_USER_RUNNER_ALLOWLIST'] = ','.join(v['allowlist'])
    return env


INIT = '''import os
from pathlib import Path
from runner.auth import private_directory, read_private
assert os.geteuid() == 10001
root = Path('/run/pool-private/private')
root.mkdir(mode=0o700, exist_ok=True)
private_directory(root)
value = Path('/run/pool-input/pool.json').read_bytes()
assert 0 < len(value) <= 65536
path = root/'pool.json'
if path.exists():
    assert read_private(path, json_format=False).encode() == value
else:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(value)
        target.flush()
        os.fsync(target.fileno())
'''


def deployment(v, baseline, configmaps=()):
    require(baseline['kind'] == 'Deployment' and baseline['apiVersion'] == 'apps/v1'
            and baseline['metadata']['namespace'] == 'm2-hosting' and baseline['metadata']['name'] == 'api')
    canonical_uuid(baseline['metadata']['uid'])
    require(re.fullmatch(r'[0-9]+', baseline['metadata']['resourceVersion']))
    result = deepcopy(baseline)
    spec = result['spec']['template']['spec']
    require(spec.get('serviceAccountName') == 'hosting')
    containers = spec['containers']
    require(len(containers) == 1 and containers[0]['name'] == 'api')
    api = containers[0]
    # Preserve ConfigMap values as references, but freeze the list of keys so
    # envFrom cannot silently reintroduce singleton settings at a later restart.
    inherited = {}
    for source in api.pop('envFrom', []):
        require(set(source) == {'configMapRef'} and set(source['configMapRef']) == {'name'})
        name = source['configMapRef']['name']
        matches = [c for c in configmaps if c['kind'] == 'ConfigMap'
                   and c['metadata']['name'] == name and c['metadata']['namespace'] == 'm2-hosting']
        require(len(matches) == 1)
        for key in matches[0].get('data', {}):
            inherited[key] = {'name': key, 'valueFrom': {'configMapKeyRef': {'name': name, 'key': key}}}
    inherited.update({e['name']: e for e in api.get('env', [])})
    retired = {'CODEX_USER_RUNNER_CONFIG_FILE', 'CODEX_USER_RUNNER_ACCOUNT_ID', 'CODEX_RUNNERS_FILE',
               'CODEX_USER_RUNNER_ALLOWLIST', 'CODEX_USER_RUNNER_POOL_CONFIG_FILE'}
    env = environment(v)
    env['POST_IMAGE_ROOT'] = '/var/lib/posts/images/data'
    env['CODEX_USER_RUNNER_POOL_CONFIG_FILE'] = '/run/pool-private/private/pool.json'
    api['env'] = [e for e in inherited.values() if e['name'] not in retired | set(env)
                  and not e['name'].startswith('CODEX_PERSONAL_')]
    api['env'] += [{'name': k, 'value': val} for k, val in env.items()]
    api['image'], api['imagePullPolicy'] = v['runtimeImage'], 'Never'
    old_volumes = {'post-images', 'runner-input', 'runner-private', 'runner-kube', 'pool-input', 'pool-private', 'owner-input'}
    api['volumeMounts'] = [m for m in api.get('volumeMounts', []) if m['name'] not in old_volumes]
    api['volumeMounts'] += [{'name': n, 'mountPath': p, 'readOnly': True} for n, p in
                           [('pool-private', '/run/pool-private'), ('runner-kube', '/run/runner-kube')]]
    api['volumeMounts'].append({'name': 'post-images', 'mountPath': '/var/lib/posts/images'})
    spec['initContainers'] = [c for c in spec.get('initContainers', [])
                              if c['name'] not in {'runner-private-input', 'runner-pool-input'}]
    require(not any(m['name'] in old_volumes for c in spec['initContainers'] for m in c.get('volumeMounts', [])))
    security = {'runAsUser': 10001, 'runAsGroup': 10001, 'runAsNonRoot': True,
                'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                'capabilities': {'drop': ['ALL']}}
    spec['initContainers'].append({'name': 'runner-pool-input', 'image': v['runtimeImage'], 'imagePullPolicy': 'Never',
        'command': ['python', '-B', '-c', INIT], 'securityContext': security,
        'volumeMounts': [{'name': 'pool-input', 'mountPath': '/run/pool-input', 'readOnly': True},
                         {'name': 'pool-private', 'mountPath': '/run/pool-private'}]})
    spec['securityContext'] = {**spec.get('securityContext', {}), 'runAsUser': 10001, 'runAsGroup': 10001,
                               'runAsNonRoot': True, 'fsGroup': 10001, 'fsGroupChangePolicy': 'OnRootMismatch'}
    spec['automountServiceAccountToken'] = False
    spec['volumes'] = [x for x in spec.get('volumes', []) if x['name'] not in old_volumes]
    spec['volumes'] += [
        {'name': 'post-images', 'persistentVolumeClaim': {'claimName': 'hosting-post-images'}},
        {'name': 'pool-input', 'secret': {'secretName': 'hosting-runner-pool-v2', 'defaultMode': 288}},
        {'name': 'pool-private', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '1Mi'}},
        {'name': 'runner-kube', 'projected': {'defaultMode': 288, 'sources': [
            {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 3600}},
            {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}}]
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': baseline['metadata']['uid']},
             {'op': 'test', 'path': '/metadata/resourceVersion', 'value': baseline['metadata']['resourceVersion']},
             {'op': 'replace', 'path': '/spec/template/spec', 'value': spec}]
    result.pop('status', None)
    result['metadata'].pop('managedFields', None)
    result['metadata'].get('annotations', {}).pop('kubectl.kubernetes.io/last-applied-configuration', None)
    return result, patch


def resource(kind, name, namespace=None, **fields):
    return {'apiVersion': 'v1' if kind in {'ServiceAccount', 'Secret', 'ResourceQuota'} else 'rbac.authorization.k8s.io/v1',
            'kind': kind, 'metadata': {'name': name, **({'namespace': namespace} if namespace else {})}, **fields}


def rule(resources, verbs, group='', names=None):
    return {'apiGroups': [group], 'resources': resources, 'verbs': verbs,
            **({'resourceNames': names} if names else {})}


CONTROL_NS = 'runner-control'


def controller(v):
    """In-cluster runner controller (#61): its own namespace (the codex-runner CNP denies the API
    server to every Pod there), a dedicated SA and a single resident Deployment."""
    subject = [{'kind': 'ServiceAccount', 'name': 'runner-controller', 'namespace': CONTROL_NS}]
    role = [rule(['pods'], ['get', 'list', 'watch', 'create', 'delete']),
            rule(['persistentvolumeclaims', 'secrets'], ['get'])]
    cluster = [rule(['namespaces'], ['get'], names=[NS]), rule(['nodes'], ['get'], names=[v['node']])]
    items = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': CONTROL_NS, 'labels': {
                 'pod-security.kubernetes.io/enforce': 'restricted', 'pod-security.kubernetes.io/enforce-version': 'latest'}}},
             resource('ServiceAccount', 'runner-controller', CONTROL_NS)]
    for kind, ns, permissions in [('Role', NS, role), ('ClusterRole', None, cluster)]:
        items.append(resource(kind, 'runner-controller', ns, rules=permissions))
        items.append(resource(kind+'Binding', 'runner-controller', ns, subjects=subject,
                              roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': kind, 'name': 'runner-controller'}))
    env = {**environment(v), 'RUNNER_NAMESPACE': NS, 'RUNNER_NAMESPACE_UID': v['namespaceUid'],
           'RUNNER_POD_CIDR': v['podCIDR'], 'RUNNER_NODE_NAME': v['node'],
           'CONTROLLER_DATABASE_URL_FILE': '/run/runner-db/controller-url',
           'OBSERVER_DATABASE_URL_FILE': '/run/runner-db/observer-url'}
    labels = {'app': 'runner-controller'}
    items.append({'apiVersion': 'apps/v1', 'kind': 'Deployment',
        'metadata': {'name': 'runner-controller', 'namespace': CONTROL_NS, 'labels': labels},
        'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'},  # One controller at a time.
                 'selector': {'matchLabels': labels},
                 'template': {'metadata': {'labels': labels}, 'spec': {
                     'serviceAccountName': 'runner-controller', 'enableServiceLinks': False,
                     'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'runAsNonRoot': True,
                                         'seccompProfile': {'type': 'RuntimeDefault'}},
                     'containers': [{'name': 'controller', 'image': v['runtimeImage'], 'imagePullPolicy': 'Never',
                         'command': ['python', '-B', '-m', 'ops.runner_controller_service'],
                         'env': [{'name': k, 'value': val} for k, val in env.items()],
                         'resources': {'requests': {'cpu': '50m', 'memory': '128Mi'}},
                         'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                             'capabilities': {'drop': ['ALL']}},
                         'volumeMounts': [{'name': 'db', 'mountPath': '/run/runner-db', 'readOnly': True},
                                          {'name': 'tmp', 'mountPath': '/tmp'}]}],
                     # Secret runner-controller-db (keys controller-url, observer-url) is created by the
                     # operator from the existing #16 logins; it is never rendered.
                     'volumes': [{'name': 'db', 'secret': {'secretName': 'runner-controller-db', 'defaultMode': 288}},
                                 {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}]}}}})
    return items


def resources(v):
    items = []
    for role in ('api', 'controller'):
        sa = 'hosting' if role == 'api' else 'runner-pool-'+role
        sa_ns = 'm2-hosting' if role == 'api' else NS
        if role != 'api':
            items.append(resource('ServiceAccount', sa, NS, automountServiceAccountToken=False))
        rules = [rule(['pods', 'persistentvolumeclaims', 'secrets'], ['get'])]
        cluster = [rule(['namespaces'], ['get'], names=[NS])]
        if role == 'controller':
            # Host storage preparation only since #61: PVC, PV and Secret, never Pods.
            rules += [rule(['persistentvolumeclaims', 'secrets'], ['create']),
                      rule(['serviceaccounts'], ['get'], names=['runner-workload']),
                      rule(['networkpolicies'], ['get'], 'networking.k8s.io', ['runner-default-deny'])]
            cluster += [rule(['persistentvolumes'], ['get', 'create'])]
        subject = [{'kind': 'ServiceAccount', 'name': sa, 'namespace': sa_ns}]
        for kind, name, ns, permissions in [('Role', 'runner-pool-'+role, NS, rules),
                                          ('ClusterRole', 'runner-pool-'+role, None, cluster)]:
            items.append(resource(kind, name, ns, rules=permissions))
            items.append(resource(kind+'Binding', name, ns, subjects=subject,
                                  roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': kind, 'name': name}))
    # Replace old subjects as well as old rules: RBAC is additive.
    items += controller(v)
    # Host supply (#60) and the host observer (#61, absorbed by runner-controller) are retired.
    for kind, name, ns in [('Role', 'runner-web', NS), ('Role', 'runner-preparer', NS), ('Role', 'runner-pool-supply', NS),
                          ('Role', 'runner-pool-observer', NS),
                          ('ClusterRole', 'runner-web-namespace', None), ('ClusterRole', 'codex-runner-preparer', None),
                          ('ClusterRole', 'runner-pool-supply', None), ('ClusterRole', 'runner-pool-observer', None)]:
        items.append(resource(kind, name, ns, rules=[]))
        items.append(resource(kind+'Binding', name, ns, subjects=[],
                              roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': kind, 'name': name}))
    items.append(resource('Secret', 'hosting-runner-pool-v2', 'm2-hosting', type='Opaque',
                          stringData={'pool.json': json.dumps(pool(v))}))
    items.append(resource('ResourceQuota', 'runner-bootstrap-no-pods', NS, spec={'hard': {'count/pods': str(v['maxPods'])}}))
    return {'apiVersion': 'v1', 'kind': 'List', 'items': items}


def inputs(v):
    p = pool(v, True)
    return {'controller': {k: p[k] for k in ('api', 'namespace', 'namespaceUid', 'podCIDR', 'caFile', 'kubeTokenFile')} |
            {'hostRoot': STORAGE, 'node': v['node'], 'providerAccountId': v['providerAccountId'],
             'image': v['runnerImage'], 'callback': 'http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools',
             **({'executor': deepcopy(v['executor'])} if 'executor' in v else {})}}


def shell_quote(value):
    import shlex
    return shlex.quote(str(value))


def docker(v, role):
    args = ['/usr/bin/docker', 'run', '--rm', '--pull=never', '--cidfile=/run/issue16-'+role+'/container-id', '--user=0:0',
            '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--tmpfs=/tmp:rw,nosuid,nodev,size=64m',
            '-e', 'PYTHONPATH=/app']
    def mount(src, dst=None, options='readonly'):
        args.extend(['--mount', 'type=bind,src='+src+',dst='+(dst or src)+(','+options if options else '')])
    mount(BIN, '/rollout')
    args += ['--network=none' if role == 'restore' else '--network=host']
    if role in {'controller', 'restore'}:
        args += ['--cap-add=SYS_ADMIN', '--cap-add=MKNOD', '--cap-add=CHOWN', '--cap-add=DAC_OVERRIDE',
                 '--cap-add=FOWNER', '--device=/dev/loop-control', '--device-cgroup-rule=b 7:* rwm',
                 '--security-opt=apparmor=unconfined']
        mount('/dev', options='')
        mount(STORAGE, options='bind-propagation=rshared')
        mount(ROOT+'/provision-output', options='readonly' if role == 'restore' else '')
    if role != 'restore':
        mount(CONFIG+'/'+role, '/private')
        for key, val in environment(v).items():
            args += ['-e', key+'='+val]
    if role == 'controller':
        args += ['-e', 'DATABASE_URL_FILE=/private/database-url', '-e', 'CONTROLLER_DATABASE_URL_FILE=/private/database-url']
    if role == 'restore':
        for key in ('CODEX_RUNNER_NATIVE_SIZE_GIB', 'CODEX_RUNNER_CONTROL_SIZE_GIB'):
            args += ['-e', key+'='+environment(v)[key]]
    args += ['--entrypoint=python', v['provisionerImageId'], '-B', '/rollout/worker.py', role]
    if role != 'restore':
        args += ['--inputs', '/private/inputs.json', '--limit', '10']
        args += ['--outputs', ROOT+'/provision-output']
    return args


def provenance(expected):
    path = Path(__file__).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    require(type(expected) is str and re.fullmatch(r'[a-f0-9]{64}', expected) and digest == expected)
    return {'modulePath': str(path), 'moduleSha256': digest}


def preflight(files, v):
    """Fail closed on missing/incorrect durable-image artifacts, before any apply."""
    pvc = json.loads(files['post-images-pvc.json'])
    require(pvc['apiVersion'] == 'v1' and pvc['kind'] == 'PersistentVolumeClaim')
    require(pvc['metadata'] == {'name': 'hosting-post-images', 'namespace': 'm2-hosting'})
    require(pvc['spec']['storageClassName'] == v['postImagesStorageClass'])
    require(pvc['spec']['accessModes'] == ['ReadWriteOnce'])
    candidate = json.loads(files['api-candidate.json'])
    spec = candidate['spec']['template']['spec']
    api, = [c for c in spec['containers'] if c['name'] == 'api']
    volume, = [x for x in spec['volumes'] if x['name'] == 'post-images']
    require(volume == {'name': 'post-images', 'persistentVolumeClaim': {'claimName': 'hosting-post-images'}})
    mount, = [m for m in api['volumeMounts'] if m['name'] == 'post-images']
    require(mount in ({'name': 'post-images', 'mountPath': '/var/lib/posts/images'},
                      {'name': 'post-images', 'mountPath': '/var/lib/posts/images', 'readOnly': False}))
    root, = [e for e in api['env'] if e['name'] == 'POST_IMAGE_ROOT']
    require(root == {'name': 'POST_IMAGE_ROOT', 'value': '/var/lib/posts/images/data'})
    patch = json.loads(files['api-patch.json'])
    require(patch[2] == {'op': 'replace', 'path': '/spec/template/spec', 'value': spec})


def render(v, baseline, templates, configmaps=()):
    checked(v)
    candidate, patch = deployment(v, baseline, configmaps)
    files = {'resources.json': resources(v), 'api-candidate.json': candidate, 'api-patch.json': patch,
             'post-images-pvc.json': {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
                 'metadata': {'name': 'hosting-post-images', 'namespace': 'm2-hosting'},
                 'spec': {'storageClassName': v['postImagesStorageClass'],
                          'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '10Gi'}}}}}
    result = {key: json.dumps(value, indent=2)+'\n' for key, value in files.items()}
    for role, value in inputs(v).items():
        result[role+'-inputs.json'] = json.dumps(value, indent=2)+'\n'
    for name in ('worker.py', 'storage-root.sh'):
        result[name] = (templates/name).read_text()
    for role in ('controller', 'restore'):
        result[role+'.sh'] = '#!/bin/sh\nset -eu\nexec '+ ' '.join(shell_quote(arg) for arg in docker(v, role))+'\n'
        result['stop-'+role+'.sh'] = (
            '#!/bin/sh\nset -eu\nfile=/run/issue16-'+role+'/container-id\n'
            'test -f "$file" || exit 0\ncid=$(cat "$file")\n'
            'test "${#cid}" = 64\ncase "$cid" in *[!0-9a-f]*) exit 1;; esac\n'
            '/usr/bin/docker rm -f "$cid" >/dev/null 2>&1 || true\n')
        dependencies = 'docker.service issue16-storage-root.service'
        if role != 'restore':
            dependencies += ' issue16-restore.service'
        # No network-online/k3s dependency for restore: it must finish before k3s starts.
        after = dependencies + (' network-online.target k3s.service' if role != 'restore' else ' local-fs.target')
        result['issue16-'+role+'.service'] = (
            '[Unit]\nDescription=Issue18 runner '+role+' finite batch\nRequires='+dependencies+'\nAfter='+after+'\n'
            + ('Before=k3s.service\n' if role == 'restore' else '')
            + '# Operator installs an existing alert hook before enabling timers:\n# OnFailure=operator-alert@%n.service\n'
            + '[Service]\nType=oneshot\nUser=root\nUMask=0077\nTimeoutStartSec=1800\n'
            + 'RuntimeDirectory=issue16-'+role+'\nRuntimeDirectoryMode=0700\n'
            + 'ExecStart='+BIN+'/'+role+'.sh\n'
            + 'ExecStopPost='+BIN+'/stop-'+role+'.sh\n'
            + ('RemainAfterExit=yes\n' if role == 'restore' else '')
            + '[Install]\nWantedBy=multi-user.target\n')
        if role != 'restore':
            result['issue16-'+role+'.timer'] = ('[Unit]\nDescription=Issue18 '+role+' bounded batches\n'
                '[Timer]\nOnBootSec=60s\nOnUnitInactiveSec=15s\nAccuracySec=1s\nUnit=issue16-'+role+'.service\n'
                '[Install]\nWantedBy=timers.target\n')
    result['issue16-storage-root.service'] = (
        '[Unit]\nDescription=Dedicated shared runner storage root\nDefaultDependencies=no\n'
        'After=local-fs.target\nBefore=docker.service k3s.service\nRequiresMountsFor=/var/lib/anxious-lookout\n'
        '[Service]\nType=oneshot\nUser=root\nUMask=0077\nRemainAfterExit=yes\nExecStart='+BIN+'/storage-root.sh\n'
        '[Install]\nWantedBy=multi-user.target\n')
    result['k3s-issue16.conf'] = '[Unit]\nRequires=issue16-restore.service\nAfter=issue16-restore.service\n'
    preflight(result, v)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--values', type=Path, required=True)
    p.add_argument('--deployment', type=Path, required=True)
    p.add_argument('--templates', type=Path, required=True)
    p.add_argument('--configmaps', type=Path, help='ConfigMap or List snapshot for API envFrom')
    p.add_argument('--output', type=Path)
    p.add_argument('--check', action='store_true')
    p.add_argument('--expected-module-sha256', required=True,
                   help='SHA256 of runner_rollout.py from the reviewed source SHA')
    args = p.parse_args()
    try:
        require(args.check or args.output is not None)
        source = provenance(args.expected_module_sha256)
        print(json.dumps(source, sort_keys=True), flush=True)
        snapshot = read(args.configmaps) if args.configmaps else {'kind': 'List', 'items': []}
        maps = snapshot['items'] if snapshot['kind'] == 'List' else [snapshot]
        values = read(args.values)
        files = render(values, read(args.deployment), args.templates, maps)
        files['renderer-provenance.json'] = json.dumps(source, indent=2)+'\n'
        if args.output is not None:
            require(args.output.absolute() == args.output.resolve() and not args.output.exists())
            args.output.mkdir(mode=0o700)
            for name, value in files.items():
                target = args.output/name
                with target.open('x') as stream:
                    stream.write(value)
                target.chmod(0o700 if name.endswith('.sh') else 0o600)
            preflight({name: (args.output/name).read_text() for name in files}, values)
    except Exception:
        p.exit(1, 'Rollout render refused; values and credentials are not printed\n')
    print('PASS offline rollout validation; no deployment or service changes')


if __name__ == '__main__':
    main()
