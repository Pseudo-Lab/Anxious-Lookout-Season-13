"""Check the reviewed bootstrap boundary against preparation's exact API footprint."""
import json
from pathlib import Path

ROOT = Path('/infra/hosting/m3/user-runner-e2e')


def objects(filename):
    return json.loads((ROOT/filename).read_text())['items']


def test_foundation_has_only_namespace_deny_and_tokenless_workload():
    ns, policy, sa = objects('00-foundation.json')
    assert [obj['kind'] for obj in (ns, policy, sa)] == ['Namespace', 'NetworkPolicy', 'ServiceAccount']
    assert ns['metadata']['name'] == 'codex-runner'
    for mode in ('enforce', 'audit', 'warn'):
        assert ns['metadata']['labels']['pod-security.kubernetes.io/'+mode] == 'restricted'
        assert ns['metadata']['labels']['pod-security.kubernetes.io/'+mode+'-version'] == 'v1.36'
    assert policy['metadata'] == {'name': 'runner-default-deny', 'namespace': 'codex-runner'}
    assert policy['spec'] == {'podSelector': {}, 'policyTypes': ['Ingress','Egress'], 'ingress': [], 'egress': []}
    assert sa['metadata'] == {'name': 'runner-workload', 'namespace': 'codex-runner'}
    assert sa['automountServiceAccountToken'] is False


def test_preparer_grants_exact_operations_and_never_workload_identity():
    sa, role, binding, cluster, clusterbinding = objects('05-preparer-rbac.json')
    assert sa['automountServiceAccountToken'] is False
    for obj in (binding, clusterbinding):
        assert obj['subjects'] == [{'kind': 'ServiceAccount', 'name': 'runner-preparer', 'namespace': 'codex-runner'}]
    actual = set()
    for scope, obj in [('codex-runner', role), ('cluster', cluster)]:
        for rule in obj['rules']:
            for group in rule['apiGroups']:
                for resource in rule['resources']:
                    for verb in rule['verbs']:
                        actual.add((scope, group, resource, verb, tuple(rule.get('resourceNames', ()))))
    assert actual == {
        ('codex-runner', '', 'persistentvolumeclaims', 'create', ()),
        ('codex-runner', '', 'secrets', 'create', ()),
        ('codex-runner', '', 'serviceaccounts', 'get', ('runner-workload',)),
        ('codex-runner', 'networking.k8s.io', 'networkpolicies', 'get', ('runner-default-deny',)),
        ('cluster', '', 'namespaces', 'get', ('codex-runner',)),
        ('cluster', '', 'persistentvolumes', 'create', ())}
    assert binding['roleRef']['name'] == role['metadata']['name']
    assert clusterbinding['roleRef']['name'] == cluster['metadata']['name']
