"""Offline fresh endpoint binding for one DNS allocated-port inspection."""
import argparse
import json
import os
from pathlib import Path

from access_endpoint_pin import verify


def scope(saved, pod, cep, agent, response):
    verify(saved, pod, cep, agent, response)
    if agent['spec'].get('hostNetwork') is not True or agent['spec'].get('shareProcessNamespace', False) is not False:
        raise ValueError()
    if agent['spec'].get('hostPID', False) is not False:
        raise ValueError()
    if not pod['metadata']['name'].startswith('access-dns-proxy-listener-diagnose-v1-'):
        raise ValueError()
    value = response[0] if isinstance(response, list) and len(response) == 1 else response
    policy = value['status']['policy']
    revisions = [policy['desired']['policy-revision'], policy['realized']['policy-revision'], policy['proxy-policy-revision']]
    if any(type(v) is not int for v in revisions) or len(set(revisions)) != 1:
        raise ValueError()
    stats = [v for v in policy['proxy-statistics'] if v.get('protocol') == 'dns' and v.get('location') == 'egress' and v.get('port') == 53]
    if len(stats) != 2:
        raise ValueError()
    ports = [v['allocated-proxy-port'] for v in stats]
    if any(type(v) is not int or not 1 <= v <= 65535 for v in ports) or len(set(ports)) != 1:
        raise ValueError()
    return {'targetPin': saved, 'allocatedPort': ports[0], 'policyRevision': revisions[0]}


def finalize(saved, pod, cep, agent, response, result, transport_exit, child, proof):
    if transport_exit != 0 or scope(saved['targetPin'], pod, cep, agent, response) != saved:
        raise ValueError()
    if set(child) != {'pid', 'parentPid', 'group', 'startTicks'} or any(type(v) is not int or not 1 <= v <= 2**63-1 for v in child.values()):
        raise ValueError()
    if min(child['pid'], child['parentPid'], child['group']) <= 1 or child['parentPid'] != child['group'] or child['pid'] == child['group']:
        raise ValueError()
    if proof.get('childGone') is not True or proof.get('groupGone') is not True or proof != dict(child, childGone=True, groupGone=True):
        raise ValueError()
    if set(result) != {'agentProcessMatched', 'tcp4', 'udp4', 'tcp6', 'udp6', 'dnsCause'} or result['agentProcessMatched'] is not True or result['dnsCause'] != 'unknown':
        raise ValueError()
    for name in ('tcp4', 'udp4', 'tcp6', 'udp6'):
        value = result[name]
        if set(value) != {'selectedBoundSockets', 'ownedByAgent', 'wildcardBinds'}:
            raise ValueError()
        if any(type(v) is not int or not 0 <= v <= 4096 for v in value.values()):
            raise ValueError()
        if value['ownedByAgent'] > value['selectedBoundSockets'] or value['wildcardBinds'] > value['selectedBoundSockets']:
            raise ValueError()
    return dict(result, selectedPortContextMatches=True, trafficConclusion='unknown')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pin', required=True); parser.add_argument('--pod', required=True)
    parser.add_argument('--cep', required=True); parser.add_argument('--agent', required=True)
    parser.add_argument('--endpoint', required=True); parser.add_argument('--scope', required=True)
    parser.add_argument('--result'); parser.add_argument('--transport-exit-code', type=int)
    parser.add_argument('--child'); parser.add_argument('--proof')
    args = parser.parse_args()
    try:
        values = [json.loads(Path(p).read_text()) for p in (args.pin, args.pod, args.cep, args.agent, args.endpoint)]
        if args.result:
            # The supplied pin must also match the scope's pin, not an unrelated file.
            saved = json.loads(Path(args.scope).read_text())
            if saved['targetPin'] != values[0]:
                raise ValueError()
            child = json.loads(Path(args.child).read_text())['listenerChild']
            proof = json.loads(Path(args.proof).read_text())
            result = finalize(saved, *values[1:], json.loads(Path(args.result).read_text()), args.transport_exit_code, child, proof)
        else:
            saved = scope(*values)
            with os.fdopen(os.open(args.scope, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600), 'w') as target:
                json.dump(saved, target)
            result = {'listenerScopeCreated': True}
        print(json.dumps(result))
    except Exception:
        parser.exit(1, 'DNS listener metadata unknown/refused; private identifiers are not printed\n')
