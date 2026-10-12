import copy
import json
from pathlib import Path
import ssl
import subprocess
import sys
import time

import pytest

import access_numeric_timing as timing
import access_numeric_prepare as prep
import access_numeric_lease as dns
import access_node_proxy_prepare as node
from test_access_numeric import prepared, scoped_fixture, capture, runtime, NONCE
from test_access_node_proxy import bundle
from test_access_runner_dns_prepare import runner_snapshot
from test_access_web_prepare import package


def test_ca_preparation_consuming_lease_prevents_tls_and_closes_sockets(monkeypatch,tmp_path):
    n,e,now=runtime(monkeypatch,tmp_path); wrapped=[]; closed=[]
    e['lease']['expiryNs']=now[0]+10_000_000_000
    class Channel:
        def __enter__(self): return self
        def __exit__(self,*a): closed.append(True)
        def settimeout(self,t): pass
        def connect(self,target): pass
    class Context:
        verify_mode=ssl.CERT_REQUIRED
        check_hostname=True
        def wrap_socket(self,ch,server_hostname): wrapped.append(True); return Channel()
    def slow_context(): now[0]+=8_000_000_000; return Context()
    r=n['numeric_once'](e,lambda *a:Channel(),slow_context)
    assert r['deadlineExceeded'] and wrapped==[] and len(closed)==2
    with pytest.raises((FileExistsError,n['NumericDeadline'])): n['numeric_once'](e,lambda *a:Channel(),slow_context)
    assert wrapped==[]


def test_actual_first_connect_refuses_when_socket_preparation_reaches_cutoff(monkeypatch,tmp_path):
    n,e,now=runtime(monkeypatch,tmp_path);calls=[];closed=[]
    now[0]=17_000_000_000
    class Channel:
        def __enter__(self): return self
        def __exit__(self,*a): closed.append(True)
        def settimeout(self,t): now[0]=19_000_000_000
        def connect(self,target): calls.append(target)
    result=n['numeric_once'](e,lambda *a:Channel(),ssl.create_default_context)
    assert result['deadlineExceeded'] and calls==[] and closed==[True]
    assert result['providerAttempts']==0


@pytest.mark.parametrize('late',[False,True])
def test_synchronous_two_second_batch_uses_dispatch_receipt_not_completion_clock(tmp_path,late):
    # Adapted from review's test_numeric_dispatch_gap.py: same real wrapper
    # and 2s fake kubectl, but explicit last-stage dispatch receipt contract.
    evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
    now=time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    start=now-1_000_000_000
    deadline=timing.admit('dispatch',start,now)
    (evidence/'operation.startCs').write_text(str(now//10_000_000)+'\n')
    dispatch_file=evidence/'dispatch.deadlineCs'
    dispatch_file.write_text(str((now-10_000_000 if late else deadline)//10_000_000)+'\n');dispatch_file.chmod(0o600)
    tool=tmp_path/'kubectl';calls=tmp_path/'calls'
    tool.write_text('#!/bin/bash\necho called > '+str(calls)+'\nsleep 2\nprintf "worker finished\\n"\n');tool.chmod(0o700)
    program=tmp_path/'program';program.write_text('pass\n');program.chmod(0o600)
    argv=['bash',str(Path(__file__).with_name('run_numeric_step.sh')),'worker',str(tool),prep.NAME+'-test',str(program),str(evidence)]
    result=subprocess.run(argv,capture_output=True,timeout=5)
    if late:
        assert result.returncode!=0 and not calls.exists()
        assert not (evidence/'worker.claim').exists() and not (evidence/'worker.dispatch.json').exists()
    else:
        assert result.returncode==0 and (evidence/'worker.exit').read_text()=='0\n'
        end=time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        assert end-now>=2_000_000_000
        with pytest.raises(ValueError): timing.completed(start,deadline,end)
        timing.dispatched(start,deadline,json.loads((evidence/'worker.dispatch.json').read_text()))
        holder=evidence/'holder.json'
        holder.write_text(json.dumps({'startNs':start,'bootId':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                     'timeNamespace':str(Path('/proc/self/ns/time').readlink())}))
        saved=evidence/'dispatch.stage.json'
        saved.write_text(json.dumps({'stage':'dispatch','stageDeadlineNs':deadline}))
        check=[sys.executable,'-B',str(Path(timing.__file__)),'--stage','dispatch','--holder',str(holder),'--completed',str(saved)]
        assert subprocess.run(check,capture_output=True).returncode!=0
        verified=subprocess.run(check+['--dispatched',str(evidence/'worker.dispatch.json')],capture_output=True)
        assert verified.returncode==0 and json.loads(verified.stdout)['hostDispatchAdmittedWithinBound']
        assert subprocess.run(argv,capture_output=True).returncode!=0
        assert calls.read_text()=='called\n'


@pytest.mark.parametrize('receipt',[{'dispatchAdmittedCs':100,'dispatchDeadlineCs':100},
                                  {'dispatchAdmittedCs':99,'dispatchDeadlineCs':101},
                                  {'dispatchAdmittedCs':True,'dispatchDeadlineCs':100}])
def test_dispatch_receipt_refuses_late_mismatched_or_invalid_time(receipt):
    with pytest.raises(ValueError): timing.dispatched(1,1_000_000_000,receipt)


@pytest.mark.parametrize('mode', ['collector','worker','budget','budget-create','budget-read','budget-prepare','budget-result'])
def test_late_host_admission_refuses_before_claim_or_new_call(tmp_path,mode):
    d=tmp_path/'evidence';d.mkdir(mode=0o700)
    cs=int(float(Path('/proc/uptime').read_text().split()[0])*100)
    (d/'operation.startCs').write_text(str(cs-7400)+'\n')
    tool=tmp_path/'kubectl';calls=tmp_path/'calls'
    tool.write_text('#!/bin/bash\necho called > '+str(calls)+'\n');tool.chmod(0o700)
    program=tmp_path/'program';program.write_text('pass\n');program.chmod(0o600)
    pod='api-test' if mode=='collector' else prep.NAME+'-test'
    r=subprocess.run(['bash',str(Path(__file__).with_name('run_numeric_step.sh')),mode,str(tool),pod,str(program),str(d)],capture_output=True)
    assert r.returncode!=0 and not calls.exists() and not (d/(mode+'.claim')).exists()


def original_fixture(b):
    value=copy.deepcopy(b)
    for side in ('spec','realized'):
        p=value['endpoint'][0]['status']['policy'][side]
        e=next(e for e in p['l4']['egress'] if json.loads(e['rule'])['port']==443)
        e['derived-from-rules'].pop()
        rule=json.loads(e['rule']);rule['l7-rules'].pop();e['rule']=json.dumps(rule)
    return value


def critical_path(prepared, delay=None):
    """Actual emit/parser/owner/row checks, scripted IO clock, no live calls."""
    b=scoped_fixture(prepared); original=original_fixture(b)
    start=1_000_000_000;now=start+300_000_000
    counts={'collector':0,'cnpCreate':0,'provider':0,'cleanupJob':0,'cleanupPolicy':0}
    trace=[];cpu_start=time.perf_counter_ns();lease=None;scope=None
    # Simulated IO + tooling startup durations, not production speed claims.
    durations=dict(zip(timing.STAGES,(1.5,1.0,.8,.6,.3,.8,.5,.3)))
    try:
        for stage in timing.STAGES:
            deadline=timing.admit(stage,start,now)
            if stage=='original': prep.original_scope(original,NONCE)
            elif stage=='collector': counts['collector']+=1
            elif stage=='binding': assert prep.collector_binding(b['collectorBaseline'],NONCE)==b['binding']
            elif stage=='lease':
                c=capture(b['binding']);c.update(beginNs=start+1_800_000_000,endNs=start+2_800_000_000)
                lease=dns.lease(c,b['binding'],now)
                prep.policy(lease,NONCE,now)
            elif stage=='create': counts['cnpCreate']+=1
            elif stage=='realization': scope=prep.scoped(b,NONCE,lease,now)
            elif stage=='emit': prep.program(b,scope,lease)
            elif stage=='dispatch': counts['provider']+=1  # Substitute only.
            cost=durations[stage]+(14 if stage==delay else 0)
            now+=int(cost*1_000_000_000)
            trace.append({'stage':stage,'elapsedMs':(now-start)//1_000_000})
            timing.completed(start,deadline,now)
        return counts,trace,(time.perf_counter_ns()-cpu_start)/1e6,True
    except ValueError:
        return counts,trace,(time.perf_counter_ns()-cpu_start)/1e6,False
    finally:
        owned_job={'uid':b['job']['metadata']['uid'],'specHash':node.digest(b['job']['spec'])}
        prep.deletion(b['job'],owned_job);counts['cleanupJob']+=1
        if counts['cnpCreate']:
            prep.deletion(b['temporary'],{'uid':b['temporary']['metadata']['uid'],'specHash':node.digest(b['temporary']['spec'])})
            counts['cleanupPolicy']+=1


def test_offline_end_to_end_critical_path_preserves_all_checks(prepared):
    counts,trace,cpu,passed=critical_path(prepared)
    assert passed and counts=={'collector':1,'cnpCreate':1,'provider':1,'cleanupJob':1,'cleanupPolicy':1}
    assert trace[-1]['elapsedMs']==6100
    print(json.dumps({'scriptedCriticalPathMs':trace[-1]['elapsedMs'],'actualHelperCpuMs':round(cpu,3),'trace':trace,'liveSuccess':False}))


@pytest.mark.parametrize('stage',['original','collector','binding','lease','create','realization','emit'])
def test_slow_critical_path_stops_before_next_create_provider_and_cleans(prepared,stage):
    counts,trace,cpu,passed=critical_path(prepared,stage)
    assert not passed and counts['provider']==0 and counts['cleanupJob']==1
    if stage in ('original','collector','binding','lease'): assert counts['cnpCreate']==0 and counts['cleanupPolicy']==0
    else: assert counts['cnpCreate']==1 and counts['cleanupPolicy']==1
    assert counts['collector']<=1
