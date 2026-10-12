import ast
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import access_numeric_prepare as prep
import access_numeric_trial as trial
from test_access_numeric import prepared, scoped_fixture, capture, bundle, runner_snapshot, package, observed_named_ports
from test_numeric_timing import original_fixture
from test_access_endpoint_pin import metadata


OBSERVED_VERSION = ('Client: 1.20.2 e0dc92bd 2026-09-15T15:51:54Z go version go1.26.8 linux/arm64\n'
                    'Daemon: 1.20.2 e0dc92bd 2026-09-15T15:51:54Z go version go1.26.8 linux/arm64\n')


def test_observed_client_and_running_daemon_version_envelope():
    assert trial.agent_version(OBSERVED_VERSION)=='1.20.2'
    assert trial.agent_version('Daemon: 1.20.2\nClient: 1.20.2\n')=='1.20.2'


@pytest.mark.parametrize('text',[
    'cilium-dbg 1.20.2\n', 'Client: 1.20.2\n', 'Daemon: 1.20.2\n',
    'Client: 1.20.2\nClient: 1.20.2\n', 'Daemon: 1.20.2\nDaemon: 1.20.2\n',
    'Client: 1.20.2\nDaemon: 1.20.3\n', 'Client: 1.20.3\nDaemon: 1.20.2\n',
    'Client: 1.20.2\nDaemon: 1.20.20\n', 'Client: 1.20.2\nDaemon: 1.20.2+custom\n',
    'Client: 1.20.2\nDaemon: 1.20.2\nDaemon: 1.20.2\n',
    'Client: 1.20.2 Daemon: 1.20.3\nDaemon: 1.20.2\n',
    'Client: 1.20.2\nDaemon: unavailable (1.20.2)\n',
    'Warning: daemon unavailable\nClient: 1.20.2\nDaemon: 1.20.2\n', None])
def test_version_requires_exact_unambiguous_client_and_daemon(text):
    with pytest.raises(ValueError): trial.agent_version(text)


class FakeIO:
    """Complete raw transport substitute, real controller/parser/owner checks."""
    def __init__(self, b, directory, fault=None):
        b=observed_named_ports(copy.deepcopy(b))
        self.evidence=directory;self.fault=fault;self.clock=1_000_000_000
        self.calls=[];self.counts={'Job':0,'CiliumNetworkPolicy':0,'collector':0,'worker':0,'connect':0,'delete':0}
        self.prototype=scoped_fixture(b);self.original=original_fixture(self.prototype)['endpoint']
        self.seed=copy.deepcopy(b);self.job=None;self.policy=None;self.pod=None;self.deleted=[]
        self.holder_start=None
        self.seed['nodes']['items'][0]['status']['nodeInfo']['bootID']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        api_image=next(c['image'] for c in self.seed['snapshot']['api']['spec']['template']['spec']['containers'] if c['name']=='api')
        self.seed['apiPod']['spec']['containers'][0]['image']=api_image
        self.seed['apiPod']['status']['containerStatuses'][0]['imageID']=api_image
        self.seed['nodes']['items'][0]['status']['images']=[{'names':[api_image,prep.direct.RUNNER_IMAGE]}]
        for key,rs in (('apiPod','apiReplicaSet'),('dnsPod','dnsReplicaSet')):
            prefix='callback' if key=='apiPod' else 'dns'
            self.seed[prefix+'Slices']['items'][0]['endpoints'][0]['targetRef']['name']=self.seed[key]['metadata']['name']
            self.seed[key]['metadata']['ownerReferences'][0]['name']=self.seed[rs]['metadata']['name']
            deployment=self.seed['snapshot']['api'] if key=='apiPod' else self.seed['dnsDeployment']
            self.seed[rs]['metadata']['ownerReferences'][0]['name']=deployment['metadata']['name']
        self.ceps={};self.endpoints={}
        for alias,key in (('api','apiPod'),('dns','dnsPod')):
            _,cep,_=metadata();pod=self.seed[key]
            cep['metadata'].update(name=pod['metadata']['name'],namespace=pod['metadata']['namespace'],uid=alias+'-cep',resourceVersion='1')
            cep['metadata']['ownerReferences']=[{'kind':'Pod','name':pod['metadata']['name'],'uid':pod['metadata']['uid']}]
            cp=cep['status'];cp['id']=101 if alias=='api' else 102
            cp['external-identifiers']['k8s-pod-name']=pod['metadata']['namespace']+'/'+pod['metadata']['name']
            cp['networking']['addressing']=[{'ipv4':pod['status']['podIP']}]
            ep=[{'id':cp['id'],'status':copy.deepcopy(cp)|{'policy':copy.deepcopy(self.seed['endpoints'][alias]['policy'])}}]
            self.ceps[alias]=cep;self.endpoints[alias]=ep
            self.seed['endpoints'][alias]=trial.baseline_endpoint(pod,cep,self.seed['agents']['items'][0],ep)
        self.seed['receipt']=prep.node.preflight(self.seed)
        self.seed['status']={'cilium':{'state':'Ok'},'kubernetes':{'state':'Ok'}}

    def call(self,kind,**values):
        self.calls.append((kind,copy.deepcopy(values)));self.clock+=1_000_000
        if kind=='images' and self.fault=='images':raise ValueError()
        if kind=='host':return {'brokerOk':True,'bootId':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                'timeNamespace':str(Path('/proc/self/ns/time').readlink()),'nowCs':self.clock//10_000_000}
        return {'brokerOk':True}

    def arm(self): self.call('arm')

    def save(self,filename,value,text=False):
        assert not (self.evidence/filename).exists()
        if self.fault=='emit_delay' and filename=='stage-emit.json':self.clock+=2_000_000_000
        (self.evidence/filename).write_text(value if text else json.dumps(value))
        self.clock+=1_000_000

    def obj(self,path):
        b=self.seed
        fixed={'/api/v1/nodes':b['nodes'],'/api/v1/nodes/'+b['expectedNode']['name']:b['nodes']['items'][0],
            '/apis/apps/v1/namespaces/kube-system/daemonsets/cilium':b['daemonset'],
            '/api/v1/namespaces/kube-system/pods?labelSelector=k8s-app%3Dcilium':b['agents'],
            '/apis/apiextensions.k8s.io/v1/customresourcedefinitions/ciliumnodeconfigs.cilium.io':b['crd'],
            '/apis/cilium.io/v2/namespaces/kube-system/ciliumnodeconfigs':b['nodeConfigs'],
            '/api/v1/namespaces/kube-system/configmaps/cilium-config':b['configMap'],
            trial.CNPS+'/access-web-boundary-v1':b['policies'][0],
            '/apis/cilium.io/v2/namespaces/m2-hosting/ciliumnetworkpolicies/hosting-access-web-link-v1':b['policies'][1],
            '/apis/cilium.io/v2/ciliumclusterwidenetworkpolicies/anxious-lookout-node-protection':b['policies'][2],
            trial.SERVICES:b['services'],trial.JOBS:{'metadata':{'resourceVersion':'1'},'items':[self.job] if self.job else []},
            trial.CNPS:{'metadata':{'resourceVersion':'1'},'items':[self.policy] if self.policy else []},
            trial.PODS:{'metadata':{'resourceVersion':'1'},'items':[self.pod] if self.pod else []},
            '/apis/discovery.k8s.io/v1/namespaces/codex-trial/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Daccess-web-runner-v1':{'metadata':{'resourceVersion':'1'},'items':[]}}
        for prefix,key,rs in (('callback','apiPod','apiReplicaSet'),('dns','dnsPod','dnsReplicaSet')):
            namespace=b[key]['metadata']['namespace'];svc=b[prefix+'Service']['metadata']['name']
            fixed['/api/v1/namespaces/'+namespace+'/services/'+svc]=b[prefix+'Service']
            fixed['/apis/discovery.k8s.io/v1/namespaces/'+namespace+'/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3D'+svc]=b[prefix+'Slices']
            fixed[trial.resource(b[key])]=b[key];fixed[trial.resource(b[rs])]=b[rs]
            fixed['/apis/cilium.io/v2/namespaces/'+namespace+'/ciliumendpoints/'+b[key]['metadata']['name']]=self.ceps['api' if prefix=='callback' else 'dns']
        for key in ('api','runner','config'):fixed[trial.resource(b['snapshot'][key])]=b['snapshot'][key]
        fixed[trial.resource(b['dnsDeployment'])]=b['dnsDeployment']
        for plural,kind in (('pvcs','PersistentVolumeClaim'),('pvs','PersistentVolume')):
            for v in b['snapshot'][plural]['items']:fixed[trial.resource(v|{'kind':kind})]=v|{'kind':kind}
        agent=b['agents']['items'][0];fixed[trial.resource(agent)]=agent
        if self.job:fixed[trial.JOBS+'/'+prep.NAME]=self.job
        if self.policy:fixed[trial.CNPS+'/'+prep.NAME]=self.policy
        if self.pod:
            cep=copy.deepcopy(self.prototype['cep']);cep['metadata']['name']=self.pod['metadata']['name']
            fixed['/apis/cilium.io/v2/namespaces/codex-trial/ciliumendpoints/'+self.pod['metadata']['name']]=cep
        return copy.deepcopy(fixed[path])

    def batch(self,operations,deadline):
        out=[];self.clock+=5_000_000
        if self.fault=='binding_delay' and (self.evidence/'stage-binding.json').exists() and not (self.evidence/'stage-lease.json').exists():
            self.clock+=3_000_000_000;self.fault=None
        for op in operations:
            argv=op['argv'];self.calls.append(('kubectl',copy.deepcopy(op)))
            if argv[0]=='create':
                obj=json.loads(op['stdin']);kind=obj['kind'];self.counts[kind]+=1
                obj['metadata'].update(uid='job' if kind=='Job' else 'numeric-policy',resourceVersion='2')
                if kind=='Job':
                    self.holder_start=self.clock
                    obj['status']={'startTime':'1970-01-01T00:01:40Z'};self.job=obj
                    self.pod=copy.deepcopy(self.prototype['pod']);self.pod['spec']=copy.deepcopy(obj['spec']['template']['spec'])
                    self.pod['spec']['nodeName']=self.seed['expectedNode']['name']
                    self.pod['metadata']['labels']=copy.deepcopy(obj['spec']['template']['metadata']['labels'])
                    for k in prep.own.control.JOB_NAME_LABELS:self.pod['metadata']['labels'][k]=prep.NAME
                    for k in prep.own.control.JOB_UID_LABELS:self.pod['metadata']['labels'][k]='job'
                    if self.fault=='original_delay':self.clock+=3_100_000_000
                else:
                    self.policy=obj
                    if self.fault=='create_delay':self.clock+=3_000_000_000
                if self.fault=='create_unknown' and kind=='CiliumNetworkPolicy':out.append((124,''));continue
                value=obj
            elif argv[0]=='delete':
                self.counts['delete']+=1;body=json.loads(op['stdin']);obj=self.job if argv[2].startswith(trial.JOBS) else self.policy
                assert body['preconditions']=={'uid':obj['metadata']['uid'],'resourceVersion':obj['metadata']['resourceVersion']}
                if self.fault=='cleanup_failure':out.append((124,''));continue
                self.deleted.append(obj['kind'])
                if obj['kind']=='Job':self.job=None;self.pod=None
                else:self.policy=None
                value={'status':'Success'}
            elif argv[0]=='config':value='https://127.0.0.1:6443'
            elif 'logs' in argv:
                command=self.job['spec']['template']['spec']['containers'][0]['command']
                value={'holding':True,'networkRequested':False,'nonce':self.pod['metadata']['labels'][prep.LABEL],
                       'podUid':self.pod['metadata']['uid'],'podName':self.pod['metadata']['name'],'startTicks':100,
                       'startNs':self.holder_start,'cmdlineSha256':hashlib.sha256(('\0'.join(command)+'\0').encode()).hexdigest(),
                       'timeNamespace':str(Path('/proc/self/ns/time').readlink()),'bootId':self.seed['receipt']['binding']['bootId']}
            elif 'cilium-dbg' in argv:
                tail=argv[argv.index('cilium-dbg')+1:]
                if tail==['version']:value=OBSERVED_VERSION
                elif tail==['status','-o','json']:value=self.seed['status']
                elif tail[0]=='config':value=self.seed['settings'][prep.node.KEYS[trial.FLAGS.index(tail[2])]]
                else:
                    ident=int(tail[2]);diag=self.prototype['cep']['status']['id']
                    if ident==diag and self.pod:
                        value=copy.deepcopy(self.prototype['endpoint'] if self.policy else self.original)
                        if self.fault=='foreign_endpoint':value[0]['id']+=1
                    else:value=next(v for k,v in self.endpoints.items() if v[0]['id']==ident)
            else:value=self.obj(argv[-1])
            out.append((0,value if isinstance(value,str) else json.dumps(value)))
        return out

    def step(self,mode,pod,filename,deadline):
        self.counts[mode]+=1;self.calls.append((mode,{'pod':pod,'deadline':deadline}))
        if mode=='collector':
            baseline=json.loads((self.evidence/'baseline.json').read_text())
            binding=prep.collector_binding(baseline,baseline['nonce']);c=capture(binding)
            begin=self.clock;self.clock+=1_000_000_000
            c.update(beginNs=begin,endNs=self.clock,realtimeNs=100_000_000_000+self.clock-self.holder_start,
                     bootId=self.seed['receipt']['binding']['bootId'],timeNamespace=str(Path('/proc/self/ns/time').readlink()))
            if self.fault=='lease_namespace':c['timeNamespace']='time:[0]'
            return 0,json.dumps(c)
        source=(self.evidence/filename).read_text();expected=ast.literal_eval(source.split('\nEXPECTED=',1)[1].split('\ntry:',1)[0])
        if self.fault=='late_dispatch':self.clock=self.holder_start+18_000_000_000
        dispatch=json.loads((self.evidence/'dispatch.stage.json').read_text())['stageDeadlineNs']//10_000_000
        (self.evidence/'worker.dispatch.json').write_text(json.dumps({'dispatchAdmittedCs':dispatch-1,'dispatchDeadlineCs':dispatch}))
        ns={};exec(prep.runtime_source(),ns)
        ns['verify_owner']=lambda e:self.holder_start
        ns['time']=SimpleNamespace(clock_gettime_ns=lambda _:self.clock,time_ns=lambda:100_000_000_000+self.clock-self.holder_start,CLOCK_BOOTTIME=7)
        ns['signal']=SimpleNamespace(signal=lambda *a:None,setitimer=lambda *a:None,SIGALRM=14,ITIMER_REAL=0)
        real=ns['os'];ns['os']=SimpleNamespace(fdopen=real.fdopen,open=lambda p,f,m:real.open(str(self.evidence/'remote.claim'),f,m),
            O_CREAT=real.O_CREAT,O_EXCL=real.O_EXCL,O_WRONLY=real.O_WRONLY,O_NOFOLLOW=real.O_NOFOLLOW)
        io=self
        class Channel:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def settimeout(self,t):pass
            def connect(self,target):io.counts['connect']+=1;io.clock+=200_000_000
            def getpeercert(self):return {'substitute':True}
        class Context:
            verify_mode=ssl.CERT_REQUIRED;check_hostname=True
            def wrap_socket(self,*a,**k):return Channel()
        try:result=ns['numeric_once'](expected,lambda *a:Channel(),Context)
        except ns['NumericDeadline']:return 1,'{}'
        return 0,json.dumps(result)


@pytest.mark.parametrize('fault',[None,'images','binding_delay','create_delay','create_unknown','foreign_endpoint','late_dispatch','cleanup_failure',
                                 'original_delay','lease_namespace','emit_delay'])
def test_complete_controller_uses_raw_helpers_and_bounded_exact_cleanup(prepared,tmp_path,fault):
    io=FakeIO(prepared,tmp_path,fault);controller=trial.Trial(io.seed,io,lambda:io.clock)
    outcome=controller.run()
    if fault=='images':assert outcome['cleanup']['status']=='not_armed' and io.counts['Job']==0
    elif fault in ('create_unknown','cleanup_failure'):assert outcome['cleanup']['status']=='unknown_handoff'
    else:assert outcome['cleanup']['status']=='verified',outcome
    if fault is None:
        assert outcome['numericAttempt']=='completed' and outcome['providerTls']=='tls_verified',outcome
        assert io.counts['collector']==io.counts['worker']==1 and io.counts['connect']==2
    elif fault in ('images','binding_delay','create_delay','create_unknown','foreign_endpoint','original_delay','lease_namespace','emit_delay'):
        assert io.counts['worker']==io.counts['connect']==0
    elif fault=='late_dispatch':assert io.counts['worker']==1 and io.counts['connect']==0
    assert io.counts['Job']<=1 and io.counts['CiliumNetworkPolicy']<=1 and io.counts['collector']<=1 and io.counts['worker']<=1
    if io.deleted:assert io.deleted[0]=='Job'
    if fault in ('original_delay','lease_namespace'):assert io.counts['CiliumNetworkPolicy']==0
    # Source requests bind each numeric endpoint GET to a preceding full CEP.
    history=[]
    for kind,value in io.calls:
        if kind!='kubectl':continue
        argv=value['argv']
        if argv[0]=='get' and 'ciliumendpoints/' in argv[-1]:history.append('cep')
        if 'cilium-dbg' in argv and 'endpoint' in argv:assert history


def test_fresh_complete_metadata_guard(prepared,tmp_path):
    io=FakeIO(prepared,tmp_path);controller=trial.Trial(io.seed,io,lambda:io.clock)
    current=controller.refresh()
    assert current['receipt']['baselineHash']==io.seed['receipt']['baselineHash']
    prep.holder(current,controller.nonce)


@pytest.mark.parametrize('envelope',[
    OBSERVED_VERSION.replace('Daemon: 1.20.2','Daemon: 1.20.3'),
    OBSERVED_VERSION.split('Daemon:')[0],
    OBSERVED_VERSION+'Daemon: 1.20.2\n'])
def test_bad_running_daemon_envelope_stops_complete_controller_before_arm(prepared,tmp_path,envelope):
    class BadVersion(FakeIO):
        def batch(self,operations,deadline):
            result=super().batch(operations,deadline)
            for i,op in enumerate(operations):
                if op['argv'][-2:]==['cilium-dbg','version']:result[i]=(0,envelope)
            return result
    io=BadVersion(prepared,tmp_path)
    outcome=trial.Trial(io.seed,io,lambda:io.clock).run()
    assert outcome['cleanup']['status']=='not_armed'
    assert io.counts['Job']==io.counts['collector']==io.counts['worker']==0
    assert not any(k=='arm' for k,_ in io.calls)


@pytest.mark.parametrize('bad',['api_image','runner_cache','api_cache'])
def test_fresh_image_gates_refuse_before_agent_exec_or_arm(prepared,tmp_path,bad):
    io=FakeIO(prepared,tmp_path)
    if bad=='api_image':io.seed['apiPod']['status']['containerStatuses'][0]['imageID']='foreign@sha256:'+'f'*64
    else:
        image=prep.direct.RUNNER_IMAGE if bad=='runner_cache' else io.seed['apiPod']['spec']['containers'][0]['image']
        io.seed['nodes']['items'][0]['status']['images'][0]['names'].remove(image)
    outcome=trial.Trial(io.seed,io,lambda:io.clock).run()
    assert outcome['cleanup']['status']=='not_armed' and io.counts['Job']==0
    assert not any(k=='kubectl' and 'cilium-dbg' in v['argv'] for k,v in io.calls)


def test_insufficient_cleanup_budget_is_unknown_without_new_cluster_call(prepared,tmp_path):
    io=FakeIO(prepared,tmp_path);io.clock+=200_000_000_000
    controller=trial.Trial(io.seed,io,lambda:io.clock);controller.arm_ns=io.clock-110_000_000_000;controller.b=io.seed
    controller.owned={'Job':{'uid':'job','specHash':prep.node.digest(io.prototype['job']['spec'])}}
    result=controller.cleanup()
    assert result['status']=='unknown_handoff' and result['remainingOwned']==controller.owned
    assert not any(k=='kubectl' for k,_ in io.calls) and io.counts['delete']==0


@pytest.mark.parametrize('bad',['pod_uid','cep_uid','attachment'])
def test_changed_baseline_attachment_refuses_before_numeric_get(prepared,tmp_path,bad):
    io=FakeIO(prepared,tmp_path)
    if bad=='pod_uid':
        io.seed['apiPod']['metadata']['uid']='replacement'
        io.seed['callbackSlices']['items'][0]['endpoints'][0]['targetRef']['uid']='replacement'
        io.ceps['api']['metadata']['ownerReferences'][0]['uid']='replacement'
    elif bad=='cep_uid':io.ceps['api']['metadata']['uid']='replacement'
    else:io.ceps['api']['status']['external-identifiers']['cni-attachment-id']='replacement'
    outcome=trial.Trial(io.seed,io,lambda:io.clock).run()
    assert outcome['cleanup']['status']=='not_armed' and io.counts['Job']==0
    assert not any(k=='kubectl' and 'cilium-dbg' in v['argv'] for k,v in io.calls)


@pytest.mark.parametrize('bad',['boot','namespace','clock'])
def test_host_clock_identity_mismatch_refuses_before_arm(prepared,tmp_path,bad):
    io=FakeIO(prepared,tmp_path);original=io.call
    def call(kind,**values):
        answer=original(kind,**values)
        if kind=='host':
            if bad=='boot':answer['bootId']='foreign'
            elif bad=='namespace':answer['timeNamespace']='time:[0]'
            else:answer['nowCs']+=100
        return answer
    io.call=call
    outcome=trial.Trial(io.seed,io,lambda:io.clock).run()
    assert outcome['cleanup']['status']=='not_armed' and io.counts['Job']==0
    observed=json.loads((tmp_path/'host-clock-observation-before-arm.json').read_text())
    expected={'boot':'bootMatches','namespace':'timeNamespaceMatches','clock':'clockSkewWithin250ms'}[bad]
    assert observed['checks'][expected] is False and expected in observed['reasons']
    assert observed['host'] is not None and not observed['accepted']


@pytest.mark.parametrize('bad',[None,'namespace','broker_failure','journal_delay'])
def test_clock_only_keeps_observation_and_never_calls_cluster_or_arm(prepared,tmp_path,bad):
    io=FakeIO(prepared,tmp_path);original=io.call;save=io.save
    def call(kind,**values):
        if kind=='host' and bad=='broker_failure':raise ValueError()
        value=original(kind,**values)
        if kind=='host' and bad=='namespace':value['timeNamespace']='time:[0]'
        return value
    def delayed_save(*a,**k):
        if bad=='journal_delay':io.clock+=300_000_000
        return save(*a,**k)
    io.call=call;io.save=delayed_save
    result=trial.Trial(io.seed,io,lambda:io.clock).clock_capture()
    assert result['clockGuardAccepted']==(bad in (None,'journal_delay'))
    assert result['armed'] is False and result['kubernetesCalls']==0
    assert set(k for k,_ in io.calls)<=set(('host','done'))
    record=json.loads((tmp_path/'host-clock-observation-clock-only.json').read_text())
    if bad=='broker_failure':assert record['reasons']==['broker_failure_or_invalid_response'] and record['host'] is None
    else:assert record['accepted']==result['clockGuardAccepted']


@pytest.mark.parametrize('kind',['matching_extra','unavailable_extra'])
def test_provenance_cannot_override_differing_identity(prepared,tmp_path,monkeypatch,kind):
    io=FakeIO(prepared,tmp_path);call=io.call
    metadata={'pid':123,'reader':'substitute','namespaces':{k:{'available':True,'link':'time:[9]','device':1,'inode':9}
              for k in ('time','time_for_children')},'timensOffsets':{'available':True,'base64':'MA=='}}
    if kind=='unavailable_extra':metadata={'namespaces':{'time':{'available':False}},'timensOffsets':{'available':False}}
    monkeypatch.setattr(trial,'namespace_provenance',lambda:copy.deepcopy(metadata))
    def changed(kind,**values):
        result=call(kind,**values)
        if kind=='host':result.update(timeNamespace='time:[0]',namespaceProvenance=copy.deepcopy(metadata))
        return result
    io.call=changed
    result=trial.Trial(io.seed,io,lambda:io.clock).clock_capture()
    assert result['clockGuardAccepted'] is False
    assert result['namespaceProvenanceStatus']==('collected' if kind=='matching_extra' else 'unknown')
    observed=json.loads((tmp_path/'host-clock-observation-clock-only.json').read_text())
    assert 'timeNamespaceMatches' in observed['reasons']
    assert not any(k=='kubectl' or k=='arm' for k,_ in io.calls)


@pytest.mark.parametrize('bad',['float_id','extra_owner'])
def test_baseline_native_binding_rejects_nonexact_identity(prepared,tmp_path,bad):
    io=FakeIO(prepared,tmp_path);cep=copy.deepcopy(io.ceps['api']);response=copy.deepcopy(io.endpoints['api'])
    if bad=='float_id':response[0]['id']=float(response[0]['id'])
    else:cep['metadata']['ownerReferences'].append({'kind':'Pod','name':'foreign','uid':'foreign'})
    with pytest.raises(ValueError):trial.baseline_endpoint(io.seed['apiPod'],cep,io.seed['agents']['items'][0],response)


@pytest.mark.skipif(os.getuid()!=0,reason='Private host broker contract requires root; run this case in root Docker')
@pytest.mark.parametrize('fault',['images','job_unknown','controller_stall','clock_only','clock_forbidden','clock_collision','clock_foreign','clock_timeout','clock_exhausted','clock_signal'])
def test_actual_shell_broker_and_wire_controller_refuse_without_retry(prepared,tmp_path,fault):
    # Docker/git/kubectl CLI substitutes execute INSIDE this network-none
    # test container. The actual shell broker and Wire controller are used.
    prototype=tmp_path/'prototype';prototype.mkdir()
    io=FakeIO(prepared,prototype);controller=trial.Trial(io.seed,io,lambda:io.clock)
    controller.refresh()
    mapping={}
    for kind,value in list(io.calls):
        if kind=='kubectl':mapping[json.dumps(value['argv'])]=io.batch([value],io.clock+13_000_000_000)[0][1]
    mapping[json.dumps(['get','--raw',trial.JOBS+'/'+prep.NAME])]=json.dumps(io.prototype['job'])
    slice_path='/apis/discovery.k8s.io/v1/namespaces/codex-trial/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Daccess-web-runner-v1'
    mapping[json.dumps(['get','--raw',slice_path])]=json.dumps(io.obj(slice_path))
    table=tmp_path/'table.json';table.write_text(json.dumps(mapping))
    seed=tmp_path/'seed.json';seed.write_text(json.dumps(io.seed))
    tools=tmp_path/'bin';tools.mkdir();calls=tmp_path/'calls.log'
    python=sys.executable
    (tools/'kubectl').write_text('#!'+python+'\n'+'''
import json,os,sys
from pathlib import Path
args=[a for a in sys.argv[1:] if not a.startswith('--request-timeout=')]
with open(os.environ['MOCK_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[0]=='create':raise SystemExit(124)
if args[0]=='delete':
 body=json.load(sys.stdin);assert body['preconditions']['uid']=='job'
 print('{"status":"Success"}');raise SystemExit()
value=json.loads(Path(os.environ['MOCK_TABLE']).read_text()).get(json.dumps(args))
if value is None:raise SystemExit(1)
print(value)
''')
    (tools/'git').write_text('#!/bin/bash\ncase "$*" in *rev-parse*) printf "%s\\n" '+('a'*40)+';; esac\n')
    (tools/'docker').write_text('#!'+python+'\n'+'''
import json,os,signal,sys,time
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['MOCK_DOCKER_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
image='sha256:e1cd49c5e8d36fae0689e727c06459529bbc67e823e08385602ea534c6397450'
state=Path(os.environ['MOCK_DOCKER_STATE'])
if args[0]=='info':
 if os.environ['MOCK_FAULT']=='clock_timeout':time.sleep(5)
 print(json.dumps('runc') if 'DefaultRuntime' in args[-1] else 'runc\\nsysbox-runc\\n');raise SystemExit()
if args[0]=='inspect':
 own=json.loads(state.read_text());assert args[-1]==own['id']
 if os.environ['MOCK_FAULT']=='clock_foreign':own['imageId']='sha256:'+'b'*64
 print(own['id']+' '+own['imageId']+' '+own['name'])
 if 'HostConfig' in args[args.index('--format')+1]:print(json.dumps(own))
 raise SystemExit()
if args[0]=='image':
 if os.environ['MOCK_FAULT']=='images':raise SystemExit(1)
 print('sha256:'+'a'*64);raise SystemExit()
if args[0]=='stop':raise SystemExit()
assert args[0]=='run' and args[args.index('--network')+1]=='none' and '--read-only' in args and '--pull=never' in args
assert '--runtime' not in args and '--pid' not in args and '--privileged' not in args
if os.environ['MOCK_FAULT']=='clock_collision':raise SystemExit(125)
if os.environ['MOCK_FAULT']=='clock_exhausted':time.sleep(10)
cid='c'*64;Path(args[args.index('--cidfile')+1]).write_text(cid)
state.write_text(json.dumps({'id':cid,'name':'/'+args[args.index('--name')+1],'imageId':image,'imageRef':image,'user':'0:0','runtime':'runc','isolation':'','usernsMode':'','networkMode':'none','pidMode':'','readonlyRootfs':True,'privileged':False,'capDrop':['ALL'],'securityOpt':['no-new-privileges']}))
if os.environ['MOCK_FAULT']=='clock_signal':
 time.sleep(0.2);os.kill(os.getppid(),signal.SIGTERM);time.sleep(10)
mounts=[args[i+1] for i,a in enumerate(args) if a=='--mount']
mapping={m.split('dst=')[1].split(',')[0]:m.split('src=')[1].split(',')[0] for m in mounts}
start=args.index('/checks/access_numeric_trial.py')
forward=[]
for a in args[start:]:
 for alias,source in mapping.items():
  if a==alias or a.startswith(alias+'/'):a=source+a[len(alias):];break
 forward.append(a)
if os.environ['MOCK_FAULT']=='clock_forbidden':
 os.execv(sys.executable,[sys.executable,'-c',"import sys,time;print('arm\\t1\\t0',flush=True);time.sleep(10)"])
if os.environ['MOCK_FAULT']=='controller_stall' and '--cleanup-only' not in forward:
 os.execv(sys.executable,[sys.executable,os.environ['MOCK_STALL'],mapping['/evidence'],mapping['/evidence']+'/seed.json'])
os.execv(sys.executable,[sys.executable,'-B',*forward])
''')
    stall=tools/'stall.py'
    stall.write_text('''import json,sys,time
from pathlib import Path
import access_numeric_trial as t
w=t.Wire(sys.argv[1]);seed=json.loads(Path(sys.argv[2]).read_text());start=time.clock_gettime_ns(time.CLOCK_BOOTTIME)
w.arm();w.save('baseline.json',seed);w.save('arm.json',{'armNs':start,'nonce':'a'*32})
w.save('owned-Job.json',json.loads(Path(sys.argv[2]).with_name('seed.json').read_text())['_testOwned'])
w.call('deadline',deadlineCs=(time.clock_gettime_ns(time.CLOCK_BOOTTIME)+2_000_000_000)//10_000_000)
time.sleep(10)
''')
    seed_value=copy.deepcopy(io.seed)
    seed_value['_testOwned']={'uid':'job','specHash':prep.node.digest(io.prototype['job']['spec'])}
    seed.write_text(json.dumps(seed_value))
    for tool in tools.iterdir():tool.chmod(0o700)
    source=tmp_path/'source';helpers=source/'infra'/'hosting';helpers.mkdir(parents=True)
    (helpers/'m3').symlink_to(Path(trial.__file__).parent,target_is_directory=True)
    evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
    docker_calls=tmp_path/'docker-calls';docker_state=tmp_path/'docker-state'
    env=dict(os.environ,PATH=str(tools)+':'+os.environ['PATH'],MOCK_CALLS=str(calls),MOCK_TABLE=str(table),MOCK_FAULT=fault,MOCK_STALL=str(stall),MOCK_DOCKER_CALLS=str(docker_calls),MOCK_DOCKER_STATE=str(docker_state))
    argv=['bash',str(Path(trial.__file__).with_name('run_numeric_trial.sh')),str(source),str(tools/'kubectl'),str(seed),str(evidence),'a'*40]
    if fault.startswith('clock_'):argv.append('--clock-only')
    started=time.monotonic()
    result=subprocess.run(argv,env=env,capture_output=True,timeout=60)
    assert result.returncode==(1 if fault in ('controller_stall','clock_forbidden','clock_collision','clock_exhausted','clock_signal') else 0),(result.stderr.decode(),[(x.name,x.read_text()[-1000:]) for x in evidence.glob('controller-*.stderr')])
    raw=[json.loads(l) for l in calls.read_text().splitlines()] if calls.exists() else []
    docker_raw=[json.loads(l) for l in docker_calls.read_text().splitlines()]
    assert all(a[-1]=='c'*64 for a in docker_raw if a[0] in ('inspect','stop'))
    if fault in ('clock_collision','clock_foreign'):
        assert not any(a[0]=='stop' for a in docker_raw)
        if fault=='clock_collision':assert not any(a[0]=='inspect' for a in docker_raw)
    if fault.startswith('clock_'):
        assert raw==[] and not (evidence/'operation.startCs').exists()
        if fault=='clock_signal':
            assert time.monotonic()-started<5
            assert sum(a[0]=='stop' for a in docker_raw)==1,(docker_raw,result.stderr.decode(),[(x.name,x.read_text()) for x in evidence.glob('controller-*.identity.*')])
            assert not (evidence/'clock-complete.json').exists()
            return
        if fault=='clock_exhausted':
            assert time.monotonic()-started<15
            assert not (evidence/'clock-complete.json').exists()
            assert not any(a[0] in ('info','inspect','stop') for a in docker_raw)
            return
        if fault in ('clock_only','clock_foreign','clock_timeout'):
            result=json.loads((evidence/'clock-result.json').read_text())
            assert result['runtimeMetadataStatus']==('collected' if fault=='clock_only' else 'unknown')
            complete=json.loads((evidence/'clock-complete.json').read_text())
            budget=json.loads((evidence/'clock-budget.json').read_text())
            assert complete['completedCs']<complete['deadlineCs']==budget['deadlineCs']
            assert result['clockGuardAccepted'] and result['armed'] is False
            assert list(evidence.glob('clock-host-response-*.json'))
            assert (evidence/'host-clock-observation-clock-only.json').exists()
            observation=json.loads((evidence/'host-clock-observation-clock-only.json').read_text())
            host=observation['host'];controller=observation['controller']
            assert host['namespaceReader']=='external-readlink:/proc/self/ns/time'
            assert controller['namespaceReader']=='python-in-process:/proc/self/ns/time'
            assert host['namespaceProvenance']['pid']!=controller['namespaceProvenance']['pid']
            for observer in (host,controller):
                provenance=observer['namespaceProvenance']
                for ns in ('time','time_for_children'):
                    assert provenance['namespaces'][ns]['available']
                    assert type(provenance['namespaces'][ns]['device']) is int and type(provenance['namespaces'][ns]['inode']) is int
                assert base64.b64decode(provenance['timensOffsets']['base64'])
            assert result['namespaceProvenanceStatus']=='collected'
        else:assert not (evidence/'arm.stdout').exists()
        return
    if fault=='controller_stall':
        recovered=json.loads((evidence/'recovery.json').read_text())
        assert recovered['status']=='verified' and recovered['fullFalseHealth']
        assert sum(a[0]=='delete' for a in raw)==1 and not any(a[0]=='create' for a in raw)
        assert (evidence/'delete-attempt-Job.json').exists()
        return
    outcome=json.loads((evidence/'outcome.json').read_text())
    if fault=='images':
        assert outcome['cleanup']['status']=='not_armed' and not (evidence/'operation.startCs').exists()
        assert not any(a[0]=='create' for a in raw)
    else:
        assert outcome['cleanup']['status']=='unknown_handoff'
        assert sum(a[0]=='create' for a in raw)==1 and not any(a[0]=='delete' for a in raw)
        assert (evidence/'uncertain-Job.json').exists() and not (evidence/'owned-Job.json').exists()
    assert not any('python' in a or 'logs' in a for a in raw)
