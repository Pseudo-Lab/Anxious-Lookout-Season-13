"""Trusted host termination evidence, never inferred from API object absence.

Run in the host PID namespace as root with a read-only host proc mount and the
trusted local CRI socket. No registry mutation, credentials or process killing.
"""
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import time
from uuid import UUID

from runner.lifecycle import require


class HostTermination:
    def __init__(self, *, node_name, node_uid, boot_id, endpoint, proc='/proc', crictl='/usr/local/bin/crictl',
                 host_pid_namespace=None, host_cgroup_namespace=None):
        require(os.geteuid() == 0)
        # Supplied by reviewed root host inventory, never discovered from the
        # observer container itself. Namespace inode IDs are tied to boot_id.
        require(isinstance(host_pid_namespace, str) and re.fullmatch(r'pid:\[[1-9][0-9]*\]', host_pid_namespace))
        require(isinstance(host_cgroup_namespace, str) and re.fullmatch(r'cgroup:\[[1-9][0-9]*\]', host_cgroup_namespace))
        self.host_pid_namespace, self.host_cgroup_namespace = host_pid_namespace, host_cgroup_namespace
        require(isinstance(node_name, str) and node_name and '/' not in node_name)
        self.node_name, self.node_uid, self.boot_id = node_name, UUID(node_uid), UUID(boot_id)
        require(endpoint.startswith('unix:///') and '?' not in endpoint)
        socket = Path(endpoint.removeprefix('unix://'))
        require(socket == socket.resolve() and stat.S_ISSOCK(socket.stat().st_mode)
                and socket.stat().st_uid == 0 and not socket.stat().st_mode & 0o007)
        binary = Path(crictl)
        require(binary.is_absolute() and binary == binary.resolve() and binary.stat().st_uid == 0
                and not binary.stat().st_mode & 0o022 and os.access(binary, os.X_OK))
        self.endpoint, self.proc, self.crictl = endpoint, Path(proc), str(binary)
        require(self.proc.is_absolute() and self.proc == self.proc.resolve())
        filesystem = subprocess.run(['/usr/bin/stat', '-f', '--format=%t', str(self.proc)],
                                    check=True, capture_output=True, timeout=3)
        require(filesystem.stdout.strip() == b'9fa0')  # Linux PROC_SUPER_MAGIC.
        self.namespaces_match()

    def namespaces_match(self):
        require(os.readlink(self.proc/'1/ns/pid') == self.host_pid_namespace
                and os.readlink('/proc/self/ns/pid') == self.host_pid_namespace)
        require(os.readlink(self.proc/'1/ns/cgroup') == self.host_cgroup_namespace
                and os.readlink('/proc/self/ns/cgroup') == self.host_cgroup_namespace)

    def cri(self, command):
        result = subprocess.run([self.crictl, '--runtime-endpoint', self.endpoint, '--timeout', '5s',
                                 *command, '-o', 'json'], check=True, capture_output=True, timeout=8)
        require(len(result.stdout) <= 8 * 1024 * 1024)
        return json.loads(result.stdout)

    def processes_absent(self, pod_uid):
        require(self.proc.joinpath('sys/kernel/random/boot_id').read_text().strip() == str(self.boot_id))
        # A container-local PID view is not evidence about node processes.
        self.namespaces_match()
        expected = str(pod_uid).replace('-', '_')
        pids = [path for path in self.proc.iterdir() if path.name.isdecimal()]
        require(any(path.name == '1' for path in pids) and len(pids) <= 100000)
        for path in pids:
            try:
                value = (path/'cgroup').read_text()
            except FileNotFoundError:
                require(not path.exists())  # A process exited during this scan.
                continue
            require(value and len(value) <= 65536)
            require(expected not in value.replace('-', '_'))

    def node_matches(self, node, now):
        require(node.get('kind') == 'Node' and node['metadata']['name'] == self.node_name
                and node['metadata']['uid'] == str(self.node_uid)
                and not node['metadata'].get('deletionTimestamp')
                and node['status']['nodeInfo']['bootID'] == str(self.boot_id))
        ready = [item for item in node['status']['conditions'] if item['type'] == 'Ready']
        require(len(ready) == 1 and ready[0]['status'] == 'True')
        heartbeat = datetime.fromisoformat(ready[0]['lastHeartbeatTime'].replace('Z', '+00:00'))
        require(heartbeat.tzinfo is not None and 0 <= (now-heartbeat).total_seconds() <= 600)

    def verify(self, pod_uid, *, get_node, get_pod):
        require(isinstance(pod_uid, UUID))
        started = time.monotonic()
        # Callbacks must perform fresh authenticated GETs; get_pod returns None
        # only for 404, never timeout/403/parse errors. Caller fences creators.
        require(get_pod() is None)
        self.node_matches(get_node(), datetime.now(timezone.utc))
        sandboxes = self.cri(['pods'])
        containers = self.cri(['ps', '-a'])
        require(type(sandboxes.get('items')) is list and type(containers.get('containers')) is list)
        for item in sandboxes['items']:
            require(isinstance(item.get('metadata'), dict) and isinstance(item['metadata'].get('uid'), str))
            canonical_pod_uid(item['metadata']['uid'])
            if item['metadata']['uid'] == str(pod_uid):
                require(item.get('state') == 'SANDBOX_NOTREADY')
        for item in containers['containers']:
            require(isinstance(item.get('labels'), dict))
            canonical_pod_uid(item['labels'].get('io.kubernetes.pod.uid'))
            if item['labels'].get('io.kubernetes.pod.uid') == str(pod_uid):
                require(item.get('state') == 'CONTAINER_EXITED')
            # Missing ownership cannot be interpreted as unrelated workload.
            require(isinstance(item['labels'].get('io.kubernetes.pod.uid'), str))
        self.processes_absent(pod_uid)
        require(get_pod() is None)
        self.node_matches(get_node(), datetime.now(timezone.utc))
        require(self.proc.joinpath('sys/kernel/random/boot_id').read_text().strip() == str(self.boot_id))
        require(time.monotonic()-started <= 30)
        return {'podUid':str(pod_uid), 'nodeName':self.node_name, 'nodeUid':str(self.node_uid),
                'bootId':str(self.boot_id), 'verifiedAt':datetime.now(timezone.utc).isoformat()}


def canonical_pod_uid(value):
    require(isinstance(value, str))
    try:
        parsed = UUID(value)
    except ValueError:
        require(False)
    require(str(parsed) == value)
