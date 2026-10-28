"""#60 codex-broker manifests: namespace, RBAC, StatefulSet, Service and Cilium policy."""
import pytest
from ops.broker_render import resources

VALUES = {'image': 'registry.invalid/runner@sha256:'+'a'*64, 'accountId': 'synthetic-account',
          'storageClass': 'openebs-lvm-ext4', 'accessMode': 'ReadWriteOncePod'}


def lookup(**changes):
    return {(item['kind'], item['metadata']['name']): item for item in resources({**VALUES, **changes})['items']}


def test_rbac_is_tokenreview_and_runner_pod_get_only():
    objects = lookup()
    assert objects['ClusterRole', 'codex-broker-tokenreview']['rules'] == [
        {'apiGroups': ['authentication.k8s.io'], 'resources': ['tokenreviews'], 'verbs': ['create']}]
    role = objects['Role', 'codex-broker-runner-pods']
    assert role['metadata']['namespace'] == 'codex-runner'
    assert role['rules'] == [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get']}]
    for kind in ('ClusterRoleBinding', 'RoleBinding'):
        binding = next(o for (k, _), o in objects.items() if k == kind)
        assert binding['subjects'] == [{'kind': 'ServiceAccount', 'name': 'codex-broker', 'namespace': 'codex-broker'}]
    assert sum(1 for kind, _ in objects if kind in {'Role', 'ClusterRole'}) == 2


def test_single_writer_statefulset_restricted_and_private_home():
    objects = lookup()
    assert objects['Namespace', 'codex-broker']['metadata']['labels']['pod-security.kubernetes.io/enforce'] == 'restricted'
    sts = objects['StatefulSet', 'codex-broker']['spec']
    pod = sts['template']['spec']
    container, = pod['containers']
    assert sts['replicas'] == 1 and sts['volumeClaimTemplates'][0]['spec']['accessModes'] == ['ReadWriteOncePod']
    assert pod['securityContext']['runAsUser'] == 10001 and pod['securityContext']['runAsNonRoot'] is True
    assert container['securityContext'] == {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                            'capabilities': {'drop': ['ALL']}}
    assert 'resources' not in container
    env = {e['name']: e['value'] for e in container['env']}
    assert env['CODEX_HOME'] == '/data/codex' and env['BROKER_ACCOUNT_ID'] == 'synthetic-account'
    assert container['command'][:2] == ['uvicorn', 'broker.app:create_app']


PORT_8080 = [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]
API = {'toEntities': ['kube-apiserver'], 'toPorts': [{'ports': [{'port': '6443', 'protocol': 'TCP'}]}]}


def test_policy_admits_runners_and_kubelet_probes_only():
    spec = lookup()['CiliumNetworkPolicy', 'codex-broker']['spec']
    assert spec['ingress'] == [
        {'fromEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'codex-runner',
                                            'k8s:anxious.io/managed': 'user-runner'}}], 'toPorts': PORT_8080},
        # allow-localhost=policy: without the host entity the kubelet probes would be dropped.
        {'fromEntities': ['host'], 'toPorts': PORT_8080}]


def test_default_egress_is_world_443_without_the_dns_proxy():
    egress = lookup()['CiliumNetworkPolicy', 'codex-broker']['spec']['egress']
    # No rules.dns: the broker's lookups stay off the Cilium DNS proxy path (#60 decision, #70).
    assert egress[0] == {'toEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'kube-system',
                                                          'k8s:k8s-app': 'kube-dns'}}],
                         'toPorts': [{'ports': [{'port': '53', 'protocol': 'UDP'}, {'port': '53', 'protocol': 'TCP'}]}]}
    assert egress[1:] == [API, {'toEntities': ['world'], 'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}]}]}]
    assert not any('toFQDNs' in rule for rule in egress)
    assert lookup(egress='world') == lookup()


def test_fqdn_egress_is_only_an_explicit_switch():
    egress = lookup(egress='fqdn')['CiliumNetworkPolicy', 'codex-broker']['spec']['egress']
    assert egress[0]['toPorts'][0]['rules'] == {'dns': [{'matchPattern': '*'}]}
    assert egress[1:] == [API, {'toFQDNs': [{'matchName': 'auth.openai.com'}, {'matchName': 'chatgpt.com'}],
                                'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}]}]}]


@pytest.mark.parametrize('key,value', [('image', 'runner:latest'), ('accountId', 'has space'),
                                       ('storageClass', 'UPPER'), ('accessMode', 'ReadWriteMany'), ('egress', 'all'), ('extra', 1)])
def test_values_fail_closed(key, value):
    with pytest.raises(ValueError):
        resources({**VALUES, key: value})
