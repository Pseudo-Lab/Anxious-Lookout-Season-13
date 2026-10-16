"""Render #13 RBAC, modifications to existing CNPs, quota and API patch offline.

Output needs review before operational application. Creates no additional VAP/CNP
layer: reuse runner-callback-draft and hosting-access-web-link-v1 names, retain
existing trial allow destinations supplied by the operator.
"""
import argparse
import json
import re
from pathlib import Path
from uuid import UUID

from app.runner_kube import name
from runner.lifecycle import require
from runner.auth import read_private


def render(config, api_image, existing_link):
    ns = name(config['namespace'])
    owner = UUID(config['plan']['owner'])
    pod = 'runner-'+owner.hex
    api = {'k8s:io.kubernetes.pod.namespace': 'm2-hosting', 'k8s:app': 'hosting-api'}
    runner = {'k8s:io.kubernetes.pod.namespace': ns, 'io.cilium.k8s.policy.serviceaccount': 'runner-workload'}
    dns = {'k8s:io.kubernetes.pod.namespace': 'kube-system', 'k8s:k8s-app': 'kube-dns'}
    callback = 'http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools'
    require(config['plan']['callback'] == callback)
    require(re.fullmatch(r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}', api_image) is not None)
    require(existing_link['metadata']['name'] == 'hosting-access-web-link-v1'
            and existing_link['metadata']['namespace'] == 'm2-hosting'
            and existing_link['kind'] == 'CiliumNetworkPolicy')
    import copy
    link = copy.deepcopy(existing_link)
    link['metadata'] = {'name': 'hosting-access-web-link-v1', 'namespace': 'm2-hosting'}
    link.pop('status', None)
    require(link['spec']['endpointSelector']['matchLabels'] == {'app': 'hosting-api'}
            or link['spec']['endpointSelector']['matchLabels'] == {'k8s:app': 'hosting-api'})
    link['spec'].setdefault('egress', []).extend([
        {'toEndpoints': [{'matchLabels': runner}], 'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]},
        {'toEntities': ['kube-apiserver'], 'toPorts': [{'ports': [{'port': '6443', 'protocol': 'TCP'}]}]}])
    # Tool callbacks arrive at the API on the same internal authenticated route
    # as trial. Existing ingress rules/default deny remain; add only this source.
    link['spec'].setdefault('ingress', []).append({'fromEndpoints': [{'matchLabels': runner}],
        'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]})
    role = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role',
        'metadata': {'name': 'runner-web', 'namespace': ns}, 'rules': [
            {'apiGroups': [''], 'resources': ['pods'], 'verbs': ['create']},
            {'apiGroups': [''], 'resources': ['pods'], 'resourceNames': [pod], 'verbs': ['get', 'delete']},
            {'apiGroups': [''], 'resources': ['persistentvolumeclaims'],
             'resourceNames': [config['plan'][k]['name'] for k in ('native','control')], 'verbs': ['get']},
            {'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': [config['plan']['transport']['name']], 'verbs': ['get']}]}
    binding = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding',
        'metadata': {'name': 'runner-web', 'namespace': ns},
        'subjects': [{'kind': 'ServiceAccount', 'name': 'hosting', 'namespace': 'm2-hosting'}],
        'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'runner-web'}}
    cluster = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
        'metadata': {'name': 'runner-web-namespace'}, 'rules': [{'apiGroups': [''], 'resources': ['namespaces'],
            'resourceNames': [ns], 'verbs': ['get']}]}
    clusterbinding = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
        'metadata': {'name': 'runner-web-namespace'}, 'subjects': binding['subjects'],
        'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': 'runner-web-namespace'}}
    boundary = {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
        'metadata': {'name': 'runner-callback-draft', 'namespace': ns}, 'spec': {
            'endpointSelector': {'matchLabels': {'io.cilium.k8s.policy.serviceaccount': 'runner-workload'}},
            'ingress': [{'fromEndpoints': [{'matchLabels': api}],
                         'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]},
                        {'fromEntities': ['host'], 'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]}],
            'egress': [
                {'toEndpoints': [{'matchLabels': api}], 'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]},
                {'toEndpoints': [{'matchLabels': dns}], 'toPorts': [{'ports': [
                    {'port': '53', 'protocol': 'UDP'}, {'port': '53', 'protocol': 'TCP'}],
                    'rules': {'dns': [{'matchName': 'chatgpt.com'}, {'matchName': 'api.m2-hosting.svc.cluster.local'}]}}]},
                {'toFQDNs': [{'matchName': 'chatgpt.com'}],
                 'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}]}]}]}}
    quota = {'apiVersion': 'v1', 'kind': 'ResourceQuota',
             'metadata': {'name': 'runner-bootstrap-no-pods', 'namespace': ns}, 'spec': {'hard': {'count/pods': '1'}}}
    patch = {'spec': {'template': {'spec': {
        'automountServiceAccountToken': False,
        'initContainers': [{'name': 'runner-private-input', 'image': api_image,
            'command': ['python', '-B', '-m', 'ops.user_runner_mount'],
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                'capabilities': {'drop': ['ALL']}},
            'volumeMounts': [{'name': 'runner-input', 'mountPath': '/run/runner-input', 'readOnly': True},
                             {'name': 'runner-private', 'mountPath': '/run/user-runner'}]}],
        'containers': [{'name': 'api', 'image': api_image, 'env': [
            {'name': 'CODEX_USER_RUNNERS_ENABLE', 'value': 'true'},
            {'name': 'CODEX_USER_RUNNER_ACCOUNT_ID', 'value': str(owner)},
            {'name': 'CODEX_USER_RUNNER_MAX_PODS', 'value': '1'},
            {'name': 'CODEX_USER_RUNNER_CONFIG_FILE', 'value': '/run/user-runner/private/runtime.json'}],
            'volumeMounts': [{'name': 'runner-private', 'mountPath': '/run/user-runner', 'readOnly': True},
                             {'name': 'runner-kube', 'mountPath': '/run/runner-kube', 'readOnly': True}]}],
        'volumes': [
            {'name': 'runner-input', 'secret': {'secretName': 'hosting-user-runner-v1', 'defaultMode': 288}},
            {'name': 'runner-private', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '1Mi'}},
            {'name': 'runner-kube', 'projected': {'defaultMode': 288, 'sources': [
                {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 3600}},
                {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}}]}}}}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [role, binding, cluster, clusterbinding, boundary, link, quota]}, patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--api-image', required=True)
    parser.add_argument('--existing-link', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        resources, patch = render(read_private(Path(args.config)), args.api_image,
                                  json.loads(Path(args.existing_link).read_text()))
        output = Path(args.output); output.mkdir(mode=0o700)
        for filename, value in [('resources.json', resources), ('api-patch.json', patch)]:
            target = output/filename
            target.write_text(json.dumps(value, indent=2)+'\n'); target.chmod(0o600)
    except Exception:
        parser.exit(1, 'Deployment render refused; no operational changes applied\n')
    print('PASS review candidates written; no operational changes applied')


if __name__ == '__main__':
    main()
