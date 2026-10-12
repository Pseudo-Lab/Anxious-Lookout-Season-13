"""Network-none Docker controller. Host broker owns transport and private files."""
import argparse
import base64
import copy
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

import access_numeric_prepare as prep
import access_numeric_timing as timing
import access_node_proxy_prepare as node

NS = 'codex-trial'
JOBS = '/apis/batch/v1/namespaces/codex-trial/jobs'
CNPS = '/apis/cilium.io/v2/namespaces/codex-trial/ciliumnetworkpolicies'
PODS = '/api/v1/namespaces/codex-trial/pods'
SERVICES = '/api/v1/namespaces/codex-trial/services'
FLAGS = ('EnableBPFTProxy', 'EnableIPv4', 'EnableIPv6', 'InstallIptRules', 'EnableEndpointRoutes')


def name(value):
    if type(value) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', value): raise ValueError()
    return value


def agent_version(text):
    """The selected running agent reports separate Client and Daemon lines."""
    if type(text) is not str: raise ValueError()
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 2: raise ValueError()
    versions = {}
    for line in lines:
        match = re.fullmatch(r'(Client|Daemon):[ \t]+([^ \t\r\n]+)(?:[ \t]+([^\r\n]+))?', line)
        if not match or match[1] in versions: raise ValueError()
        if match[3] and re.search(r'\b(?:Client|Daemon):', match[3]): raise ValueError()
        versions[match[1]] = match[2]
    if versions != {'Client': '1.20.2', 'Daemon': '1.20.2'}: raise ValueError()
    return versions['Daemon']


def namespace_provenance():
    """Own observer metadata only; these fields never decide guard acceptance."""
    record = {'pid': os.getpid(), 'reader': 'python-in-process', 'namespaces': {}}
    for kind in ('time', 'time_for_children'):
        path = Path('/proc/self/ns')/kind
        try:
            link = str(path.readlink()); info = path.stat()
            record['namespaces'][kind] = {'available': True, 'link': link, 'device': info.st_dev, 'inode': info.st_ino}
        except OSError as error:
            record['namespaces'][kind] = {'available': False, 'errorType': type(error).__name__}
    try:
        record['timensOffsets'] = {'available': True, 'base64': base64.b64encode(Path('/proc/self/timens_offsets').read_bytes()).decode()}
    except OSError as error:
        record['timensOffsets'] = {'available': False, 'errorType': type(error).__name__}
    return record


def provenance_collected(record):
    if not isinstance(record, dict): return False
    try:
        return (all(record['namespaces'][k]['available'] is True for k in ('time', 'time_for_children'))
                and record['timensOffsets']['available'] is True)
    except (KeyError, TypeError): return False


def resource(obj):
    groups = {'Deployment': ('/apis/apps/v1', 'deployments'), 'ReplicaSet': ('/apis/apps/v1', 'replicasets'),
              'ConfigMap': ('/api/v1', 'configmaps'), 'PersistentVolumeClaim': ('/api/v1', 'persistentvolumeclaims'),
              'PersistentVolume': ('/api/v1', 'persistentvolumes'), 'Pod': ('/api/v1', 'pods')}
    group, plural = groups[obj['kind']]; m = obj['metadata']
    return group+('/namespaces/'+name(m['namespace']) if 'namespace' in m else '')+'/'+plural+'/'+name(m['name'])


def baseline_pin(pod, cep, agent):
    """Validate exact attachment before requesting its local numeric endpoint."""
    m, c = pod['metadata'], cep['metadata']; n = name(m['name']); ns = m['namespace']
    if ns not in ('m2-hosting', 'kube-system'): raise ValueError()
    node.ident(pod, 'Pod', ns); node.ident(cep, 'CiliumEndpoint', ns)
    owners = [o for o in c['ownerReferences'] if o.get('kind') == 'Pod']
    if (c['name'] != n or pod['spec']['nodeName'] != agent['spec']['nodeName'] or len(owners) != 1
            or owners[0].get('name') != n or owners[0].get('uid') != m['uid']): raise ValueError()
    cp = cep['status']
    ident = cp['id']; attachment = cp['external-identifiers']['cni-attachment-id']
    if type(ident) is not int or not 1 <= ident <= 65535 or type(attachment) is not str or not attachment or cp['state'] != 'ready': raise ValueError()
    if cp['external-identifiers'].get('k8s-pod-name', ns+'/'+n) != ns+'/'+n: raise ValueError()
    if not any(pod['status']['podIP'] in (v.get('ipv4'), v.get('ipv6')) for v in cp['networking']['addressing']): raise ValueError()
    return {'id': ident, 'attachment': attachment}


def baseline_endpoint(pod, cep, agent, response):
    pin = baseline_pin(pod, cep, agent)
    m, c = pod['metadata'], cep['metadata']; ns, n = m['namespace'], m['name']
    value = prep.native.endpoint_one(response); status = value['status']
    if type(value['id']) is not int or value['id'] != pin['id'] or status['state'] != 'ready' or status['external-identifiers']['cni-attachment-id'] != pin['attachment']: raise ValueError()
    for source in (status,):
        if source['external-identifiers'].get('k8s-pod-name', ns+'/'+n) != ns+'/'+n: raise ValueError()
        if not any(pod['status']['podIP'] in (v.get('ipv4'), v.get('ipv6')) for v in source['networking']['addressing']): raise ValueError()
    return {'podUid': m['uid'], 'cepUid': c['uid'], 'attachment': pin['attachment'],
            'agentUid': agent['metadata']['uid'], 'state': status['state'], 'policy': status['policy']}


class Wire:
    def __init__(self, evidence): self.evidence = Path(evidence); self.serial = 0

    def call(self, kind, **values):
        self.serial += 1
        encode = lambda s: base64.b64encode(s.encode()).decode() or '-'
        fields = [kind, str(self.serial), str(values.get('deadlineCs') or 0)]
        lines = []
        if kind == 'batch':
            fields.append(str(len(values['operations'])))
            for op in values['operations']:
                if any(type(a) is not str or '\0' in a for a in op['argv']): raise ValueError()
                lines.append(encode('\0'.join(op['argv'])+'\0')+'\t'+encode(op.get('stdin', '')))
        elif kind == 'save': fields.extend((values['filename'], encode(values['value'])))
        elif kind == 'step': fields.extend((values['mode'], values['pod'], values['filename']))
        elif kind == 'images': fields.extend((values['runner'], values['api']))
        elif kind == 'done': fields.append(encode(json.dumps(values['outcome'])))
        elif kind not in ('arm', 'deadline', 'clear', 'cleanup', 'host'): raise ValueError()
        print('\t'.join(fields), flush=True)
        for line in lines: print(line, flush=True)
        answer = json.loads(sys.stdin.readline())
        if answer.get('id') != self.serial or answer.get('brokerOk') is not True: raise ValueError()
        return answer

    def batch(self, operations, deadline):
        result = self.call('batch', operations=operations, deadlineCs=deadline//10_000_000)
        if len(result['results']) != len(operations): raise ValueError()
        return [(r['exit'], (self.evidence/r['stdout']).read_text()) for r in result['results']]

    def save(self, filename, value, text=False):
        self.call('save', filename=filename, value=value if text else json.dumps(value), deadlineCs=None)

    def step(self, mode, pod, filename, deadline):
        r = self.call('step', mode=mode, pod=pod, filename=filename, deadlineCs=deadline//10_000_000)
        return r['exit'], (self.evidence/(mode+'.stdout')).read_text()

    def arm(self): self.call('arm')


class Trial:
    def __init__(self, seed, io, now=None):
        self.seed = copy.deepcopy(seed); self.io = io
        self.now = now or (lambda: time.clock_gettime_ns(time.CLOCK_BOOTTIME))
        self.limit = self.now()+120_000_000_000; self.arm_ns = None; self.group_end = None
        self.owned = {}; self.uncertain = []; self.b = None; self.holder_receipt = None
        self.nonce = secrets.token_hex(16); self.cleanup_mode = False
        self.job_dispatched_ns = None
        self.recovery_mode = False; self.clock_only = False
        self.last_clock_observation = None

    def boundary(self, seconds=13):
        end = self.arm_ns+(120 if self.cleanup_mode else 75)*1_000_000_000 if self.arm_ns else self.limit
        if self.now()+seconds*1_000_000_000 > end: raise ValueError()
        return min(end, self.group_end or end, self.now()+seconds*1_000_000_000)

    def read(self, operations, seconds=13, tolerate=False):
        deadline = self.boundary(seconds)
        if deadline <= self.now()+1_000_000_000: raise ValueError()
        result = self.io.batch(operations, deadline)
        if self.now() >= deadline: raise ValueError()
        if not tolerate and any(code != 0 for code, _ in result): raise ValueError()
        return result

    def get(self, paths):
        values = self.read([{'argv': ['get', '--raw', path]} for path in paths])
        return [json.loads(text) for _, text in values]

    def agent_calls(self, agent, tails):
        return self.read([{'argv': ['-n', 'kube-system', 'exec', name(agent['metadata']['name']),
                                   '-c', 'cilium-agent', '--', 'cilium-dbg', *tail]} for tail in tails])

    def native(self, agent, ceps):
        ids = [c['status']['id'] for c in ceps]
        if any(type(i) is not int or not 1 <= i <= 65535 for i in ids): raise ValueError()
        return [json.loads(t) for _, t in self.agent_calls(agent, [['endpoint', 'get', str(i), '-o', 'json'] for i in ids])]

    def chains(self, b):
        for prefix, namespace, svc in (('callback', 'm2-hosting', 'api'), ('dns', 'kube-system', 'kube-dns')):
            service, slices = self.get(['/api/v1/namespaces/'+namespace+'/services/'+svc,
                '/apis/discovery.k8s.io/v1/namespaces/'+namespace+'/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3D'+svc])
            entries = [e for s in node.items(slices) for e in s['endpoints']]
            if len(entries) != 1: raise ValueError()
            target = entries[0]['targetRef']
            if target['namespace'] != namespace or target['kind'] != 'Pod': raise ValueError()
            pod = self.get(['/api/v1/namespaces/'+namespace+'/pods/'+name(target['name'])])[0]
            if pod['metadata']['uid'] != target['uid']: raise ValueError()
            owner = pod['metadata']['ownerReferences']
            if len(owner) != 1 or owner[0]['kind'] != 'ReplicaSet': raise ValueError()
            rs = self.get(['/apis/apps/v1/namespaces/'+namespace+'/replicasets/'+name(owner[0]['name'])])[0]
            parent = rs['metadata']['ownerReferences']
            if len(parent) != 1 or parent[0]['kind'] != 'Deployment': raise ValueError()
            deployment = self.get(['/apis/apps/v1/namespaces/'+namespace+'/deployments/'+name(parent[0]['name'])])[0]
            b[prefix+'Service'], b[prefix+'Slices'] = service, slices
            b['apiPod' if prefix == 'callback' else 'dnsPod'] = pod
            b['apiReplicaSet' if prefix == 'callback' else 'dnsReplicaSet'] = rs
            if prefix == 'callback': b['snapshot']['api'] = deployment
            else: b['dnsDeployment'] = deployment
        return b

    def refresh(self, allow_owned=False):
        b = {k: copy.deepcopy(self.seed[k]) for k in ('expectedNode', 'inputs', 'images', 'baseline')}
        paths = ['/api/v1/nodes', '/apis/apps/v1/namespaces/kube-system/daemonsets/cilium',
            '/api/v1/namespaces/kube-system/pods?labelSelector=k8s-app%3Dcilium',
            '/apis/apiextensions.k8s.io/v1/customresourcedefinitions/ciliumnodeconfigs.cilium.io',
            '/apis/cilium.io/v2/namespaces/kube-system/ciliumnodeconfigs',
            '/api/v1/namespaces/kube-system/configmaps/cilium-config',
            CNPS+'/access-web-boundary-v1', '/apis/cilium.io/v2/namespaces/m2-hosting/ciliumnetworkpolicies/hosting-access-web-link-v1',
            '/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/anxious-lookout-node-protection',
            SERVICES, PODS, JOBS, CNPS]
        values = self.get(paths)
        for k, v in zip(('nodes', 'daemonset', 'agents', 'crd', 'nodeConfigs', 'configMap'), values[:6]): b[k] = v
        b['policies'] = values[6:9]; b['services'], b['pods'] = values[9:11]
        for kind, listing in zip(('Job', 'CiliumNetworkPolicy'), values[11:]):
            matches = [x for x in node.items(listing) if x['metadata']['name'] == prep.NAME]
            if matches:
                if not allow_owned or len(matches) != 1 or kind not in self.owned: raise ValueError()
                prep.deletion(matches[0], self.owned[kind])  # Validate only; no delete.
        b['snapshot'] = {}
        original = self.seed['snapshot']
        b['snapshot'].update(zip(('api', 'runner', 'config'), self.get([resource(original[k]) for k in ('api', 'runner', 'config')])))
        for plural, kind in (('pvcs', 'PersistentVolumeClaim'), ('pvs', 'PersistentVolume')):
            objects = original[plural]['items']
            fresh = self.get([resource(x | {'kind': kind}) for x in objects])
            b['snapshot'][plural] = {'items': fresh}
        runners = [p for p in node.items(b['pods']) if p['metadata'].get('labels', {}).get('app') == 'access-web-runner']
        if runners and allow_owned:
            if len(runners) != 1 or 'Job' not in self.owned: raise ValueError()
            job = next(x for x in node.items(values[11]) if x['metadata']['name'] == prep.NAME)
            prep.deletion(job, self.owned['Job']); prep.selected(job, runners[0], self.nonce)
            runners = []
        b['snapshot']['runnerPodCount'] = len(runners)
        agent_ref = node.select_initial(b)
        agent = next(p for p in node.items(b['agents']) if p['metadata']['uid'] == agent_ref['uid'])
        if agent_ref['uid'] != self.seed['receipt']['initialAgent']['uid'] or node.agent_signature(agent)['imageID'] != self.seed['receipt']['agentImageId']: raise ValueError()
        selected_node = next(n for n in node.items(b['nodes']) if n['metadata']['uid'] == b['expectedNode']['uid'])
        if selected_node['status']['nodeInfo']['bootID'] != self.seed['receipt']['binding']['bootId']: raise ValueError()
        self.chains(b)
        if node.application_witness(b['snapshot']) != self.seed['receipt']['applicationWitness']: raise ValueError()
        expected_api = [c for c in b['snapshot']['api']['spec']['template']['spec']['containers'] if c['name'] == 'api']
        actual_api = [c for c in b['apiPod']['spec']['containers'] if c['name'] == 'api']
        signature = [c for c in b['apiPod']['status']['containerStatuses'] if c['name'] == 'api']
        if len(expected_api) != 1 or len(actual_api) != 1 or len(signature) != 1: raise ValueError()
        image = expected_api[0]['image']
        if actual_api[0]['image'] != image or '@sha256:' not in image or signature[0]['imageID'].split('@')[-1] != image.split('@')[-1]: raise ValueError()
        cached = [v for image in selected_node['status'].get('images', []) for v in image['names']]
        if image not in cached or prep.direct.RUNNER_IMAGE not in cached: raise ValueError()
        for prefix, key, rs, deployment, port in (('callback', 'apiPod', 'apiReplicaSet', b['snapshot']['api'], 8080),
                                                ('dns', 'dnsPod', 'dnsReplicaSet', b['dnsDeployment'], 53)):
            prep.service_target(b[prefix+'Service'], b[prefix+'Slices'], b[key], b[rs], deployment, port)
            if b[key]['spec']['nodeName'] != b['expectedNode']['name']: raise ValueError()
            alias = 'api' if key == 'apiPod' else 'dns'
            if b[key]['metadata']['uid'] != self.seed['receipt']['endpointWitnesses'][alias]['podUid']: raise ValueError()
        ceps = self.get(['/apis/cilium.io/v2/namespaces/'+b[k]['metadata']['namespace']+'/ciliumendpoints/'+name(b[k]['metadata']['name']) for k in ('apiPod', 'dnsPod')])
        for alias, key, cep in zip(('api', 'dns'), ('apiPod', 'dnsPod'), ceps):
            pin = baseline_pin(b[key], cep, agent)
            expected = self.seed['receipt']['endpointWitnesses'][alias]
            if cep['metadata']['uid'] != expected['cepUid'] or pin['attachment'] != expected['attachment']: raise ValueError()
        native = self.native(agent, ceps)
        b['endpoints'] = {alias: baseline_endpoint(b[k], c, agent, ep) for alias, k, c, ep in zip(('api', 'dns'), ('apiPod', 'dnsPod'), ceps, native)}
        agent_raw = self.agent_calls(agent, [['version'], ['status', '-o', 'json']]+[['config', 'get', flag] for flag in FLAGS])
        b['version'] = agent_version(agent_raw[0][1]); b['status'] = json.loads(agent_raw[1][1])
        b['settings'] = dict(zip(node.KEYS, [t.strip() for _, t in agent_raw[2:]]))
        server = self.read([{'argv': ['config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}']}])[0][1].strip()
        b['independentApiServer'] = server
        # A fresh explicit independent GET, not a inherited boolean.
        if server not in ('https://127.0.0.1:6443', 'https://[::1]:6443'): raise ValueError()
        independent = json.loads(self.read([{'argv': ['--server='+server, 'get', '--raw', '/api/v1/nodes/'+name(b['expectedNode']['name'])]}])[0][1])
        if independent['metadata']['uid'] != b['expectedNode']['uid'] or independent['status']['nodeInfo'] != next(n for n in node.items(b['nodes']) if n['metadata']['uid'] == independent['metadata']['uid'])['status']['nodeInfo']: raise ValueError()
        b['independentApiGetPassed'] = True
        b['receipt'] = node.preflight(b)
        pinned = self.seed['receipt']
        for key in ('binding', 'daemonsetUid', 'daemonsetSpecHash', 'agentImageId', 'settings', 'configMapHash',
                    'nodeConfigsHash', 'policiesHash', 'baselineHash', 'endpointWitnesses', 'applicationWitness'):
            if b['receipt'][key] != pinned[key]: raise ValueError()
        if b['receipt']['initialAgent']['uid'] != pinned['initialAgent']['uid']: raise ValueError()
        node.health(b['receipt'], b, 'false', [])
        if b['receipt']['binding']['bootId'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip(): raise ValueError()
        b['nonce'] = self.nonce
        prep.collector_binding(b, self.nonce)
        return b

    def save(self, filename, value, text=False):
        self.io.save(filename, value, text)
        end = self.arm_ns+(120 if self.cleanup_mode else 75)*1_000_000_000 if self.arm_ns else self.limit
        if self.now() >= (self.group_end or end): raise ValueError()

    def host_clock(self):
        phase = 'clock-only' if self.clock_only else ('recovery' if self.recovery_mode else ('cleanup' if self.cleanup_mode else 'before-arm'))
        controller = {'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                      'timeNamespace': str(Path('/proc/self/ns/time').readlink()), 'requestNs': self.now()}
        if self.clock_only:
            controller['namespaceReader'] = 'python-in-process:/proc/self/ns/time'
            controller['namespaceProvenance'] = namespace_provenance()
        try:
            captured = self.io.call('host')
        except Exception as error:
            controller['receivedNs'] = self.now()
            observation = {'phase': phase, 'controller': controller, 'host': None,
                           'accepted': False, 'reasons': ['broker_failure_or_invalid_response'], 'errorType': type(error).__name__}
            observation['namespaceProvenanceStatus'] = 'unknown'; self.last_clock_observation = observation
            print(json.dumps({'clockObservation': observation}), file=sys.stderr, flush=True)
            self.io.save('host-clock-observation-'+phase+'.json', observation)
            raise
        controller['receivedNs'] = self.now()
        valid_clock = type(captured.get('nowCs')) is int
        skew = controller['receivedNs']-captured['nowCs']*10_000_000 if valid_clock else None
        checks = {'bootMatches': captured.get('bootId') == controller['bootId'],
                  'timeNamespaceMatches': captured.get('timeNamespace') == controller['timeNamespace'],
                  'clockTypeValid': valid_clock, 'clockSkewWithin250ms': valid_clock and abs(skew) <= 250_000_000}
        observation = {'phase': phase, 'host': captured, 'controller': controller, 'skewNs': skew,
                       'checks': checks, 'accepted': all(checks.values()), 'reasons': [k for k,v in checks.items() if not v]}
        if self.clock_only:
            observation['namespaceProvenanceStatus'] = ('collected' if provenance_collected(captured.get('namespaceProvenance'))
                and provenance_collected(controller.get('namespaceProvenance')) else 'unknown')
        self.last_clock_observation = observation
        # Preserve the receipt-time sample BEFORE rejection; journalling delay
        # must not manufacture skew in the unchanged250ms predicate.
        print(json.dumps({'clockObservation': observation}), file=sys.stderr, flush=True)
        self.io.save('host-clock-observation-'+phase+'.json', observation)
        if not observation['accepted']: raise ValueError()
        return captured

    def clock_capture(self):
        self.clock_only = True
        result = {'clockOnly': True, 'armed': False, 'kubernetesCalls': 0, 'clockGuardAccepted': False}
        try:
            self.host_clock(); result['clockGuardAccepted'] = True
        except Exception:
            pass
        result['namespaceProvenanceStatus'] = (self.last_clock_observation or {}).get('namespaceProvenanceStatus', 'unknown')
        self.io.save('clock-result.json', result)
        self.io.call('done', outcome=result)
        return result

    def create(self, kind, manifest):
        self.boundary()
        self.save('uncertain-'+kind+'.json', manifest)
        deadline = self.boundary()
        self.uncertain.append(kind)
        if kind == 'Job': self.job_dispatched_ns = self.now()
        values = self.io.batch([{'argv': ['create', '-f', '-', '-o', 'json'], 'stdin': json.dumps(manifest)}], deadline)
        if len(values) != 1 or values[0][0] != 0: raise ValueError()
        obj = json.loads(values[0][1]); node.ident(obj, kind, NS)
        if obj['metadata']['name'] != prep.NAME: raise ValueError()
        owned = {'uid': obj['metadata']['uid'], 'specHash': node.digest(obj['spec'])}
        self.owned[kind] = owned
        self.uncertain.remove(kind)
        # Late complete acknowledgements still establish cleanup ownership;
        # they cannot admit another business stage. Persist before refusing.
        self.io.save('owned-'+kind+'.json', owned)
        if self.now() >= deadline: raise ValueError()
        return obj

    def stage(self, stage, fn):
        deadline = timing.admit(stage, self.holder_receipt['startNs'], self.now())
        self.group_end = deadline
        self.io.call('deadline', deadlineCs=deadline//10_000_000)
        self.save('stage-'+stage+'.json', {'stage': stage, 'stageDeadlineNs': deadline})
        try:
            value = fn()
            timing.completed(self.holder_receipt['startNs'], deadline, self.now())
            self.io.call('clear')
            return value
        finally: self.group_end = None

    def scope_reads(self, b, include_policy=False):
        paths = [JOBS+'/'+prep.NAME, PODS, SERVICES, '/apis/cilium.io/v2/namespaces/codex-trial/ciliumendpoints/'+name(b['pod']['metadata']['name']),
                 '/api/v1/namespaces/kube-system/pods/'+name(b['agent']['metadata']['name']),
                 CNPS+'/access-web-boundary-v1', '/apis/cilium.io/v2/namespaces/m2-hosting/ciliumnetworkpolicies/hosting-access-web-link-v1',
                 '/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/anxious-lookout-node-protection']
        if include_policy: paths.append(CNPS+'/'+prep.NAME)
        values = self.get(paths)
        b['job'], b['pods'], b['services'], b['cep'], b['agent'] = values[:5]
        matches = [p for p in node.items(b['pods']) if p['metadata']['uid'] == b['pod']['metadata']['uid']]
        if len(matches) != 1: raise ValueError()
        b['pod'] = matches[0]; b['policies'] = values[5:8]; b['production'], _, b['globalPolicy'] = b['policies']
        if include_policy: b['temporary'] = values[8]
        # CEP is fully read and bound before this dependent numeric GET.
        prep.selected(b['job'], b['pod'], self.nonce); prep.singleton(b['pods'], b['pod'], self.nonce)
        pin = prep.endpoint.pin(b['pod'], b['cep'], b['agent'])
        if (pin['node'] != b['receipt']['node']['name'] or pin['agentUid'] != b['receipt']['initialAgent']['uid']
                or node.agent_signature(b['agent']) != b['agentSignature']): raise ValueError()
        b['endpoint'] = self.native(b['agent'], [b['cep']])[0]
        return b

    def original(self):
        # Acquisition is bounded even before logs reveal kernel PID1 start.
        self.group_end = self.job_dispatched_ns+3_000_000_000
        if self.now() >= self.group_end: raise ValueError()
        self.io.call('deadline', deadlineCs=self.group_end//10_000_000)
        b = copy.deepcopy(self.b)
        job, pods, agents = self.get([JOBS+'/'+prep.NAME, PODS, '/api/v1/namespaces/kube-system/pods?labelSelector=k8s-app%3Dcilium'])
        matches = [p for p in node.items(pods) if any(o.get('uid') == self.owned['Job']['uid'] for o in p['metadata'].get('ownerReferences', []))]
        if len(matches) != 1: raise ValueError()
        b.update(job=job, pods=pods, pod=matches[0], agent=next(p for p in node.items(agents) if p['metadata']['uid'] == b['receipt']['initialAgent']['uid']))
        prep.selected(job, b['pod'], self.nonce)
        b['agentSignature'] = node.agent_signature(b['agent'])
        raw = self.read([{'argv': ['-n', NS, 'logs', name(b['pod']['metadata']['name']), '-c', 'diagnose', '--tail=1', '--limit-bytes=4096']}])[0][1]
        self.holder_receipt = json.loads(raw); self.save('holder.receipt.json', self.holder_receipt)
        if (self.holder_receipt['bootId'] != b['receipt']['binding']['bootId']
                or self.holder_receipt['timeNamespace'] != str(Path('/proc/self/ns/time').readlink())): raise ValueError()
        self.group_end = min(self.group_end, self.holder_receipt['startNs']+3_000_000_000)
        self.io.call('deadline', deadlineCs=self.group_end//10_000_000)
        if self.now() >= self.group_end: raise ValueError()
        # Kernel start is known only now; the preceding acquisition is part of
        # original and must also fit its absolute3s window after PID1 start.
        def finish():
            self.scope_reads(b); b['reference'] = copy.deepcopy(b['endpoint'])
            prep.original_scope(b, self.nonce)
            b['holderReceipt'] = self.holder_receipt
            b['collectorBaseline'] = copy.deepcopy(self.b)
            b['binding'] = prep.collector_binding(self.b, self.nonce)
            self.save('collector.py', prep.collector_program(b['binding']), True)
            return b
        deadline = self.group_end
        self.save('stage-original.json', {'stage': 'original', 'stageDeadlineNs': deadline})
        result = finish()
        timing.completed(self.holder_receipt['startNs'], deadline, self.now())
        self.io.call('clear')
        self.group_end = None
        return result

    def run(self):
        outcome = {'numericAttempt': 'refused', 'webOrNativeSuccess': False}
        try:
            self.b = self.refresh()
            job = prep.holder(self.b, self.nonce)
            self.io.call('images', runner=prep.direct.RUNNER_IMAGE, api=self.b['images']['api'])
            self.save('host-clock.json', self.host_clock())
            self.save('baseline.json', self.b); self.save('holder.manifest.json', job)
            self.arm_ns = self.now(); self.io.arm(); self.save('arm.json', {'armNs': self.arm_ns, 'nonce': self.nonce})
            self.create('Job', job)
            b = self.original()
            def collect():
                code, text = self.io.step('collector', b['binding']['apiPodName'], 'collector.py', self.boundary(6))
                if code != 0: raise ValueError()
                b['capture'] = json.loads(text)
            self.stage('collector', collect)
            def binding():
                # Full current baseline and API/DNS chains, not selected fields
                # from the old capture. A short group can validly refuse.
                fresh = self.refresh(allow_owned=True)
                b['bindingAfter'] = prep.collector_binding(fresh, self.nonce)
                if b['bindingAfter'] != b['binding']: raise ValueError()
            self.stage('binding', binding)
            def lease():
                if (b['capture']['bootId'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                        or b['capture']['timeNamespace'] != str(Path('/proc/self/ns/time').readlink())): raise ValueError()
                b['lease'] = prep.dns.lease(b['capture'], b['binding'], self.now())
                manifest = prep.policy(b['lease'], self.nonce, self.now())
                self.save('lease.json', b['lease']); return manifest
            policy = self.stage('lease', lease)
            temporary = self.stage('create', lambda: self.create('CiliumNetworkPolicy', policy))
            b['temporary'] = temporary; b['ownedPolicy'] = self.owned['CiliumNetworkPolicy']
            def realized():
                self.scope_reads(b, True)
                if prep.dns.lease(b['capture'], b['binding'], self.now(), 10) != b['lease']: raise ValueError()
                b['scope'] = prep.scoped(b, self.nonce, b['lease'], self.now())
            self.stage('realization', realized)
            def emit():
                if (prep.dns.lease(b['capture'], b['binding'], self.now(), 10) != b['lease']
                        or prep.scoped(b, self.nonce, b['lease'], self.now()) != b['scope']): raise ValueError()
                self.save('worker.py', prep.program(b, b['scope'], b['lease']), True)
            self.stage('emit', emit)
            dispatch = timing.admit('dispatch', self.holder_receipt['startNs'], self.now())
            self.group_end = dispatch
            self.io.call('deadline', deadlineCs=dispatch//10_000_000)
            self.save('dispatch.stage.json', {'stage': 'dispatch', 'stageDeadlineNs': dispatch})
            self.save('dispatch.deadlineCs', str(dispatch//10_000_000)+'\n', True)
            self.io.call('clear')
            self.group_end = None
            self.boundary(45)
            code, text = self.io.step('worker', b['pod']['metadata']['name'], 'worker.py', self.boundary(45))
            self.scope_reads(b, True)
            validation_end = self.boundary(6)
            self.io.call('deadline', deadlineCs=validation_end//10_000_000)
            dispatched = json.loads((self.io.evidence/'worker.dispatch.json').read_text())
            timing.dispatched(self.holder_receipt['startNs'], dispatch, dispatched)
            b['result'] = json.loads(text); b['transportExit'] = code
            checked = prep.checked_result(b)
            if self.now() >= validation_end: raise ValueError()
            self.io.call('clear')
            outcome = checked | {'numericAttempt': 'completed'}
        except Exception:
            pass
        finally:
            outcome['cleanup'] = self.cleanup()
            self.io.save('outcome.json', outcome)
            self.io.call('done', outcome=outcome)
        return outcome

    def cleanup(self):
        if self.arm_ns is None: return {'status': 'not_armed'}
        self.group_end = None; self.cleanup_mode = True
        self.io.call('cleanup')
        remaining = list(self.owned)
        try:
            self.host_clock()
            for kind, collection in (('Job', JOBS), ('CiliumNetworkPolicy', CNPS)):
                if kind not in self.owned: continue
                current = self.get([collection+'/'+prep.NAME])[0]
                body = prep.deletion(current, self.owned[kind])
                if (self.io.evidence/('delete-attempt-'+kind+'.json')).exists(): raise ValueError()
                self.save('delete-attempt-'+kind+'.json', body)
                self.read([{'argv': ['delete', '--raw', collection+'/'+prep.NAME, '-f', '-'], 'stdin': json.dumps(body)}])
                listing = self.get([collection])[0]
                if any(x['metadata']['name'] == prep.NAME for x in node.items(listing)): raise ValueError()
                if kind == 'Job':
                    pods, slices = self.get([PODS, '/apis/discovery.k8s.io/v1/namespaces/codex-trial/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Daccess-web-runner-v1'])
                    if any(p['metadata'].get('labels', {}).get('app') == 'access-web-runner' for p in node.items(pods)): raise ValueError()
                    if any(s['endpoints'] for s in node.items(slices)): raise ValueError()
                remaining.remove(kind)
            if self.uncertain: raise ValueError()
            fresh = self.refresh()
            if self.now() >= self.arm_ns+120_000_000_000: raise ValueError()
            node.health(self.b['receipt'], fresh, 'false', [])
            return {'status': 'verified', 'runnerPods': 0, 'fullFalseHealth': True}
        except Exception:
            return {'status': 'unknown_handoff', 'remainingOwned': {k: self.owned[k] for k in remaining},
                    'unacknowledgedCreates': self.uncertain, 'nonce': self.nonce}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seed', required=True); p.add_argument('--evidence', required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--cleanup-only', action='store_true'); mode.add_argument('--clock-only', action='store_true'); args = p.parse_args()
    io = Wire(args.evidence)
    try:
        trial = Trial(json.loads(Path(args.seed).read_text()), io)
        if args.clock_only:
            trial.clock_capture()
        elif args.cleanup_only:
            trial.recovery_mode = True
            arm = json.loads((io.evidence/'arm.json').read_text()); trial.arm_ns = arm['armNs']; trial.nonce = arm['nonce']
            trial.b = json.loads((io.evidence/'baseline.json').read_text())
            for kind in ('Job', 'CiliumNetworkPolicy'):
                owned = io.evidence/('owned-'+kind+'.json')
                if owned.exists(): trial.owned[kind] = json.loads(owned.read_text())
                elif (io.evidence/('uncertain-'+kind+'.json')).exists(): trial.uncertain.append(kind)
            result = trial.cleanup(); io.save('recovery.json', result); io.call('done', outcome=result)
        else: trial.run()
    except Exception:
        raise SystemExit(1)
