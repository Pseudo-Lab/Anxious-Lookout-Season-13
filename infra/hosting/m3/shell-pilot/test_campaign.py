"""#64 campaign.sh against a fake ssh: the real `harness.py gate`, scripted slots, no cluster or model."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FAKE_SSH = r'''#!PYTHON
import json, os, re, subprocess, sys
state, command = os.environ['FAKE_STATE'], sys.argv[-1]
with open(state + '/calls', 'a') as log:
    log.write(command + '\n')
config = 'A' if 'pilot-a' in command else 'B'
scenario = json.load(open(state + '/scenario.json'))
ledger_path = f'{state}/ledger-{config}.json'
if 'harness.py budget' in command:
    print(open(ledger_path).read())
elif 'harness.py gate' in command:
    sys.exit(subprocess.run([sys.executable, '-B', os.environ['HARNESS'], 'gate'], stdin=sys.stdin).returncode)
elif 'harness.py run' in command:
    if scenario.get('transport'):
        sys.exit(255)
    task, slot = re.search(r'--task (\S+) --config \S+ --slot (\d+)', command).groups()
    key = f'{task}-{config}-{int(slot):02d}'
    ledger = json.load(open(ledger_path))
    status = scenario.get('status', 'completed')
    ledger.setdefault('slots', {})[key] = {'run': key, 'status': status}
    if status.startswith('harness_error'):
        ledger.setdefault('errors', []).append(key)
    json.dump(ledger, open(ledger_path, 'w'))
    open(f'{state}/console', 'w').write(scenario.get('stdout', '{"run": "%s"}' % key))
    print('rc=%d' % scenario.get('rc', 0))
elif 'grep -rlE' in command:
    print(scenario.get('check', 'exit=1'))
elif ' cat ' in command:
    print(open(f'{state}/console').read())
elif 'wc -l' in command:
    print(0)
else:
    sys.exit(99)
'''
JWT = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMtb25seSJ9.sig'


def ledger(**fields):
    return {'input': 0, 'cachedInput': 0, 'output': 0, 'runs': [], 'unknown': [], 'inflight': None, **fields}


@pytest.fixture
def campaign(tmp_path):
    bin_dir, state, out = tmp_path/'bin', tmp_path/'state', tmp_path/'out'
    for path in (bin_dir, state, out):
        path.mkdir()
    (bin_dir/'ssh').write_text(FAKE_SSH.replace('PYTHON', sys.executable))
    (bin_dir/'ssh').chmod(0o755)
    def run(scenario, max_slots=60, a=None, b=None):
        (state/'scenario.json').write_text(json.dumps(scenario))
        (state/'ledger-A.json').write_text(json.dumps(a or ledger()))
        (state/'ledger-B.json').write_text(json.dumps(b or ledger()))
        env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'HOME': str(tmp_path),
               'FAKE_STATE': str(state), 'HARNESS': str(HERE/'harness.py')}
        done = subprocess.run(['bash', str(HERE/'campaign.sh'), str(out), str(max_slots)], env=env,
                              capture_output=True, text=True, timeout=60)
        calls = (state/'calls').read_text().splitlines() if (state/'calls').exists() else []
        return done.returncode, (out/'progress.md').read_text(), [c for c in calls if 'harness.py run' in c]
    return run


def test_a_detected_jwt_stops_before_any_output_leaves_the_pod(campaign):
    code, progress, runs = campaign({'stdout': '{"summary": "%s"}' % JWT, 'check': '/pilot/results/x.json exit=0'})
    assert code == 2 and len(runs) == 1 and 'eyJ' not in progress and 'JWT check not clean' in progress


def test_a_transport_failure_stops_without_calling_the_slot_again(campaign):
    code, progress, runs = campaign({'transport': True})
    assert code == 2 and len(runs) == 1 and 'transport failure' in progress


def test_a_non_zero_exit_stops_after_recording_the_clean_output(campaign):
    code, progress, runs = campaign({'rc': 1})
    assert code == 2 and len(runs) == 1 and 'exited 1' in progress


def test_the_gate_judges_the_last_slot_before_pausing(campaign):
    a = ledger(errors=['x'], slots={'x': {'status': 'harness_error: X'}})
    b = ledger(errors=['y'], slots={'y': {'status': 'harness_error: Y'}})
    code, progress, runs = campaign({'status': 'harness_error: grade OSError'}, max_slots=1, a=a, b=b)
    assert code == 2 and len(runs) == 1 and '3 harness errors across A and B' in progress


def test_slots_alternate_and_pause_after_max(campaign):
    code, progress, runs = campaign({}, max_slots=3)
    assert code == 3 and 'PAUSE' in progress and 'next slow-suite B 1' in progress
    assert [r.split('--config ')[1][0] for r in runs] == ['A', 'B', 'A']


def test_an_abandoned_claim_stops_the_run(campaign):
    code, progress, runs = campaign({}, a=ledger(slots={'fix-failing-test-A-01': {'run': 'r', 'status': 'started'}}))
    assert code == 2 and runs == [] and 'claimed but never finished' in progress
