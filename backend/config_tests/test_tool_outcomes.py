"""#94 (2) tool_outcomes.py (infra/hosting/m3/chat-stream): read only, fixed names and codes. Pure files."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'infra' / 'hosting' / 'm3' / 'chat-stream' / 'tool_outcomes.py'
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason='infra/ not mounted')
SECRET = 'SECRET-USER-CONTENT'
IN, OUT = '2026-11-15T11:55:00.123Z', '2026-11-15T13:00:00.000Z'


def line(kind, payload, stamp=IN):
    return json.dumps({'timestamp': stamp, 'type': kind, 'payload': payload})


def call(name, call_id, stamp=IN):
    return line('response_item', {'type': 'function_call', 'name': name, 'call_id': call_id,
                                  'arguments': json.dumps({'query': SECRET})}, stamp)


def output(call_id, value, stamp=IN, kind='function_call_output', **extra):
    return line('response_item', {'type': kind, 'call_id': call_id, 'output': value, **extra}, stamp)


def sessions(tmp_path, *lines, name='rollout-2026-11-15T11-50-00-0000.jsonl'):
    folder = tmp_path / 'sessions' / '2026' / '11' / '15'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text('\n'.join(lines) + '\n')
    return tmp_path / 'sessions'


def run(root, since='2026-11-15T11:50:00Z', until='2026-11-15T12:10:00Z'):
    done = subprocess.run([sys.executable, '-I', str(SCRIPT), str(root), '--since', since, '--until', until],
                          capture_output=True, text=True)
    assert SECRET not in done.stdout and SECRET not in done.stderr
    return done.returncode, json.loads(done.stdout)


def test_tool_calls_are_paired_with_their_outputs(tmp_path):
    root = sessions(tmp_path,
                    line('event_msg', {'type': 'turn_started'}),
                    call('research_list', 'c1'),
                    output('c1', json.dumps({'error': {'code': 'tool_not_allowed', 'message': SECRET}})),
                    call('research_get', 'c2'),
                    output('c2', [{'type': 'input_text', 'text': json.dumps({'items': [SECRET]})}], success=True),
                    call('research_versions', 'c3'),
                    output('c3', {'content': json.dumps({'error': {'code': 'SECRET-code', 'message': 'x'}}),
                                  'success': False}),
                    call('research_relations', 'c4'),
                    output('c4', 'plain text ' + SECRET),
                    call('Some ' + SECRET, 'c5'),
                    call('research_archive', 'c6'),
                    line('event_msg', {'type': 'turn_complete'}),
                    line('event_msg', {'type': SECRET}))
    code, value = run(root)
    assert code == 0 and value['ok'] is True
    assert value['calls'] == [
        {'tool': 'research_list', 'output': 'error:tool_not_allowed', 'success': 'absent'},
        {'tool': 'research_get', 'output': 'ok-json', 'success': True},
        {'tool': 'research_versions', 'output': 'error:other', 'success': False},
        {'tool': 'research_relations', 'output': 'text', 'success': 'absent'},
        {'tool': 'other', 'output': 'missing', 'success': 'absent'},
        {'tool': 'research_archive', 'output': 'missing', 'success': 'absent'}]
    assert value['eventKinds']['event_msg/turn_started'] == 1 and value['eventKinds']['other'] == 1
    assert value['files'] == {'scanned': 1, 'inWindow': 1}


def test_only_the_window_counts(tmp_path):
    root = sessions(tmp_path, call('research_list', 'early', '2026-11-15T11:00:00Z'),
                    call('research_list', 'in'), output('in', json.dumps({'items': []})),
                    output('early', json.dumps({'error': {'code': 'not_found'}}), OUT))
    code, value = run(root)
    assert value['calls'] == [{'tool': 'research_list', 'output': 'ok-json', 'success': 'absent'}]
    assert value['unpairedOutputs'] == 0


def test_code_mode_exec_counts_and_error_hints(tmp_path):
    root = sessions(tmp_path,
                    line('response_item', {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'x1',
                                           'input': 'await tools.research_list({}) // ' + SECRET}),
                    output('x1', 'Error: {"error":{"code":"not_found","message":"' + SECRET + '"}}',
                           kind='custom_tool_call_output'),
                    call('exec_command', 'x2'), output('x2', 'ok ' + SECRET))
    code, value = run(root)
    assert value['exec'] == {'calls': 2, 'outputs': 2, 'errorTextHints': {'not_found': 1}}
    assert value['calls'] == []


def test_links_fifos_and_odd_files_are_not_read(tmp_path):
    root = sessions(tmp_path, call('research_list', 'c1'))
    folder = root / '2026' / '11' / '15'
    target = tmp_path / 'elsewhere.jsonl'
    target.write_text(call('research_get', 'z') + '\n')
    os.symlink(target, folder / 'rollout-linked.jsonl')
    os.mkfifo(folder / 'rollout-fifo.jsonl')
    (folder / 'notes.jsonl').write_text(call('research_get', 'n') + '\n')
    (folder / 'rollout-bad.jsonl').write_text('not json\n[1]\n{"timestamp": 5}\n')
    code, value = run(root)
    assert code == 0 and [c['tool'] for c in value['calls']] == ['research_list']
    assert value['files'] == {'scanned': 2, 'inWindow': 1}


@pytest.mark.parametrize('since, until', [('2026-11-15T12:00:00Z', '2026-11-15T11:00:00Z'),
                                          ('yesterday', '2026-11-15T12:00:00Z'),
                                          ('2026-11-15T00:00:00Z', '2026-11-15T07:00:00Z')])
def test_bad_windows_refuse(tmp_path, since, until):
    root = sessions(tmp_path, call('research_list', 'c1'))
    code, value = run(root, since, until)
    assert code == 2 and value == {'ok': False, 'reason': 'window'}


def test_missing_directory_and_usage_refuse(tmp_path):
    code, value = run(tmp_path / 'absent')
    assert code == 2 and value['reason'] == 'directory_missing'
    done = subprocess.run([sys.executable, '-I', str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert done.returncode == 2 and json.loads(done.stdout)['reason'] == 'usage'
