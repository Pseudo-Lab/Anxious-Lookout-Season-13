"""Run against the actual runtime image in network-none Docker and empty tmpfs only."""
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ops.runner_policy import OWNER, STATE, plan_for
from ops.runner_rehearsal_init import initialize

assert os.geteuid() == os.getegid() == 10001
assert not Path('/usr/local/bin/codex').exists()  # runtime target, provider excluded
initialize(Path('/state'), Path('/control'))
token = secrets.token_urlsafe(48)
plan = plan_for({'image': 'synthetic@sha256:'+'a'*64,
    'callback': 'https://rehearsal-callback.invalid:443/research/tools',
    'callbackNamespace': 'codex-runner-callback-disabled', 'callbackServiceAccount': 'callback-disabled',
    'callbackPort': '443', 'quotaResourceVersion': '1'}).manifest('codex-runner-rehearsal')
container = plan['spec']['containers'][0]
pod_uid = '23c64cfe-f7bf-4cdb-9d46-eb250fc3f230'
env = {'PATH': os.environ['PATH'], 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
env.update({item['name']: item['value'] for item in container['env'] if 'value' in item})
assert [item['valueFrom']['secretKeyRef']['key'] for item in container['env'] if item['name'] == 'RUNNER_TOKEN'] == ['token']
env['RUNNER_TOKEN'] = token  # Kubernetes resolves the Secret reference; the runner drops it after reading.
env['RUNNER_POD_UID'] = pod_uid
process = subprocess.Popen(container['command'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def health(bearer, state):
    headers = {'Authorization': 'Bearer '+bearer}
    if state is not None: headers['X-Runner-State-Id'] = state
    try:
        with urlopen(Request('http://127.0.0.1:8080/health', headers=headers), timeout=2) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


try:
    deadline = time.monotonic()+20
    while True:
        assert process.poll() is None, 'runner startup failed'
        try:
            status, body = health(token, STATE)
            break
        except URLError:
            if time.monotonic() >= deadline: raise AssertionError('runner startup timeout')
            time.sleep(0.1)
    assert status == 200
    assert (body['ownerId'], body['stateId'], body['podUid']) == (OWNER, STATE, pod_uid)
    assert body['available'] is False and body['verification'] == 'unverified' and body['reason'] == 'not_configured'
    assert health('wrong-bearer', STATE)[0] == 401
    assert health(token, '00000000-0000-0000-0000-000000000000')[0] == 503
    assert health(token, None)[0] == 503
    assert not list(Path('/state').rglob('auth.json'))
    # No supplied envelope, session or turn call; no native binary exists.
finally:
    process.terminate()
    try:
        _, logs = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill(); process.communicate()
        raise AssertionError('runner shutdown timeout')
    assert token.encode() not in logs
    # Uvicorn re-raises the captured SIGTERM after lifespan cleanup.
    assert process.returncode in (0, -signal.SIGTERM)
    assert b'Application shutdown complete' in logs
print('PASS actual image TCP /health: 200 unverified/unavailable, wrong bearer401, wrong/missing state503; provider excluded')
