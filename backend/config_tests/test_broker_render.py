"""#60 codex-broker manifests: namespace, RBAC, StatefulSet, Service and Cilium policy."""
import pytest
from ops.broker_render import resources

VALUES = {'image': 'registry.invalid/runner@sha256:'+'a'*64, 'accountId': 'synthetic-account',
          'storageClass': 'openebs-lvm-ext4', 'accessMode': 'ReadWriteOncePod'}


def lookup():
    return {(item['kind'], item['metadata']['name']): item for item in resources(dict(VALUES))['items']}


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


def test_policy_admits_runners_and_probes_and_egress_needs_no_l7_proxy():
    spec = lookup()['CiliumNetworkPolicy', 'codex-broker']['spec']
    assert spec['ingress'] == [
        {'fromEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'codex-runner',
                                            'k8s:anxious.io/managed': 'user-runner'}}],
         'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]},
        {'fromEntities': ['host'], 'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]}]
    # l7Proxy is off: no DNS rules or toFQDNs (they would not be enforced); FQDN narrowing is #62.
    assert not any('toFQDNs' in rule or 'rules' in port for rule in spec['egress'] for port in rule.get('toPorts', []))
    assert {'toEntities': ['world'], 'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}]}]} in spec['egress']
    assert {'toEntities': ['kube-apiserver'], 'toPorts': [{'ports': [{'port': '6443', 'protocol': 'TCP'}]}]} in spec['egress']
    assert len(spec['egress']) == 3


@pytest.mark.parametrize('key,value', [('image', 'runner:latest'), ('accountId', 'has space'),
                                       ('storageClass', 'UPPER'), ('accessMode', 'ReadWriteMany'), ('extra', 1)])
def test_values_fail_closed(key, value):
    with pytest.raises(ValueError):
        resources({**VALUES, key: value})
