import base64
import copy
import json
import hashlib
from pathlib import Path
import signal
import ssl
import struct
import subprocess
from types import SimpleNamespace

import pytest

import access_numeric_lease as dns
import access_numeric_prepare as prep
import access_node_proxy_prepare as node
from test_access_node_proxy import bundle, meta, listing
from test_access_runner_dns_prepare import runner_snapshot
from test_access_web_prepare import package
from test_access_dns_contrast import witness, NONCE
from test_access_endpoint_pin import metadata


def wire_name(value):
    return b''.join(bytes([len(s)])+s.encode() for s in value.split('.'))+b'\0'


def reply(records=None, flags=0x8180):
    records = records or [('chatgpt.com', 5, 60, 'cdn.example'), ('cdn.example', 1, 30, '1.1.1.1')]
    result = b'\x12\x34'+struct.pack('!HHHHH', flags, 1, len(records), 0, 0)+wire_name('chatgpt.com')+struct.pack('!HH', 1, 1)
    for owner, kind, ttl, value in records:
        data = wire_name(value) if kind == 5 else bytes(map(int, value.split('.')))
        result += wire_name(owner)+struct.pack('!HHIH', kind, 1, ttl, len(data))+data
    return result


def capture(binding=None, packet=None):
    return {'binding': binding or {'nonce': NONCE, 'resolverIp': '10.43.0.10', 'bootId':'boot'}, 'peer': '10.43.0.10',
            'beginNs': 1_000_000_000, 'endNs': 2_000_000_000, 'queryId': '1234',
            'response': base64.b64encode(packet or reply()).decode(),
            'realtimeNs': 100_000_000_000, 'timeNamespace': 'time:[1]', 'bootId': 'boot'}


def test_exact_cname_chain_min_ttl_and_deterministic_single_address():
    packet = reply([('chatgpt.com', 5, 20, 'cdn.example'), ('cdn.example', 1, 60, '8.8.8.8'), ('cdn.example', 1, 30, '1.1.1.1')])
    assert dns.answer(packet, b'\x12\x34') == ('1.1.1.1', 20, 1)
    c = capture(packet=packet)
    result = dns.lease(c, c['binding'], 3_000_000_000)
    assert result['expiryNs'] == 21_000_000_000


@pytest.mark.parametrize('change', ['truncated', 'rcode', 'wrong_id', 'wrong_question', 'ttl0', 'private',
    'multicast', 'unrelated', 'cycle', 'missing_a', 'ambiguous_cname', 'trailing', 'short', 'bad_pointer'])
def test_malformed_partial_or_nonpublic_dns_refused(change):
    packet = reply()
    if change == 'truncated': packet = reply(flags=0x8380)
    elif change == 'rcode': packet = reply(flags=0x8183)
    elif change == 'wrong_id': packet = b'\xab\xcd'+packet[2:]
    elif change == 'wrong_question': packet = packet.replace(b'chatgpt', b'chatgpx', 1)
    elif change == 'ttl0': packet = reply([('chatgpt.com', 1, 0, '1.1.1.1')])
    elif change == 'private': packet = reply([('chatgpt.com', 1, 30, '10.0.0.1')])
    elif change == 'multicast': packet = reply([('chatgpt.com', 1, 30, '224.0.0.1')])
    elif change == 'unrelated': packet = reply([('chatgpt.com', 1, 30, '1.1.1.1'), ('other.example', 1, 30, '8.8.8.8')])
    elif change == 'cycle': packet = reply([('chatgpt.com', 5, 30, 'cdn.example'), ('cdn.example', 5, 30, 'chatgpt.com')])
    elif change == 'missing_a': packet = reply([('chatgpt.com', 5, 30, 'cdn.example')])
    elif change == 'ambiguous_cname': packet = reply([('chatgpt.com', 5, 30, 'a.example'), ('chatgpt.com', 5, 30, 'b.example')])
    elif change == 'trailing': packet += b'x'
    elif change == 'short': packet = packet[:-1]
    else: packet = packet[:12]+b'\xc0\x0c'+packet[14:]
    with pytest.raises(ValueError): dns.answer(packet, b'\x12\x34')


@pytest.mark.parametrize('change', ['stale', 'future', 'peer', 'binding', 'slow', 'bool_clock'])
def test_collection_provenance_and_lease_freshness(change):
    c = capture(); expected = copy.deepcopy(c['binding']); now = 3_000_000_000
    if change == 'stale': now = 20_000_000_000
    elif change == 'future': now = 1_000_000_000
    elif change == 'peer': c['peer'] = '8.8.8.8'
    elif change == 'binding': c['binding']['nonce'] = 'b'*32
    elif change == 'slow': c['beginNs'] = 1; c['endNs'] = 4_000_000_000; now = 5_000_000_000
    else: c['beginNs'] = True
    with pytest.raises(ValueError): dns.lease(c, expected, now)


@pytest.fixture
def prepared(bundle):
    b = bundle
    b['status'] = {'cilium': {'state': 'Ok'}, 'kubernetes': {'state': 'Ok'}}
    b['snapshot']['api']['spec']['template'].setdefault('metadata', {})['labels'] = {'app': 'api'}
    b['receipt'] = node.preflight(b)
    b['nonce'] = NONCE
    svc = meta('Service', 'access-web-runner-v1', 'codex-trial') | {'spec': {'selector': {'app': 'access-web-runner'}}}
    b['services'] = listing([svc]); b['pods'] = listing([])
    for prefix, namespace, name, label, ip, port in (
        ('callback', 'm2-hosting', 'api', {'app': 'api'}, '10.43.0.7', 8080),
        ('dns', 'kube-system', 'kube-dns', {'k8s-app': 'kube-dns'}, '10.43.0.10', 53)):
        deployment = b['snapshot']['api'] if prefix == 'callback' else meta('Deployment', 'coredns', namespace) | {'spec': {'template': {'metadata': {'labels': label}}}}
        rs = meta('ReplicaSet', name+'-rs', namespace)
        rs['metadata']['ownerReferences'] = [{'kind': 'Deployment', 'uid': deployment['metadata']['uid'], 'controller': True}]
        pod = meta('Pod', name+'-pod', namespace)
        pod['metadata'].update(labels=label, ownerReferences=[{'kind': 'ReplicaSet', 'uid': rs['metadata']['uid'], 'controller': True}])
        pod['spec'] = {'nodeName': b['receipt']['node']['name']}
        pod['status'] = {'phase': 'Running', 'podIP': '10.42.0.7' if prefix == 'callback' else '10.42.0.10',
                         'conditions': [{'type': 'Ready', 'status': 'True'}], 'containerStatuses': [{'name': 'api', 'ready': True,
                         'containerID': 'cri://api', 'restartCount': 0, 'imageID': 'api@sha256:abc', 'state': {'running': {'startedAt':'fixture'}}}]}
        service = meta('Service', name, namespace) | {'spec': {'clusterIP': ip, 'selector': label,
                      'ports': [{'port': port, 'targetPort': port, 'protocol': 'TCP'}]}}
        slices = listing([meta('EndpointSlice', name+'-slice', namespace) | {'addressType': 'IPv4',
            'ports': [{'port': port, 'protocol': 'TCP'}], 'endpoints': [{'conditions': {'ready': True},
            'addresses': [pod['status']['podIP']], 'targetRef': {'kind': 'Pod', 'namespace': namespace, 'uid': pod['metadata']['uid']}}]}])
        slices['items'][0]['metadata'].update(labels={'kubernetes.io/service-name': name}, ownerReferences=[{'kind': 'Service', 'uid': service['metadata']['uid']}])
        b[prefix+'Service'], b[prefix+'Slices'] = service, slices
        b['apiPod' if prefix == 'callback' else 'dnsPod'] = pod
        b['apiReplicaSet' if prefix == 'callback' else 'dnsReplicaSet'] = rs
        if prefix == 'dns': b['dnsDeployment'] = deployment
    return b


def test_static_holder_callback_dns_bindings_and_policy_are_narrow(prepared):
    b = prepared; binding = prep.collector_binding(b, NONCE)
    job = prep.holder(b, NONCE)
    assert job['spec']['activeDeadlineSeconds'] == 30 and job['spec']['backoffLimit'] == 0
    assert job['spec']['template']['metadata']['labels'] == {'app': 'access-web-runner', prep.LABEL: NONCE}
    c = capture(binding); lease = dns.lease(c, binding, 3_000_000_000)
    policy = prep.policy(lease, NONCE, 3_000_000_000)
    assert policy['spec']['enableDefaultDeny'] == {'ingress': False, 'egress': False}
    assert policy['spec']['egress'] == [{'toCIDR': ['1.1.1.1/32'], 'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}]}]}]
    current = copy.deepcopy(policy); current['metadata'].update(uid='owned', resourceVersion='2')
    assert prep.deletion(current, {'uid': 'owned', 'specHash': node.digest(current['spec'])})['preconditions']['uid'] == 'owned'
    current['metadata']['uid'] = 'foreign'
    with pytest.raises(ValueError): prep.deletion(current, {'uid': 'owned', 'specHash': node.digest(current['spec'])})


@pytest.mark.parametrize('change', ['service_ip', 'target_uid', 'owner_uid', 'port', 'unready', 'second_endpoint', 'nonce_collision'])
def test_target_or_nonce_substitution_stops_before_collection(prepared, change):
    b = prepared
    if change == 'service_ip': b['callbackService']['spec']['clusterIP'] = '127.0.0.1'
    elif change == 'target_uid': b['callbackSlices']['items'][0]['endpoints'][0]['targetRef']['uid'] = 'other'
    elif change == 'owner_uid': b['apiReplicaSet']['metadata']['ownerReferences'][0]['uid'] = 'other'
    elif change == 'port': b['callbackService']['spec']['ports'][0]['targetPort'] = 9090
    elif change == 'unready': b['apiPod']['status']['conditions'][0]['status'] = 'False'
    elif change == 'second_endpoint': b['callbackSlices']['items'][0]['endpoints'].append(copy.deepcopy(b['callbackSlices']['items'][0]['endpoints'][0]))
    else: b['pods'] = listing([{'metadata': {'labels': {'app': 'access-web-runner', prep.LABEL: NONCE}}}])
    with pytest.raises(ValueError):
        prep.collector_binding(b, NONCE); prep.holder(b, NONCE)


def observed_named_ports(b):
    """Observed API/CoreDNS port shape; synthetic identities stay in fixtures."""
    service=b['callbackService'];pod=b['apiPod']
    service['spec']['selector']={'app':'hosting-api'}
    pod['metadata']['labels']={'app':'hosting-api'}
    b['snapshot']['api']['spec']['template']['metadata']['labels']={'app':'hosting-api'}
    service['spec']['ports']=[{'name':'http','port':8080,'protocol':'TCP','targetPort':'http'}]
    pod['spec']['containers']=[{'name':'api','ports':[{'name':'http','containerPort':8080,'protocol':'TCP'}]}]
    b['callbackSlices']['items'][0]['ports']=[{'name':'http','port':8080,'protocol':'TCP'}]
    b['dnsService']['spec']['ports']=[{'name':'dns','port':53,'protocol':'UDP','targetPort':53},
        {'name':'dns-tcp','port':53,'protocol':'TCP','targetPort':53},
        {'name':'metrics','port':9153,'protocol':'TCP','targetPort':9153}]
    b['dnsSlices']['items'][0]['ports']=[{'name':'dns','port':53,'protocol':'UDP'},
        {'name':'dns-tcp','port':53,'protocol':'TCP'},{'name':'metrics','port':9153,'protocol':'TCP'}]
    b['receipt']=node.preflight(b)
    return b


def test_observed_named_api_and_numeric_multiport_core_dns_shape(prepared):
    b=observed_named_ports(prepared)
    binding=prep.collector_binding(b,NONCE)
    assert binding['callback']['servicePortName']=='http'
    assert binding['callback']['targetPort']=='http' and binding['callback']['resolvedPort']==8080
    assert binding['callback']['targetContainer']=='api' and binding['resolverIp']==b['dnsService']['spec']['clusterIP']
    # A Service port name can differ from its named Pod target; match the
    # EndpointSlice to the Service name and Pod declaration to targetPort.
    b['callbackService']['spec']['ports'][0]['name']='callback'
    b['callbackSlices']['items'][0]['ports'][0]['name']='callback'
    assert prep.collector_binding(b,NONCE)['callback']['targetPort']=='http'


@pytest.mark.parametrize('change',['missing','duplicate','other_container','wrong_container','udp','number',
    'slice_name','slice_number','slice_duplicate','numeric_decoy','target_uid','slice_owner','unready','second_endpoint'])
def test_named_mapping_refuses_ambiguity_substitution_and_unrelated_numeric_ports(prepared,change):
    b=observed_named_ports(prepared);pod=b['apiPod'];s=b['callbackSlices']['items'][0]
    p=pod['spec']['containers'][0]['ports'][0]
    if change=='missing': pod['spec']['containers'][0]['ports']=[]
    elif change=='duplicate': pod['spec']['containers'][0]['ports'].append(copy.deepcopy(p))
    elif change=='other_container': pod['spec']['containers'].append({'name':'sidecar','ports':[copy.deepcopy(p)]})
    elif change=='wrong_container': pod['spec']['containers'][0]['name']='sidecar'
    elif change=='udp': p['protocol']='UDP'
    elif change=='number': p['containerPort']=9090
    elif change=='slice_name': s['ports'][0]['name']='other'
    elif change=='slice_number': s['ports'][0]['port']=9090
    elif change=='slice_duplicate': s['ports'].append(copy.deepcopy(s['ports'][0]))
    elif change=='numeric_decoy': s['ports']=[{'name':'http','port':9090,'protocol':'TCP'},{'name':'other','port':8080,'protocol':'TCP'}]
    elif change=='target_uid': s['endpoints'][0]['targetRef']['uid']='other'
    elif change=='slice_owner': s['metadata']['ownerReferences'][0]['uid']='other'
    elif change=='unready': pod['status']['conditions'][0]['status']='False'
    else: s['endpoints'].append(copy.deepcopy(s['endpoints'][0]))
    with pytest.raises(ValueError): prep.collector_binding(b,NONCE)


def test_numeric_target_with_named_service_port_remains_bound_to_slice_name(prepared):
    b=observed_named_ports(prepared)
    b['callbackService']['spec']['ports'][0]['targetPort']=8080
    assert prep.collector_binding(b,NONCE)['callback']['targetPort']==8080
    b['callbackSlices']['items'][0]['ports'][0]['name']='unrelated'
    with pytest.raises(ValueError): prep.collector_binding(b,NONCE)


def runtime(monkeypatch, tmp_path):
    namespace = {'__name__': 'fixture'}; exec(prep.runtime_source(), namespace)
    expected = {'nonce': NONCE, 'podUid': 'pod', 'jobUid': 'job', 'callbackIp': '10.43.0.7', 'jobDeadlineNs': 50_000_000_000,
                'context': {}, 'lease': {'providerIp': '1.1.1.1', 'endNs': 2_000_000_000, 'expiryNs': 40_000_000_000,
                    'realtimeNs': 100_000_000_000, 'timeNamespace': 'time:[1]', 'bootId': 'boot'}}
    namespace['verify_owner'] = lambda e: 1_000_000_000
    namespace['Path'] = lambda p: SimpleNamespace(readlink=lambda: 'time:[1]', read_text=lambda: 'boot')
    now = [3_000_000_000]
    monkeypatch.setattr(namespace['time'], 'clock_gettime_ns', lambda c: now[0])
    monkeypatch.setattr(namespace['time'], 'time_ns', lambda: 100_000_000_000+now[0]-2_000_000_000)
    real_open = namespace['os'].open
    monkeypatch.setattr(namespace['os'], 'open', lambda p, f, m: real_open(str(tmp_path/'claim'), f, m))
    return namespace, expected, now


@pytest.mark.parametrize('failure', [None, 'callback', 'certificate', 'provider', 'bad_context', 'late_tls'])
def test_single_numeric_worker_closes_sockets_and_never_retries(monkeypatch, tmp_path, failure):
    n, expected, now = runtime(monkeypatch, tmp_path); calls=[]; closes=[]
    class Channel:
        def __enter__(self): return self
        def __exit__(self,*a): closes.append(True)
        def settimeout(self,t): assert t==3
        def connect(self,target):
            calls.append(target)
            if (failure=='callback' and target[1]==8080) or (failure=='provider' and target[1]==443): raise OSError()
            if failure=='late_tls' and target[1]==443: now[0]=39_000_000_000
        def getpeercert(self): return {'fixture':True}
    class Context:
        verify_mode=ssl.CERT_NONE if failure=='bad_context' else ssl.CERT_REQUIRED
        check_hostname=True
        def wrap_socket(self,ch,server_hostname):
            assert server_hostname=='chatgpt.com'
            if failure=='certificate': raise ssl.SSLCertVerificationError()
            return Channel()
    result = n['numeric_once'](expected, lambda *a:Channel(), Context)
    assert result['dnsQueries']==result['httpBytes']==0
    assert len(calls)==(1 if failure=='callback' else 2)
    assert result['providerAttempts']==(0 if failure=='callback' else 1)
    if failure is None: assert result['provider']=='tls_verified'
    if failure=='certificate': assert result['provider']=='certificate_failed'
    assert closes
    with pytest.raises((FileExistsError,n['NumericDeadline'])): n['numeric_once'](expected, lambda *a:Channel(), Context)
    assert len(calls)==(1 if failure=='callback' else 2)


@pytest.mark.parametrize('change', ['expiry', 'late_holder', 'wrong_boot', 'wrong_namespace'])
def test_clock_or_lease_boundary_stops_all_connects(monkeypatch, tmp_path, change):
    n,e,now = runtime(monkeypatch,tmp_path)
    if change=='expiry': e['lease']['expiryNs']=now[0]+9_999_999_999
    elif change=='late_holder': now[0]=19_000_000_000
    elif change=='wrong_boot': e['lease']['bootId']='other'
    else: e['lease']['timeNamespace']='time:[2]'
    with pytest.raises((ValueError,n['NumericDeadline'])): n['numeric_once'](e, lambda *a: (_ for _ in ()).throw(AssertionError('connect')), ssl.create_default_context)


def scoped_fixture(b):
    binding=prep.collector_binding(b,NONCE); c=capture(binding); lease=dns.lease(c,binding,3_000_000_000)
    job=prep.holder(b,NONCE); job['metadata'].update(uid='job',resourceVersion='2'); job['status']={'startTime':'1970-01-01T00:01:40Z'}
    pod,cep,_=metadata(); pod['metadata'].update(name=prep.NAME+'-fixture',labels=copy.deepcopy(job['spec']['template']['metadata']['labels']),
        resourceVersion='2',ownerReferences=[{'kind':'Job','name':prep.NAME,'uid':'job','controller':True}])
    for k in prep.own.control.JOB_NAME_LABELS: pod['metadata']['labels'][k]=prep.NAME
    for k in prep.own.control.JOB_UID_LABELS: pod['metadata']['labels'][k]='job'
    pod['spec']=copy.deepcopy(job['spec']['template']['spec']); pod['spec']['nodeName']=b['receipt']['node']['name']
    pod['status']['containerStatuses']=[{'name':'diagnose','restartCount':0,'state':{'running':{}},'containerID':'cri://numeric','imageID':prep.direct.RUNNER_IMAGE}]
    cep['metadata']['name']=pod['metadata']['name']; cep['metadata']['ownerReferences'][0]['name']=pod['metadata']['name']
    production=b['policies'][0]; global_policy=b['policies'][2]
    ref=witness(production,global_policy); p=copy.deepcopy(ref[0]['status']['policy']['realized']); p['build']=12
    temporary=prep.policy(lease,NONCE,3_000_000_000); temporary['metadata'].update(uid='numeric-policy',resourceVersion='3')
    e=next(e for e in p['l4']['egress'] if json.loads(e['rule'])['port']==443)
    e['derived-from-rules'].append(['k8s:io.cilium.k8s.policy.derived-from=CiliumNetworkPolicy',
        'k8s:io.cilium.k8s.policy.name='+prep.NAME,'k8s:io.cilium.k8s.policy.namespace=codex-trial','k8s:io.cilium.k8s.policy.uid=numeric-policy'])
    rule=json.loads(e['rule']); selector='&LabelSelector{MatchLabels:map[string]string{cidr:1.1.1.1/32: ,},MatchExpressions:[]LabelSelectorRequirement{},}'
    rule['l7-rules'].append({selector:None}); e['rule']=json.dumps(rule)
    ep=[{'id':cep['status']['id'],'status':copy.deepcopy(cep['status'])|{'policy':{'spec':copy.deepcopy(p),'realized':copy.deepcopy(p),
        'proxy-policy-revision':12,'proxy-statistics':[{'protocol':'dns','location':'egress','port':53,'allocated-proxy-port':54321} for _ in range(2)]}}}]
    data=copy.deepcopy(b); data.update(collectorBaseline=copy.deepcopy(b),job=job,pod=pod,cep=cep,agent=b['agents']['items'][0],
        agentSignature=node.agent_signature(b['agents']['items'][0]),pods=listing([pod]),reference=ref,endpoint=ep,
        temporary=temporary,ownedPolicy={'uid':'numeric-policy'},production=production,globalPolicy=global_policy,lease=lease,binding=binding,capture=c)
    data['holderReceipt']={'holding':True,'networkRequested':False,'nonce':NONCE,'podUid':pod['metadata']['uid'],
        'podName':pod['metadata']['name'],'startNs':1_000_000_000,'startTicks':100,'timeNamespace':'time:[1]','bootId':'boot',
        'cmdlineSha256':hashlib.sha256(('\0'.join(prep.holder_command(NONCE))+'\0').encode()).hexdigest()}
    return data


def test_exact_native_original_plus_cidr_gate(prepared):
    b=scoped_fixture(prepared); before=copy.deepcopy(b)
    scope=prep.scoped(b,NONCE,b['lease'],3_000_000_000)
    assert scope['policyUid']=='numeric-policy' and b==before


@pytest.mark.parametrize('change',['second_pod','uid','nonce','subnet','unknown_native','host_deny_missing','extra_allow','ack','signature'])
def test_scoped_policy_singleton_and_actual_realization_refusals(prepared,change):
    b=scoped_fixture(prepared)
    if change=='second_pod': b['pods']['items'].append(copy.deepcopy(b['pod']))
    elif change=='uid': b['temporary']['metadata']['uid']='foreign'
    elif change=='nonce': b['pod']['metadata']['labels'][prep.LABEL]='b'*32
    elif change=='subnet': b['temporary']['spec']['egress'][0]['toCIDR']=['1.1.1.0/24']
    elif change=='ack': b['endpoint'][0]['status']['policy']['proxy-policy-revision']=11
    elif change=='signature': b['agent']['status']['containerStatuses'][0]['containerID']='foreign'
    else:
        for side in ('spec','realized'):
            p=b['endpoint'][0]['status']['policy'][side]
            if change=='host_deny_missing': p['l4']['egress'].pop(0)
            elif change=='extra_allow': p['l4']['egress'].append(copy.deepcopy(p['l4']['egress'][-1]))
            else:
                e=next(e for e in p['l4']['egress'] if json.loads(e['rule'])['port']==443)
                e['derived-from-rules'].append(['foreign'])
    with pytest.raises(ValueError): prep.scoped(b,NONCE,b['lease'],3_000_000_000)


def result_fixture(b):
    scope=prep.scoped(b,NONCE,b['lease'],3_000_000_000); b['scope']=scope
    b['transportExit']=0
    b['result']={'context':{'leaseHash':node.digest(b['lease']),'policyUid':scope['policyUid'],'policyRv':scope['policyRv'],
        'agentUid':scope['pin']['agentUid'],'policyRevision':scope['policyRevision']},'nonce':NONCE,'podUid':b['pod']['metadata']['uid'],
        'jobUid':b['job']['metadata']['uid'],'dnsQueries':0,'httpBytes':0,'authRpc':False,'nativeStarted':False,'sourceCredentialRead':False,
        'enteredNs':3_000_000_000,'finishedNs':4_000_000_000,'providerAttempts':1,'callback':'tcp_connected','provider':'tls_verified'}
    return b


@pytest.mark.parametrize('change',['transport','nonce','pod','policy','late','http','provider_after_callback_fail'])
def test_postexec_context_and_outcome_cannot_be_substituted(prepared,change):
    b=result_fixture(scoped_fixture(prepared)); r=b['result']
    assert prep.checked_result(b)['webOrNativeSuccess'] is False
    if change=='transport': b['transportExit']=1
    elif change=='nonce': r['nonce']='b'*32
    elif change=='pod': r['podUid']='other'
    elif change=='policy': b['temporary']['metadata']['uid']='other'
    elif change=='late': r['finishedNs']=b['lease']['expiryNs']
    elif change=='http': r['httpBytes']=1
    else: r['callback']='failed'
    with pytest.raises(ValueError): prep.checked_result(b)


@pytest.mark.parametrize('mode',['collector','worker'])
def test_host_claim_transport_failure_is_consumed_without_retry(tmp_path,mode):
    tool=tmp_path/'kubectl'; calls=tmp_path/'calls'
    tool.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >>'+str(calls)+'\nexit 1\n'); tool.chmod(0o700)
    private=tmp_path/'evidence'; private.mkdir(mode=0o700)
    source=tmp_path/'program'; source.write_text('print("fixture")'); source.chmod(0o600)
    wrapper=Path(__file__).with_name('run_numeric_step.sh')
    assert subprocess.run(['bash',str(wrapper),'arm',str(tool),'-','-',str(private)],capture_output=True).returncode==0
    if mode=='worker':
        deadline=private/'dispatch.deadlineCs'
        deadline.write_text(str(int(float(Path('/proc/uptime').read_text().split()[0])*100)+100)+'\n'); deadline.chmod(0o600)
    pod='api-fixture' if mode=='collector' else prep.NAME+'-fixture'
    argv=['bash',str(wrapper),mode,str(tool),pod,str(source),str(private)]
    assert subprocess.run(argv,capture_output=True).returncode==1
    before=calls.read_text(); assert '--request-timeout=8s' in before
    assert subprocess.run(argv,capture_output=True).returncode!=0 and calls.read_text()==before


def test_whole_budget_reserves_cleanup_before_any_host_exec(tmp_path):
    private=tmp_path/'evidence'; private.mkdir(mode=0o700)
    (private/'operation.startCs').write_text('0\n')
    wrapper=Path(__file__).with_name('run_numeric_step.sh')
    assert subprocess.run(['bash',str(wrapper),'budget','/trusted/kubectl','-','-',str(private)],capture_output=True).returncode!=0
