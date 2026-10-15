"""Authless registration-contract diagnosis, Docker-only, zero model turns."""
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from stock_wire import Client

root = Path('/tmp/registration'); root.mkdir(mode=0o700)
for name in ('home', 'codex', 'work'): (root/name).mkdir(mode=0o700)
path = str(root/'control.sock')
args = ['/usr/local/bin/codex', 'app-server', '--listen', 'unix://'+path]
for value in ['cli_auth_credentials_store="ephemeral"', 'features.daemon_auto_start=false',
              'analytics.enabled=false', 'otel.metrics_exporter="none"']:
    args += ['-c', value]
p = subprocess.Popen(args, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(root/'home'),
                     'CODEX_HOME': str(root/'codex'), 'LANG': 'C.UTF-8'}, cwd=root/'work',
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
client = None
report = {'hostAuthRead': False, 'providerContact': False, 'modelTurns': 0,
          'binaryShaMatchesPin': hashlib.sha256(Path('/usr/local/bin/codex').read_bytes()).hexdigest()
          == 'fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e'}
try:
    deadline = time.monotonic()+10
    while not Path(path).exists() and time.monotonic()<deadline:
        assert p.poll() is None; time.sleep(.05)
    client = Client('registration', path)
    tool = {'type': 'function', 'name': 'registration_fixture', 'description': 'Synthetic registration',
            'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'deferLoading': False}
    cases = {'canonical': tool, 'legacy': {k:v for k,v in tool.items() if k != 'type'},
             'badKind': {**tool, 'type': 'not_a_tool_type'},
             'badSchema': {**tool, 'inputSchema': {'type': 'not_a_schema_type'}}}
    for label, value in cases.items():
        before = len(client.errors)
        result = client.rpc('thread/start', {'model': 'gpt-6.1-sol', 'cwd': str(root/'work'),
                           'approvalPolicy': 'never', 'sandbox': 'read-only', 'historyMode': 'legacy',
                           'ephemeral': False, 'dynamicTools': [value]})
        report[label+'Accepted'] = result is not None
        report[label+'ErrorCodes'] = [v['syntheticError']['code'] for v in client.errors[before:]]
        if result:
            identity = result['thread']['id']
            assert client.rpc('thread/name/set', {'threadId': identity, 'name': 'synthetic-'+label}) is not None
    rows = 0
    for db in (root/'codex').glob('state_*.sqlite'):
        with sqlite3.connect('file:'+str(db)+'?mode=ro', uri=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_dynamic_tools'").fetchone():
                rows += connection.execute('SELECT COUNT(*) FROM thread_dynamic_tools').fetchone()[0]
    report['persistedToolRows'] = rows
finally:
    if client: client.close()
    p.terminate()
    try: p.wait(timeout=50)
    except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=2)
    report['nativeExitCode'] = p.returncode
    print(json.dumps(report))
assert report['binaryShaMatchesPin'] and report['nativeExitCode'] == 0
