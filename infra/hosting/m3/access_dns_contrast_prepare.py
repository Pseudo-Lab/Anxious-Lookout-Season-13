"""Offline diagnostic-only CNP clone, conditional DNS rule transition and cleanup."""
import argparse
import copy
import json
from pathlib import Path

import access_runner_dns_prepare as direct
import access_runner_dns_control_prepare as control
import access_web_prepare as web

POLICY = 'access-runner-dns-contrast-v1'
JOBS = {'l4': 'access-runner-dns-l4-v1', 'l7': 'access-runner-dns-l7-v1'}
RULE_PATH = '/spec/egress/1/toPorts/0/rules'


def identity(obj, kind, name):
    meta = obj['metadata']
    if (obj.get('kind') != kind or meta['namespace'] != 'codex-trial' or meta['name'] != name
            or not isinstance(meta.get('uid'), str) or not meta['uid']
            or not isinstance(meta.get('resourceVersion'), str) or not meta['resourceVersion']
            or meta.get('deletionTimestamp')):
        raise ValueError()
    return {'kind': kind, 'namespace': meta['namespace'], 'name': name, 'uid': meta['uid']}


def policy_pair(snapshot, inputs, images, production, baseline):
    if control.canonical(direct.emit(snapshot, inputs, images)) != control.canonical(baseline):
        raise ValueError()
    identity(production, 'CiliumNetworkPolicy', 'access-web-boundary-v1')
    web.configure(images)
    expected = web.emit(snapshot, inputs, 'policy')['items'][0]['spec']
    if control.canonical(production['spec']) != control.canonical(expected):
        raise ValueError()
    l7 = {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
          'metadata': {'namespace': 'codex-trial', 'name': POLICY}, 'spec': copy.deepcopy(expected)}
    l7['spec']['endpointSelector']['matchLabels']['app'] = control.CONTROL_APP
    l4 = copy.deepcopy(l7)
    del l4['spec']['egress'][1]['toPorts'][0]['rules']
    return {'l4': l4, 'l7': l7}


def normal_replies(result, transport_exit):
    if (type(transport_exit) is not int or transport_exit != 0 or result.get('helperAttempts') != 4
            or type(result.get('helperAttempts')) is not int or result.get('libcQueries') != 0
            or type(result.get('libcQueries')) is not int or result.get('nativeStarted') is not False
            or result.get('sourceCredentialRead') is not False or result.get('httpOrAuthRpc') is not False):
        raise ValueError()
    for target in ('codexBackend', 'callbackService'):
        for protocol in ('absoluteUdp', 'absoluteTcp'):
            value = result[target][protocol]
            if (value != {'status': 'reply', 'rcode': 0, 'answersPresent': True}
                    or type(value['rcode']) is not int or value['answersPresent'] is not True):
                raise ValueError()


def transition(current, pair, owned, result, transport_exit, cleanup):
    if identity(current, 'CiliumNetworkPolicy', POLICY) != owned:
        raise ValueError()
    if control.canonical(current['spec']) != control.canonical(pair['l4']['spec']):
        raise ValueError()
    normal_replies(result, transport_exit)
    # This receipt must be backed by the saved exact Job GET/delete and three Pod lists.
    if (set(cleanup) != {'job', 'podUid', 'nonce', 'jobAbsent', 'ownerPodCount', 'controlPodCount', 'runnerPodCount'}
            or cleanup['job'].get('kind') != 'Job' or cleanup['job'].get('namespace') != 'codex-trial'
            or cleanup['job'].get('name') != JOBS['l4'] or not isinstance(cleanup['job'].get('uid'), str)
            or not cleanup['job']['uid'] or not isinstance(cleanup['podUid'], str) or not cleanup['podUid']
            or not isinstance(cleanup['nonce'], str) or len(cleanup['nonce']) != 32
            or cleanup['jobAbsent'] is not True
            or any(type(cleanup[key]) is not int or cleanup[key] != 0
                   for key in ('ownerPodCount', 'controlPodCount', 'runnerPodCount'))):
        raise ValueError()
    context = result.get('dnsContrastContext')
    if not isinstance(context, dict):
        raise ValueError()
    expected = {'phase': 'l4', 'jobUid': cleanup['job']['uid'], 'podUid': cleanup['podUid'],
                'nonce': cleanup['nonce'], 'policyUid': owned['uid'], 'policyRv': current['metadata']['resourceVersion']}
    if (any(context.get(key) != value for key, value in expected.items())
            or type(context.get('policyRevision')) is not int or context['policyRevision'] <= 0
            or type(context.get('startTicks')) is not int or context['startTicks'] <= 0):
        raise ValueError()
    return [{'op': 'test', 'path': '/metadata/uid', 'value': owned['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/spec', 'value': pair['l4']['spec']},
            {'op': 'add', 'path': RULE_PATH, 'value': pair['l7']['spec']['egress'][1]['toPorts'][0]['rules']}]


def deletion(current, owned):
    kind, name = owned['kind'], owned['name']
    if (kind, name) not in {('CiliumNetworkPolicy', POLICY), *(('Job', name) for name in JOBS.values())}:
        raise ValueError()
    if identity(current, kind, name) != owned:
        raise ValueError()
    return {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Foreground',
            'preconditions': {'uid': owned['uid']}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pair', 'transition', 'delete'))
    for name in ('snapshot', 'inputs', 'images', 'production', 'baseline', 'pair', 'current', 'owned', 'result', 'cleanup'):
        parser.add_argument('--'+name)
    parser.add_argument('--transport-exit', type=int)
    args = parser.parse_args()
    try:
        read = lambda key: json.loads(Path(getattr(args, key)).read_text())
        if args.action == 'pair':
            result = policy_pair(*(read(key) for key in ('snapshot', 'inputs', 'images', 'production', 'baseline')))
        elif args.action == 'transition':
            result = transition(read('current'), read('pair'), read('owned'), read('result'), args.transport_exit, read('cleanup'))
        else:
            result = deletion(read('current'), read('owned'))
        print(json.dumps(result, indent=2))
    except Exception:
        parser.exit(1, 'DNS contrast preparation refused; private metadata is not printed\n')
