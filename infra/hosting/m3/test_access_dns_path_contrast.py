import copy
import json
import signal
import time

import pytest

from access_dns_path_contrast import ContrastDeadline, contrast, main, path_context
from access_resolver_map import expected_context, summary
from access_diagnose_prepare import emit, DIAGNOSTIC_IMAGE
from test_access_resolver_map import dns_metadata
from test_access_web_prepare import package


def metadata(node='selected-node'):
    data = dns_metadata(node)
    data['slice'] = {'kind': 'EndpointSlice', 'metadata': {'namespace': 'kube-system', 'name': 'known-slice',
         'uid': 'slice-uid', 'resourceVersion': '3', 'labels': {'kubernetes.io/service-name': 'kube-dns'},
         'ownerReferences': [{'kind': 'Service', 'name': 'kube-dns', 'uid': 'service-uid', 'controller': True}]},
         'addressType': 'IPv4', 'ports': [{'port': 53, 'protocol': p} for p in ('UDP', 'TCP')],
         'endpoints': [{'nodeName': node, 'addresses': ['10.42.0.10'], 'conditions': {'ready': True},
                       'targetRef': {'kind': 'Pod', 'namespace': 'kube-system', 'name': 'known-dns', 'uid': 'dns-uid'}}]}
    return data


def reply(status='reply'):
    return {'status': status, 'rcode': 0 if status == 'reply' else None, 'answersPresent': status == 'reply'}


def test_only_four_exact_provider_queries_in_fixed_order_and_no_address_output():
    expected = path_context(metadata(), 'selected-node', expected_context)
    calls = []
    def probe(name, address, *, tcp):
        calls.append((name, address, tcp))
        return reply('timeout' if address == '10.43.0.10' else 'reply')
    result = contrast('nameserver 10.43.0.10\n', expected, probe, summary)
    assert calls == [('chatgpt.com', address, tcp) for address in ('10.43.0.10', '10.42.0.10') for tcp in (False, True)]
    assert result['configuredService']['udp']['status'] == 'timeout'
    assert result['knownCoreDnsPod']['tcp']['status'] == 'reply'
    assert result['dnsCause'] == 'unknown' and result['queryCount'] == 4
    assert all(v not in json.dumps(result) for v in ('chatgpt.com', '10.43.0.10', '10.42.0.10'))


@pytest.mark.parametrize('text', ['nameserver 169.254.20.10\n', 'nameserver 10.43.0.10\nnameserver 10.43.0.10\n'])
def test_runtime_mapping_drift_aborts_before_any_query(text):
    calls = []
    with pytest.raises(ValueError):
        contrast(text, path_context(metadata(), 'selected-node', expected_context), lambda *a, **k: calls.append(a), summary)
    assert calls == []


@pytest.mark.parametrize('change', ['owner', 'pod_uid', 'pod_name', 'ready_bool', 'terminating', 'node', 'address', 'port', 'duplicate'])
def test_foreign_or_unready_service_backend_is_refused(change):
    data = metadata(); s = data['slice']; e = s['endpoints'][0]
    if change == 'owner': s['metadata']['ownerReferences'][0]['uid'] = 'foreign'
    if change == 'pod_uid': e['targetRef']['uid'] = 'foreign'
    if change == 'pod_name': e['targetRef']['name'] = 'foreign'
    if change == 'ready_bool': e['conditions']['ready'] = 1
    if change == 'terminating': e['conditions']['terminating'] = True
    if change == 'node': e['nodeName'] = 'foreign'
    if change == 'address': e['addresses'] = ['10.42.0.11']
    if change == 'port': s['ports'][0]['port'] = 5353
    if change == 'duplicate': s['endpoints'].append(copy.deepcopy(e))
    with pytest.raises(ValueError): path_context(data, 'selected-node', expected_context)


def test_slow_query_deadline_escapes_exception_handler_and_discards_partial(capsys, monkeypatch):
    import access_dns_path_contrast as module
    alarms = []
    monkeypatch.setattr(module.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(module.signal, 'alarm', alarms.append)
    def slow(*_, **__):
        try:
            raise ContrastDeadline()
        except Exception:
            return reply('failed')
    with pytest.raises(SystemExit):
        main(path_context(metadata(), 'selected-node', expected_context), lambda: 'nameserver 10.43.0.10\n', slow, summary)
    assert alarms == [12, 0]
    assert json.loads(capsys.readouterr().out) == {'dnsPathContrast': 'unknown', 'networkRequested': 'unknown'}


def test_actual_twelve_second_alarm_stops_stalled_query(capsys):
    prior = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    def stalled(*_, **__):
        try:
            time.sleep(30)
        except Exception:
            return reply('failed')
    try:
        with pytest.raises(SystemExit):
            main(path_context(metadata(), 'selected-node', expected_context), lambda: 'nameserver 10.43.0.10\n', stalled, summary)
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, prior)
    assert 11 <= time.monotonic()-started < 15
    assert json.loads(capsys.readouterr().out) == {'dnsPathContrast': 'unknown', 'networkRequested': 'unknown'}


@pytest.mark.parametrize('bad', [dict(reply(), rcode=True), dict(reply(), raw='PRIVATE_SENTINEL'),
                               dict(reply('timeout'), answersPresent=True)])
def test_unsupported_probe_output_cannot_be_forwarded(bad):
    with pytest.raises(ValueError):
        contrast('nameserver 10.43.0.10\n', path_context(metadata(), 'selected-node', expected_context), lambda *a, **k: bad, summary)


def test_renderer_reuses_reviewed_wire_helpers_without_running_old_main(package):
    import access_web_prepare as web
    snapshot, inputs, _ = package
    snapshot['runner'] = {'metadata': {'namespace': 'codex-trial', 'name': 'access-web-runner-v1'}, 'spec': {'replicas': 0}}
    snapshot['runnerPodCount'] = 0
    data = metadata(web.base.storage(snapshot))
    job = emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'dns-path-contrast', dns_inputs=data)
    spec = job['spec']['template']['spec']; c = spec['containers'][0]
    assert spec['volumes'] == c['env'] == c['volumeMounts'] == []
    assert not spec['automountServiceAccountToken'] and job['spec']['activeDeadlineSeconds'] == 30
    assert 'dnsConfig' not in spec and 'dnsPolicy' not in spec
    namespace = {'__name__': 'fixture'}
    exec(c['command'][3], namespace)
    assert namespace['TARGETS'] == ('chatgpt.com', 'api.m2-hosting.svc.cluster.local')
    calls = []
    result = namespace['contrast']('nameserver 10.43.0.10\n', namespace['EXPECTED_DNS_CONTEXT'],
        lambda name, address, *, tcp: calls.append((name, address, tcp)) or reply(), namespace['summary'])
    assert len(calls) == 4 and result['dnsCause'] == 'unknown'
    assert 'time.sleep' not in c['command'][3]
