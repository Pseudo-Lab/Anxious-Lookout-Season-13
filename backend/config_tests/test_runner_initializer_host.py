"""Run the real shell guard with mocked host observations and real jq, in Docker."""
import json
import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path('/workspace/infra/hosting/m3/user-runner-isolated/initialize-rehearsal.sh')


@pytest.mark.parametrize('case', [
    'valid', 'wrong-host', 'wrong-local-ip', 'wrong-node', 'wrong-label',
    'wrong-node-ip', 'wrong-node-host', 'lookup-failed', 'ip-failed',
    'malformed-node', 'empty-node', 'malformed-ip',
])
def test_host_mapping_before_docker(tmp_path, case):
    node = {'kind': 'Node', 'metadata': {'name': 'anxious-lookout', 'labels': {
        'kubernetes.io/hostname': 'anxious-lookout'}}, 'status': {'addresses': [
            {'type': 'InternalIP', 'address': '10.0.0.107'},
            {'type': 'Hostname', 'address': 'simple'}]}}
    if case == 'wrong-node':
        node['metadata']['name'] = 'other'
    if case == 'wrong-label':
        node['metadata']['labels']['kubernetes.io/hostname'] = 'other'
    if case == 'wrong-node-ip':
        node['status']['addresses'][0]['address'] = '10.0.0.108'
    if case == 'wrong-node-host':
        node['status']['addresses'][1]['address'] = 'other'
    prelude = tmp_path / 'mocks.sh'
    trace = tmp_path / 'docker-called'
    prelude.write_text('''
hostname() { [[ "$*" == '-s' ]] || return 90; printf '%s\\n' "$MOCK_HOST"; }
ip() { [[ "$*" == '-json address show' ]] || return 90; printf '%s\\n' "$MOCK_IP"; return "$IP_RC"; }
sudo() {
  [[ "$*" == '-n /usr/local/bin/k3s kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml --request-timeout=10s get node anxious-lookout -o json' ]] || return 90
  printf '%s\\n' "$MOCK_NODE"
  return "$LOOKUP_RC"
}
getenforce() { printf 'Enforcing\\n'; }
docker() { printf '%s\\n' "$*" > "$TRACE"; return 97; }
''')
    env = dict(os.environ, BASH_ENV=str(prelude), TRACE=str(trace),
               MOCK_HOST='other' if case == 'wrong-host' else 'simple',
               MOCK_IP='{' if case == 'malformed-ip' else json.dumps([{'addr_info': [
                   {'family': 'inet', 'local': '10.0.0.108' if case == 'wrong-local-ip' else '10.0.0.107'}]}]),
               MOCK_NODE='{' if case == 'malformed-node' else '' if case == 'empty-node' else json.dumps(node),
               IP_RC='1' if case == 'ip-failed' else '0',
               LOOKUP_RC='1' if case == 'lookup-failed' else '0')
    result = subprocess.run(['bash', str(SCRIPT), 'verify', 'sha256:' + 'a' * 64],
                            env=env, capture_output=True, timeout=5)
    assert result.returncode != 0  # Docker mock deliberately stops the valid case.
    assert trace.exists() == (case == 'valid'), result.stderr.decode()
    if case == 'valid':
        assert trace.read_text().startswith('image inspect --format ')
