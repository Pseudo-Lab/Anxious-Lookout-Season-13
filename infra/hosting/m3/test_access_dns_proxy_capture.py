import io
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

from access_drop_capture import capture, finalize
from test_access_drop_capture import frames, setup_values, stream

IDENTITY = 1048576  # Security identity, deliberately different from local endpoint321.


def context():
    values = setup_values()
    values[2]['status']['identity'] = {'id': IDENTITY}
    values[4][0]['status']['identity'] = {'id': IDENTITY}
    return values


def dns(response=False, reversed_endpoint=False):
    client = {'id': 321, 'identity': IDENTITY,
              'labels': ['k8s:app=access-web-runner', 'k8s:io.kubernetes.pod.namespace=codex-trial', 'private-label-sentinel']}
    server = {'id': 0, 'identity': 50000, 'labels': ['private-server-label']}
    src, dst = (server, client) if reversed_endpoint else (client, server)
    return {'type': 'logRecord', 'observationPoint': 'Egress', 'l7Proto': 'dns',
            'flowType': 'Response' if response else 'Request', 'verdict': 'Forwarded',
            'srcEpID': src['id'], 'dstEpID': dst['id'], 'srcIdentity': src['identity'], 'dstIdentity': dst['identity'],
            'srcEpLabels': src['labels'], 'dstEpLabels': dst['labels'],
            'dns': {'Query': 'chatgpt.com.', 'ObservationSource': 'proxy', 'QTypes': [1],
                    'IPs': ['203.0.113.9'] if response else [], 'CNAMEs': ['private-answer-name-sentinel']}}


@pytest.mark.parametrize('reverse', [False, True])
def test_proxy_request_response_metadata_only(reverse):
    values = context()
    begin, end = frames('observe-dns')
    receipt = capture(stream(begin, dns(), dns(True, reverse), end), 'observe-dns', values[0], IDENTITY)
    result = finalize(receipt, dict(receipt['child'], childGone=True, groupGone=True), *values)
    assert result['dnsRequests'] == result['dnsResponses'] == 1 and result['dnsCause'] == 'unknown'
    encoded = json.dumps(receipt)
    for raw in ('chatgpt.com', '203.0.113.9', 'private-label', 'private-server', 'private-answer', 'srcEpLabels'):
        assert raw not in encoded
    assert receipt['events'][1]['rcode'] == 0 and receipt['events'][1]['hasAnswers'] is True


@pytest.mark.parametrize('change', ['endpoint', 'identity', 'local_as_identity', 'namespace', 'http', 'l7', 'query',
                                  'double_dot', 'flow', 'point', 'origin', 'qtype_bool', 'qtype_unknown', 'rcode', 'answer'])
def test_foreign_and_non_dns_record_discards_prior_events(change):
    value = dns()
    if change == 'endpoint': value['srcEpID'] = 322
    if change == 'identity': value['srcIdentity'] = IDENTITY+1
    if change == 'local_as_identity': value['srcIdentity'] = 321
    if change == 'namespace': value['srcEpLabels'] = ['k8s:app=access-web-runner']
    if change == 'http': value['http'] = {'headers': {'authorization': 'raw-secret-sentinel'}}
    if change == 'l7': value['l7'] = {'Fields': {'raw': 'raw-secret-sentinel'}}
    if change == 'query': value['dns']['Query'] = 'foreign.example'
    if change == 'double_dot': value['dns']['Query'] = 'chatgpt.com..'
    if change == 'flow': value['flowType'] = 'Sample'
    if change == 'point': value['observationPoint'] = 'Ingress'
    if change == 'origin': value['dns']['ObservationSource'] = 'standalone-proxy'
    if change == 'qtype_bool': value['dns']['QTypes'] = [True]
    if change == 'qtype_unknown': value['dns']['QTypes'] = [65]
    if change == 'rcode': value['dns']['RCode'] = -1
    if change == 'answer': value['dns']['IPs'] = ['raw-secret-sentinel']
    begin, end = frames('observe-dns')
    receipt = capture(stream(begin, dns(), value, end), 'observe-dns', context()[0], IDENTITY)
    assert not receipt['complete'] and receipt['events'] == []
    assert 'raw-secret-sentinel' not in json.dumps(receipt)


def test_security_identity_drift_after_capture_rejects_adoption():
    values = context()
    begin, end = frames('observe-dns')
    receipt = capture(stream(begin, dns(), end), 'observe-dns', values[0], IDENTITY)
    values[2]['status']['identity']['id'] += 1
    with pytest.raises(ValueError):
        finalize(receipt, dict(receipt['child'], childGone=True, groupGone=True), *values)


def test_empty_dns_capture_is_unknown_and_denied_error_are_distinct():
    values = context()
    begin, end = frames('observe-dns')
    for events in ([], [dict(dns(), verdict='Denied'), dict(dns(True), verdict='Error')]):
        receipt = capture(stream(begin, *events, end), 'observe-dns', values[0], IDENTITY)
        result = finalize(receipt, dict(receipt['child'], childGone=True, groupGone=True), *values)
        assert result['noEventConclusion'] == 'unknown'
        assert result['dnsProxyDenied'] == result['dnsProxyErrors'] == (1 if events else 0)


def test_dns_remote_fixture_uses_only_l7_exact_endpoint_mode(tmp_path):
    fixture = tmp_path/'cilium-dbg'
    fixture.write_text('#!/bin/sh\n'
                       '[ "$#" -eq 7 ] && [ "$1" = monitor ] && [ "$2" = --type ] && [ "$3" = l7 ] && '
                       '[ "$4" = --related-to ] && [ "$5" = 321 ] && [ "$6" = --json ] && [ "$7" = --numeric ] || exit 2\n'
                       'printf "%s\\n" '+shlex.quote(json.dumps(dns(True)))+'\n'
                       'trap \'kill -TERM "$fixture_child" 2>/dev/null; wait "$fixture_child" 2>/dev/null; exit 0\' INT\n'
                       'sleep 30 &\nfixture_child=$!\nwait\n')
    fixture.chmod(0o700)
    script = str(Path(__file__).with_name('access_drop_remote.sh'))
    process = subprocess.run(['sh', script, 'observe-dns', '321'], capture_output=True, timeout=8,
                             env=dict(os.environ, PATH=str(tmp_path)+':'+os.environ['PATH']))
    assert process.returncode == 0
    values = context()
    receipt = capture(io.BytesIO(process.stdout), 'observe-dns', values[0], IDENTITY)
    child = receipt['child']
    proof = subprocess.run(['sh', script, 'check', *[str(child[k]) for k in ('pid', 'parentPid', 'group', 'startTicks')]],
                           capture_output=True, timeout=2)
    assert proof.returncode == 0
    assert finalize(receipt, json.loads(proof.stdout), *values)['dnsResponses'] == 1
