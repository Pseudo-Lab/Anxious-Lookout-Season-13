"""Pure rendering/trust/error checks. No sandbox, socket, Docker or VM execution."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import json

from cryptography import x509
import pytest

from ops import research_dev_fixture as fixture
from ops.native_measure import POD_FIELDS, runner_manifest
from ops.research_dev_probe import internal

IMAGE = 'docker.io/library/issue97-runner@sha256:' + 'a' * 64


def deployment():
    return {'kind': 'Deployment', 'metadata': {'name': 'api', 'namespace': 'm2-hosting', 'uid': 'uid', 'resourceVersion': '11'},
            'spec': {'replicas': 1, 'template': {'spec': {'containers': [{'name': 'api', 'image': IMAGE,
                'envFrom': [{'configMapRef': {'name': 'baseline'}}],
                'env': [{'name': 'AUTH_ORIGIN', 'valueFrom': {'configMapKeyRef': {'name': 'old', 'key': 'origin'}}},
                        {'name': 'RESEARCH_AGENT_API_ENABLE', 'value': 'false'},
                        {'name': 'OTHER', 'value': 'unchanged'}]}]}}}}


def test_tls_chain_only_authenticates_exact_synthetic_name_and_expires_in_one_hour():
    now = datetime.now(timezone.utc)
    bundle, ca_bytes = fixture.bundle(now)
    secret, middleware, route = bundle['items']
    ca = x509.load_pem_x509_certificate(ca_bytes)
    leaf = x509.load_pem_x509_certificate(base64.b64decode(secret['data']['tls.crt']))
    leaf.verify_directly_issued_by(ca)
    assert leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName) == [fixture.HOST]
    assert now + timedelta(minutes=59) < leaf.not_valid_after_utc <= now + timedelta(hours=1)
    assert not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert 'PRIVATE KEY' not in ca_bytes.decode()
    assert all(not flag for flag in middleware['spec']['encodedCharacters'].values())
    match = route['spec']['routes'][0]
    assert 'Host(`research97.dev.invalid`)' in match['match']
    assert '!(Path(`/api/internal`) || PathPrefix(`/api/internal/`))' in match['match']
    assert match['services'] == [{'name': 'api', 'port': 8080}]
    assert match['middlewares'] == [{'name': fixture.NAME}]
    assert route['spec']['entryPoints'] == ['websecure']


def test_origin_change_and_restore_preserve_image_flags_secret_references_and_every_other_spec_field():
    original = deployment()
    patch = fixture.origin_patch(original)
    current = deepcopy(original)
    current['spec']['template']['spec']['containers'][0]['env'] = patch[-1]['value']
    current['metadata']['resourceVersion'] = '12'
    assert current['spec']['template']['spec']['containers'][0]['env'][:-1] == original['spec']['template']['spec']['containers'][0]['env'][1:]
    restore = fixture.origin_restore(current, original)
    current['spec']['template']['spec']['containers'][0]['env'] = restore[-1]['value']
    assert current['spec'] == original['spec']
    assert restore[:2] == [{'op': 'test', 'path': '/metadata/uid', 'value': 'uid'},
                           {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '12'}]
    assert original == deployment()


def test_origin_restore_removes_new_env_when_baseline_had_none():
    original = deployment()
    del original['spec']['template']['spec']['containers'][0]['env']
    current = deepcopy(original)
    current['spec']['template']['spec']['containers'][0]['env'] = fixture.origin_patch(original)[-1]['value']
    assert fixture.origin_restore(current, original)[-1] == {
        'op': 'remove', 'path': '/spec/template/spec/containers/0/env'}


@pytest.mark.parametrize('damage', ['uid', 'image', 'flag', 'replicas'])
def test_restore_refuses_concurrent_replacement_or_unreviewed_changes(damage):
    original = deployment()
    current = deepcopy(original)
    container = current['spec']['template']['spec']['containers'][0]
    container['env'] = fixture.origin_patch(original)[-1]['value']
    if damage == 'uid': current['metadata']['uid'] = 'different'
    if damage == 'image': container['image'] = 'unexpected'
    if damage == 'flag': container['env'][0]['value'] = 'true'
    if damage == 'replicas': current['spec']['replicas'] = 2
    with pytest.raises(ValueError): fixture.origin_restore(current, original)


def test_measurement_uses_runner_security_without_pvc_credentials_or_service():
    pod = fixture.measurement(IMAGE)
    runner = runner_manifest(IMAGE)
    for name in POD_FIELDS:
        assert pod['spec'][name] == runner['spec'][name]
    container = pod['spec']['containers'][0]
    assert container['securityContext'] == runner['spec']['containers'][0]['securityContext']
    assert container['resources'] == runner['spec']['containers'][0]['resources']
    assert pod['spec']['hostAliases'] == [{'ip': fixture.PEER, 'hostnames': [fixture.HOST, 'wrong97.dev.invalid']}]
    assert 'env' not in container and 'envFrom' not in container
    assert all('emptyDir' in volume for volume in pod['spec']['volumes'])
    assert not ({'anxious.io/owner', 'anxious.io/state', 'anxious.io/generation'} & pod['metadata']['labels'].keys())


def test_tag_or_malformed_digest_cannot_select_measurement_image():
    for image in ['runner:latest', 'runner@sha256:short', IMAGE + '\n']:
        with pytest.raises(ValueError): fixture.measurement(image)


@pytest.mark.parametrize('damage', ['uid', 'state', 'control', 'broker'])
def test_inode_probe_refuses_existing_or_credentialed_layout_before_mutation(tmp_path, monkeypatch, damage):
    from ops import research_dev_probe as probe
    (tmp_path/'state').mkdir()
    (tmp_path/'control').mkdir()
    monkeypatch.setattr(probe, 'Path', lambda value: tmp_path/str(value).lstrip('/'))
    monkeypatch.setattr(probe.os, 'getuid', lambda: 10002 if damage == 'uid' else 10001)
    if damage == 'broker': (tmp_path/'run/codex-broker').mkdir(parents=True)
    elif damage in {'state','control'}: (tmp_path/damage/'preserve').write_text('unchanged')
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*'))
    with pytest.raises(ValueError): probe.fresh_layout()
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*')) == before
    for path in tmp_path.rglob('preserve'): assert path.read_text() == 'unchanged'
    compile(probe.INNER, '<sandbox-probe>', 'exec')


def test_internal_wrong_purpose_probe_uses_fixed_services_never_real_auth_or_response_body(monkeypatch, capsys):
    requests = []
    class Connection:
        def __init__(self, host, port, timeout):
            requests.append({'host': host, 'port': port})
        def request(self, method, path, body, headers):
            requests[-1].update(method=method, path=path, body=json.loads(body), headers=headers)
        def getresponse(self):
            value = requests[-1]
            self.status = 401 if 'Authorization' not in value['headers'] else 403 if value['port'] == 8080 else 404
            return self
        def read(self, size): return b'PRIVATE-BODY'
        def close(self): pass
    monkeypatch.setattr('ops.research_dev_probe.http.client.HTTPConnection', Connection)
    assert internal()
    assert len(requests) == 6 and {value['port'] for value in requests} == {8080, 8081}
    assert {value['path'] for value in requests} == {'/v1/access', '/api/internal/research/tools'}
    assert all(not value['body'].get('refresh') and not value['body'].get('attest') for value in requests)
    assert 'PRIVATE-BODY' not in capsys.readouterr().out


@pytest.mark.parametrize('origin,base', [
    ('http://example.invalid', ''), ('https://user:secret@example.invalid', ''),
    ('https://example.invalid:443', ''), ('https://example.invalid:8080', ''),
    ('https://example.invalid/path', ''), ('https://example.invalid?secret=x', ''),
    ('https://example.invalid\n', ''), ('https://example.invalid#', ''),
    ('https://example.invalid', '/../../internal'), ('https://example.invalid', '/api?x=y'),
])
def test_production_preflight_refuses_destination_changes_before_network(origin, base):
    from ops.research_https_preflight import destination
    with pytest.raises(ValueError): destination(origin, base)


def test_production_preflight_uses_verified_system_ca_no_credentials_or_retry(monkeypatch):
    from ops.research_https_preflight import probe, destination
    from types import SimpleNamespace
    assert destination('https://example.invalid', '/base') == ('example.invalid', '/base/api/health')
    calls = []
    class Connection:
        def __init__(self, host, port, timeout, context):
            assert host == 'example.invalid' and port == 443 and timeout == 10
            assert context.check_hostname and context.verify_mode == 2
            self.sock = SimpleNamespace(getpeername=lambda: ('203.0.113.1',443), getpeercert=lambda **kwargs: b'public cert')
        def connect(self): pass
        def request(self, *args, **kwargs): calls.append((args,kwargs))
        def getresponse(self): return SimpleNamespace(status=200, read=lambda size: b'{"status":"ok"}')
        def close(self): pass
    monkeypatch.setenv('SSL_CERT_FILE', '/must-not-use')
    monkeypatch.setenv('HTTPS_PROXY', 'http://must-not-use')
    monkeypatch.setattr('ops.research_https_preflight.http.client.HTTPSConnection', Connection)
    result = probe('https://example.invalid', '/base')
    assert result['tls_verified'] and result['ready'] and result['http'] == 200
    assert calls == [(('GET', '/base/api/health'), {'headers': {'Accept': 'application/json'}})]
