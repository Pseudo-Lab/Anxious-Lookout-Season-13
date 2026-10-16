"""Docker UID10001 probe; mount the isolated executor IPC, never private state."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from runner.shell_broker import ShellBroker

broker = ShellBroker(Path('/tmp/broker.sock'), lambda turn, args: turn['owner'], lambda value: value, wall=1)
try:
    lease = broker.activate({'owner': True})
    def call(token, command):
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'SHELL_BROKER_SOCKET': '/tmp/broker.sock', 'SHELL_BRIDGE_LEASE': token}
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'shell', 'arguments': {'command': command}}}
        result = subprocess.run([sys.executable, '-I', '/source/runner/shell_bridge.py'],
            input=json.dumps(request)+'\n', text=True, capture_output=True, env=env, timeout=12, check=True)
        assert result.stderr == ''
        return json.loads(json.loads(result.stdout)['result']['content'][0]['text'])
    result = call(lease, 'printf bridge-ok')
    assert result['status'] == 'completed' and result['stdout'] == 'bridge-ok', result
    result = call(lease, 'sleep 5')
    assert result['status'] == 'timed_out' and result['error']['code'] == 'shell_timeout', result
    public = broker.activate({'owner': False})
    assert call(lease, 'touch stale')['error']['code'] == 'tool_not_allowed'
    assert call(public, 'touch public')['error']['code'] == 'tool_not_allowed'
    lease = broker.activate({'owner': True})
    result = call(lease, 'test ! -e stale && test ! -e public && id -u')
    assert result['status'] == 'completed' and result['stdout'].strip() == '20002', result
    results = []
    worker = threading.Thread(target=lambda: results.append(call(lease, 'sleep 5')))
    worker.start()
    deadline = time.monotonic() + 3
    while not broker.connections and time.monotonic() < deadline:
        time.sleep(.01)
    assert broker.connections
    broker.revoke()
    worker.join(timeout=5)
    assert not worker.is_alive() and results[0]['error']['code'] == 'shell_unavailable', results
    print('MCP -> private broker -> UID20002 executor, timeout, stale/public fence passed')
finally:
    broker.close()
