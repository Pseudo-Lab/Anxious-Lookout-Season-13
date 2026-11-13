"""#62 step E: the runner-subresources VAP and binding, pinned (run with infra mounted at /infra).

CEL evaluation itself (CONNECT matching on k3s v1.36, the system:masters exception) is proven on
the development VM (user-runner-e2e/RUNNER-NETWORK.md step E); this pins what is applied there.
"""
import json
from pathlib import Path

ROOT = Path('/infra/hosting/m3/user-runner-e2e')
NAMESPACE = {'matchLabels': {'kubernetes.io/metadata.name': 'codex-runner'}}


def objects():
    value = json.loads((ROOT/'50-runner-subresources-vap.json').read_text())
    assert value['kind'] == 'List'
    return value['items']


def test_exactly_one_policy_and_its_binding():
    policy, binding = objects()
    assert (policy['kind'], binding['kind']) == ('ValidatingAdmissionPolicy', 'ValidatingAdmissionPolicyBinding')
    assert policy['apiVersion'] == binding['apiVersion'] == 'admissionregistration.k8s.io/v1'
    assert policy['metadata'] == binding['metadata'] == {'name': 'runner-subresources'}
    assert binding['spec'] == {'policyName': 'runner-subresources', 'validationActions': ['Deny'],
                               'matchResources': {'namespaceSelector': NAMESPACE}}


def test_policy_matches_only_the_runner_access_subresources():
    policy, _ = objects()
    spec = policy['spec']
    assert set(spec) == {'failurePolicy', 'matchConstraints', 'validations'}  # no paramKind, variables, audit
    assert spec['failurePolicy'] == 'Fail'
    match = spec['matchConstraints']
    assert set(match) == {'matchPolicy', 'namespaceSelector', 'resourceRules'}
    assert match['matchPolicy'] == 'Equivalent' and match['namespaceSelector'] == NAMESPACE
    assert match['resourceRules'] == [
        {'apiGroups': [''], 'apiVersions': ['v1'], 'operations': ['CONNECT'],
         'resources': ['pods/exec', 'pods/attach', 'pods/portforward'], 'scope': 'Namespaced'},
        {'apiGroups': [''], 'apiVersions': ['v1'], 'operations': ['UPDATE'],
         'resources': ['pods/ephemeralcontainers'], 'scope': 'Namespaced'}]
    # Plain pods (create/delete by runner-controller, get by the API and broker) are never matched.
    assert not any(r for rule in match['resourceRules'] for r in rule['resources'] if '/' not in r)


def test_only_system_masters_passes():
    policy, _ = objects()
    validation, = policy['spec']['validations']
    assert validation['expression'] == "has(request.userInfo.groups) && 'system:masters' in request.userInfo.groups"
    assert validation['reason'] == 'Forbidden' and validation['message'].startswith('runner-subresources: ')
    # CONNECT requests carry no object; the expression must not depend on one.
    assert 'object' not in validation['expression'].replace('request.userInfo', '')
