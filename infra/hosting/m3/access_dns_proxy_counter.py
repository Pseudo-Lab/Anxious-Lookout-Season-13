"""Offline exact-L7-endpoint notification-counter comparison, never traffic capture."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re

import access_dns_contrast_exec as launch
from access_dns_contrast_prepare import POLICY, identity
from access_dns_contrast_scope import endpoint_one
from access_endpoint_pin import pin
from access_runner_dns_control_prepare import canonical

FIELDS = ('received', 'forwarded', 'denied', 'error')
INT64 = 2**63-1
TOTAL_MAX = 2*INT64


def positive(value, maximum=INT64):
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError()
    return value


def clock(receipt, mode, endpoint_id):
    if (set(receipt) != {'mode', 'endpointId', 'timeNamespace', 'beginUpperCs', 'endUpperCs', 'childExit', 'complete'}
            or receipt['mode'] != mode or type(receipt['endpointId']) is not int or receipt['endpointId'] != endpoint_id
            or type(receipt['childExit']) is not int or receipt['childExit'] != 0 or receipt['complete'] is not True
            or not isinstance(receipt['timeNamespace'], str)
            or not re.fullmatch(r'time:\[[1-9][0-9]*\]', receipt['timeNamespace'])):
        raise ValueError()
    for key in ('beginUpperCs', 'endUpperCs'):
        positive(receipt[key], INT64//10_000_000)
    if receipt['endUpperCs'] < receipt['beginUpperCs']:
        raise ValueError()
    return receipt


def binding(job, pod, cep, agent, diagnostic, receipt, scope, nonce):
    launch.selected(job, pod, 'l7', nonce)
    if (scope.get('phase') != 'l7' or scope.get('realizedRowsMatch') is not True
            or pin(pod, cep, agent) != scope['pin']
            or identity(diagnostic, 'CiliumNetworkPolicy', POLICY)['uid'] != scope['policyUid']
            or diagnostic['metadata']['resourceVersion'] != scope['policyRv']
            or receipt['nonce'] != nonce or receipt['podUid'] != pod['metadata']['uid']
            or receipt['podName'] != pod['metadata']['name'] or receipt.get('holding') is not True):
        raise ValueError()
    positive(scope['allocatedPort'], 65535); positive(scope['policyRevision'])
    positive(receipt['startTicks']); positive(receipt['startNs'])
    endpoint_identity = positive(cep['status']['identity']['id'], 2**32-1)
    containers = agent['status']['containerStatuses']
    if len(containers) != 1 or containers[0]['name'] != 'cilium-agent' or agent['status']['phase'] != 'Running':
        raise ValueError()
    a = containers[0]
    if (type(a['restartCount']) is not int or a['restartCount'] < 0
            or not isinstance(a['state'].get('running'), dict) or not a['state']['running'].get('startedAt')
            or not a.get('containerID') or not a.get('imageID')):
        raise ValueError()
    container = pod['status']['containerStatuses'][0]
    return dict(scope, jobUid=job['metadata']['uid'], nonce=nonce, containerId=container['containerID'],
                restartCount=container['restartCount'], holderStartTicks=receipt['startTicks'], startNs=receipt['startNs'],
                endpointIdentity=endpoint_identity, policySpecHash=hashlib.sha256(canonical(diagnostic['spec']).encode()).hexdigest(),
                agentInstance={'uid': agent['metadata']['uid'], 'containerId': a['containerID'], 'imageId': a['imageID'],
                               'restartCount': a['restartCount'], 'startedAt': a['state']['running']['startedAt']})


def snapshot(response, bound):
    endpoint = endpoint_one(response)
    status = endpoint['status']; policy = status['policy']
    if (type(endpoint['id']) is not int or endpoint['id'] != bound['pin']['endpointId'] or status['state'] != 'ready'
            or type(status['identity']['id']) is not int or status['identity']['id'] != bound['endpointIdentity']
            or status['external-identifiers']['cni-attachment-id'] != bound['pin']['cniAttachmentId']
            or not any(bound['pin']['podIp'] in (row.get('ipv4'), row.get('ipv6')) for row in status['networking']['addressing'])):
        raise ValueError()
    revisions = [policy['spec']['policy-revision'], policy['realized']['policy-revision'], policy['proxy-policy-revision']]
    if any(type(v) is not int or v != bound['policyRevision'] for v in revisions):
        raise ValueError()
    stats = policy['proxy-statistics']
    if not isinstance(stats, list) or len(stats) != 2:
        raise ValueError()
    vectors = []
    for row in stats:
        if (set(row) != {'protocol', 'location', 'port', 'allocated-proxy-port', 'statistics'}
                or row['protocol'] != 'dns' or row['location'] != 'egress'
                or type(row['port']) is not int or row['port'] != 53
                or type(row['allocated-proxy-port']) is not int or row['allocated-proxy-port'] != bound['allocatedPort']
                or not isinstance(row['statistics'], dict) or set(row['statistics']) != {'requests', 'responses'}):
            raise ValueError()
        vector = []
        for direction in ('requests', 'responses'):
            counts = row['statistics'][direction]
            if not isinstance(counts, dict) or counts.keys()-set(FIELDS):
                raise ValueError()
            for name in FIELDS:
                value = counts.get(name, 0)  # Only documented omitempty scalar fields become0.
                if type(value) is not int or not 0 <= value <= INT64:
                    raise ValueError()
                vector.append(value)
        vectors.append(tuple(vector))
    totals = [sum(values) for values in zip(*vectors)]
    if any(not 0 <= value <= TOTAL_MAX for value in totals):
        raise ValueError()
    return vectors, totals


def finalize(before, after, before_bound, after_bound, before_clock, after_clock, result, transport_exit):
    if canonical(before_bound) != canonical(after_bound):
        raise ValueError()
    bound = before_bound
    clock(before_clock, 'before', bound['pin']['endpointId'])
    clock(after_clock, 'after', bound['pin']['endpointId'])
    if (type(transport_exit) is not int or transport_exit != 0 or result.get('helperAttempts') != 4
            or type(result.get('helperAttempts')) is not int or result.get('libcQueries') != 0
            or type(result.get('libcQueries')) is not int or result.get('nativeStarted') is not False
            or result.get('sourceCredentialRead') is not False or result.get('httpOrAuthRpc') is not False):
        raise ValueError()
    for name in ('codexBackend', 'callbackService'):
        for protocol in ('absoluteUdp', 'absoluteTcp'):
            value = result[name][protocol]
            if set(value) != {'status', 'rcode', 'answersPresent'} or type(value['answersPresent']) is not bool:
                raise ValueError()
            if value['status'] == 'reply':
                if type(value['rcode']) is not int or not 0 <= value['rcode'] <= 15: raise ValueError()
            elif value['status'] not in ('timeout', 'failed') or value['rcode'] is not None or value['answersPresent'] is not False:
                raise ValueError()
    context = result['dnsContrastContext']
    expected = {'phase': 'l7', 'jobUid': bound['jobUid'], 'podUid': bound['pin']['podUid'], 'nonce': bound['nonce'],
                'policyUid': bound['policyUid'], 'policyRv': bound['policyRv'], 'policyRevision': bound['policyRevision'],
                'startTicks': bound['holderStartTicks']}
    if canonical(context) != canonical(expected):
        raise ValueError()
    budget = result['counterBudget']
    if (set(budget) != {'timeNamespace', 'startNs', 'enteredNs', 'finishedNs', 'afterCutoffNs'}
            or budget['timeNamespace'] != before_clock['timeNamespace'] or budget['timeNamespace'] != after_clock['timeNamespace']):
        raise ValueError()
    for key in ('startNs', 'enteredNs', 'finishedNs', 'afterCutoffNs'): positive(budget[key])
    start, entered, finished = (budget[key] for key in ('startNs', 'enteredNs', 'finishedNs'))
    if (start != bound['startNs'] or not start <= entered <= finished < start+28_000_000_000
            or entered >= start+18_000_000_000 or budget['afterCutoffNs'] != start+38_000_000_000
            or before_clock['endUpperCs']*10_000_000 > entered
            or (after_clock['beginUpperCs']-1)*10_000_000 < finished
            or after_clock['endUpperCs']*10_000_000 >= budget['afterCutoffNs']):
        raise ValueError()
    before_vectors, before_total = snapshot(before, bound)
    after_vectors, after_total = snapshot(after, bound)
    possibilities = [permutation for permutation in itertools.permutations(after_vectors)
                     if all(all(a >= b for a, b in zip(actual, old)) for actual, old in zip(permutation, before_vectors))]
    if not possibilities:
        raise ValueError()
    delta = [a-b for a, b in zip(after_total, before_total)]
    return {'counterComparison': 'observed', 'delta': {direction: dict(zip(FIELDS, delta[offset:offset+4]))
            for direction, offset in (('requests', 0), ('responses', 4))},
            'entryMappingAmbiguous': len(possibilities) > 1, 'perEntryResetExcluded': False,
            'counterMeaning': 'endpoint_notification_updates', 'proxyNotReceivedConclusion': 'unknown'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('bind', 'compare'))
    for key in ('job', 'pod', 'cep', 'agent', 'diagnostic', 'receipt', 'scope', 'before', 'after', 'before-bound',
                'after-bound', 'before-clock', 'after-clock', 'result'):
        parser.add_argument('--'+key)
    parser.add_argument('--nonce'); parser.add_argument('--transport-exit', type=int)
    args = parser.parse_args()
    try:
        read = lambda key: json.loads(Path(getattr(args, key.replace('-', '_'))).read_text())
        if args.action == 'bind':
            value = binding(*(read(key) for key in ('job', 'pod', 'cep', 'agent', 'diagnostic', 'receipt', 'scope')), args.nonce)
        else:
            value = finalize(*(read(key) for key in ('before', 'after', 'before-bound', 'after-bound', 'before-clock',
                              'after-clock', 'result')), args.transport_exit)
        print(json.dumps(value, indent=2))
    except Exception:
        if args.action == 'compare':
            print('{"counterComparison":"unknown","proxyNotReceivedConclusion":"unknown"}')
        parser.exit(1, 'DNS counter comparison refused; private metadata is not printed\n')
