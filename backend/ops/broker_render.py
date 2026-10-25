"""Offline codex-broker manifest renderer (#60). No cluster, credential or Docker calls.

values: {"image": "<runner image>@sha256:...", "accountId": "<operator account id>",
         "storageClass": "<class>", "accessMode": "ReadWriteOncePod" | "ReadWriteOnce",
         "egress": "fqdn" (default) | "world"}
The account id is the ChatGPT workspace id the runners already expect, not a secret.
"""
import argparse
import json
from pathlib import Path
import re

NS = 'codex-broker'
RUNNER_NS = 'codex-runner'
LABELS = {'app': 'codex-broker'}
IMAGE = r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}'


def require(ok):
    if not ok:
        raise ValueError('Broker values refused')


def checked(v):
    require(type(v) is dict and {'image', 'accountId', 'storageClass', 'accessMode'} <= set(v)
            <= {'image', 'accountId', 'storageClass', 'accessMode', 'egress'})
    require(isinstance(v['image'], str) and re.fullmatch(IMAGE, v['image']))
    require(isinstance(v['accountId'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', v['accountId']))
    require(isinstance(v['storageClass'], str) and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', v['storageClass']))
    # ReadWriteOncePod needs a CSI class (#63); ReadWriteOnce with replicas 1 until then.
    require(v['accessMode'] in {'ReadWriteOncePod', 'ReadWriteOnce'})
    # fqdn (#60 acceptance): auth.openai.com and chatgpt.com only, which needs Cilium's DNS proxy
    # (l7Proxy: true). world is an explicit, weaker opt-in for clusters without it: any internet host on 443.
    require(v.get('egress', 'fqdn') in {'fqdn', 'world'})
    return v


def rbac(kind, name, namespace, rules):
    meta = {'name': name, **({'namespace': namespace} if namespace else {})}
    subject = [{'kind': 'ServiceAccount', 'name': 'codex-broker', 'namespace': NS}]
    return [{'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': kind, 'metadata': meta, 'rules': rules},
            {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': kind+'Binding', 'metadata': meta, 'subjects': subject,
             'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': kind, 'name': name}}]


def policy(egress='fqdn'):
    port = lambda number, protocol='TCP': {'port': str(number), 'protocol': protocol}
    kube_dns = [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'kube-system', 'k8s:k8s-app': 'kube-dns'}}]
    if egress == 'fqdn':
        # DNS through the Cilium proxy so toFQDNs can learn the provider addresses.
        dns = {'toEndpoints': kube_dns, 'toPorts': [{'ports': [port(53, 'UDP'), port(53)], 'rules': {'dns': [{'matchPattern': '*'}]}}]}
        internet = {'toFQDNs': [{'matchName': 'auth.openai.com'}, {'matchName': 'chatgpt.com'}], 'toPorts': [{'ports': [port(443)]}]}
    else:
        dns = {'toEndpoints': kube_dns, 'toPorts': [{'ports': [port(53, 'UDP'), port(53)]}]}
        internet = {'toEntities': ['world'], 'toPorts': [{'ports': [port(443)]}]}
    return {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
            'metadata': {'name': 'codex-broker', 'namespace': NS},
            'spec': {'endpointSelector': {'matchLabels': LABELS},
                     'ingress': [{'fromEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': RUNNER_NS,
                                                                     'k8s:anxious.io/managed': 'user-runner'}}],
                                  'toPorts': [{'ports': [port(8080)]}]},
                                 # allow-localhost=policy: kubelet probes come from the host entity.
                                 {'fromEntities': ['host'], 'toPorts': [{'ports': [port(8080)]}]}],
                     'egress': [dns, {'toEntities': ['kube-apiserver'], 'toPorts': [{'ports': [port(6443)]}]}, internet]}}


def resources(v):
    checked(v)
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    probe = {'httpGet': {'path': '/healthz', 'port': 8080}, 'periodSeconds': 30, 'timeoutSeconds': 3}
    items = [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS, 'labels': {
            'pod-security.kubernetes.io/enforce': 'restricted', 'pod-security.kubernetes.io/enforce-version': 'latest'}}},
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': 'codex-broker', 'namespace': NS}},
        *rbac('ClusterRole', 'codex-broker-tokenreview', None,
              [{'apiGroups': ['authentication.k8s.io'], 'resources': ['tokenreviews'], 'verbs': ['create']}]),
        *rbac('Role', 'codex-broker-runner-pods', RUNNER_NS, [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get']}]),
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'codex-broker', 'namespace': NS, 'labels': LABELS},
         'spec': {'selector': LABELS, 'ports': [{'name': 'http', 'port': 8080, 'targetPort': 8080, 'protocol': 'TCP'}]}},
        {'apiVersion': 'apps/v1', 'kind': 'StatefulSet', 'metadata': {'name': 'codex-broker', 'namespace': NS, 'labels': LABELS},
         'spec': {'serviceName': 'codex-broker', 'replicas': 1, 'selector': {'matchLabels': LABELS},
                  # A single writer: refresh tokens rotate on every use.
                  'podManagementPolicy': 'OrderedReady', 'updateStrategy': {'type': 'RollingUpdate'},
                  'template': {'metadata': {'labels': LABELS}, 'spec': {
                      'serviceAccountName': 'codex-broker', 'enableServiceLinks': False,
                      'terminationGracePeriodSeconds': 20,
                      # fsGroup gives the empty volume root to 10001 once (OnRootMismatch); the
                      # broker keeps CODEX_HOME as a 0700 subdirectory so auth.json stays 0600.
                      'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'runAsNonRoot': True, 'fsGroup': 10001,
                                          'fsGroupChangePolicy': 'OnRootMismatch', 'seccompProfile': {'type': 'RuntimeDefault'}},
                      'containers': [{'name': 'broker', 'image': v['image'], 'imagePullPolicy': 'IfNotPresent',
                          'command': ['uvicorn', 'broker.app:create_app', '--factory', '--host', '0.0.0.0', '--port', '8080',
                                      '--workers', '1', '--no-access-log', '--no-proxy-headers'],
                          'env': [{'name': 'BROKER_ACCOUNT_ID', 'value': v['accountId']},
                                  {'name': 'BROKER_RUNNER_NAMESPACE', 'value': RUNNER_NS},
                                  {'name': 'CODEX_HOME', 'value': '/data/codex'}, {'name': 'HOME', 'value': '/data/codex'}],
                          'ports': [{'name': 'http', 'containerPort': 8080, 'protocol': 'TCP'}],
                          'readinessProbe': probe, 'livenessProbe': probe, 'securityContext': security,
                          'volumeMounts': [{'name': 'data', 'mountPath': '/data'}, {'name': 'tmp', 'mountPath': '/tmp'}]}],
                      'volumes': [{'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '64Mi'}}]}},
                  'volumeClaimTemplates': [{'metadata': {'name': 'data'}, 'spec': {
                      'accessModes': [v['accessMode']], 'storageClassName': v['storageClass'],
                      'resources': {'requests': {'storage': '1Gi'}}}}]}},
        policy(v.get('egress', 'fqdn'))]
    return {'apiVersion': 'v1', 'kind': 'List', 'items': items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--values', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(resources(json.loads(args.values.read_text())), indent=2))
    except Exception:
        parser.exit(1, 'Broker render refused; values are not printed\n')


if __name__ == '__main__':
    main()
