"""Host evidence failure matrix with synthetic proc/CRI, no live node probe."""
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from ops.runner_termination import HostTermination
from runner.lifecycle import Refused


@pytest.fixture
def evidence(tmp_path):
    observer = HostTermination.__new__(HostTermination)
    observer.node_name, observer.node_uid, observer.boot_id = 'node', uuid4(), uuid4()
    observer.proc = tmp_path
    boot = tmp_path/'sys/kernel/random'; boot.mkdir(parents=True)
    (boot/'boot_id').write_text(str(observer.boot_id))
    ns = tmp_path/'1/ns'; ns.mkdir(parents=True)
    (ns/'pid').symlink_to(os.readlink('/proc/self/ns/pid'))
    (tmp_path/'1/cgroup').write_text('0::/init.scope\n')
    pod = uuid4()
    node = {'kind':'Node','metadata':{'name':'node','uid':str(observer.node_uid)},
            'status':{'nodeInfo':{'bootID':str(observer.boot_id)},
                      'conditions':[{'type':'Ready','status':'True','lastHeartbeatTime':datetime.now(timezone.utc).isoformat()}]}}
    cri = {'pods':{'items':[]}, 'ps':{'containers':[]}}
    observer.cri = lambda command:cri[command[0]]
    return observer, pod, node, cri


def test_matching_node_empty_cri_and_host_proc(evidence):
    observer, pod, node, cri = evidence
    result = observer.verify(pod, get_node=lambda:node, get_pod=lambda:None)
    assert result['podUid'] == str(pod) and result['nodeUid'] == str(observer.node_uid)


@pytest.mark.parametrize('damage', ['pod-exists','node-uid','boot','not-ready','stale','sandbox','container',
                                    'process','unknown-container','missing-proc','local-pidns','bad-cri'])
def test_absence_never_overrides_missing_or_conflicting_evidence(evidence, damage):
    observer, pod, node, cri = evidence
    if damage == 'node-uid': node['metadata']['uid'] = str(uuid4())
    elif damage == 'boot': node['status']['nodeInfo']['bootID'] = str(uuid4())
    elif damage == 'not-ready': node['status']['conditions'][0]['status'] = 'Unknown'
    elif damage == 'stale': node['status']['conditions'][0]['lastHeartbeatTime'] = (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    elif damage == 'sandbox': cri['pods']['items'] = [{'metadata':{'uid':str(pod)},'state':'SANDBOX_READY'}]
    elif damage == 'container': cri['ps']['containers'] = [{'labels':{'io.kubernetes.pod.uid':str(pod)},'state':'CONTAINER_RUNNING'}]
    elif damage == 'process': (observer.proc/'1/cgroup').write_text('0::/kubepods/pod'+str(pod).replace('-','_')+'/x\n')
    elif damage == 'unknown-container': cri['ps']['containers'] = [{'labels':{},'state':'CONTAINER_EXITED'}]
    elif damage == 'missing-proc': (observer.proc/'1/cgroup').unlink()
    elif damage == 'local-pidns':
        (observer.proc/'1/ns/pid').unlink(); (observer.proc/'1/ns/pid').symlink_to('pid:[wrong]')
    elif damage == 'bad-cri': cri['pods'] = {}
    with pytest.raises((Refused, OSError)):
        observer.verify(pod, get_node=lambda:node, get_pod=lambda:{} if damage == 'pod-exists' else None)


def test_final_get_cannot_release_a_reappeared_pod(evidence):
    observer, pod, node, cri = evidence
    results = iter([None, {'metadata':{'uid':str(uuid4())}}])
    with pytest.raises(Refused): observer.verify(pod, get_node=lambda:node, get_pod=lambda:next(results))


def test_cri_query_failure_is_not_empty_inventory(evidence):
    observer, pod, node, cri = evidence
    def failure(command): raise TimeoutError()
    observer.cri = failure
    with pytest.raises(TimeoutError): observer.verify(pod,get_node=lambda:node,get_pod=lambda:None)
