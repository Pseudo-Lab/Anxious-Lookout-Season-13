import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest
from ops.runner_render import render, TEMPLATES


@pytest.fixture
def setup(tmp_path):
    root = tmp_path/'templates'
    root.mkdir()
    source = Path('/templates')
    for name in (*TEMPLATES, 'rehearsal-values.json'):
        shutil.copyfile(source/name, root/name)
    token = tmp_path/'token'
    token.write_text('synthetic-transport-private-'+'x'*40)
    token.chmod(0o600)
    return [root/name for name in TEMPLATES], root/'rehearsal-values.json', token, tmp_path/'out', tmp_path/'private'


def test_render_order_types_private_secret_redaction_and_hashes(setup):
    templates, values, token, public, private = setup
    assert render(*setup) == 10
    public_list = json.loads((public/'sha256.json').read_text())
    assert len(public_list) == 10
    assert [x['file'][:2] for x in public_list] == [f'{i:02d}' for i in range(10)]
    token64 = base64.b64encode(token.read_bytes()).decode()
    for path in public.iterdir():
        assert token.read_text() not in path.read_text() and token64 not in path.read_text()
    for directory in (public, private):
        assert directory.stat().st_mode & 0o777 == 0o700
        for row in json.loads((directory/'sha256.json').read_text()):
            path = directory/row['file']
            assert path.stat().st_mode & 0o777 == 0o600
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
    secret_path = next(private.glob('*secret*.json'))
    assert json.loads(secret_path.read_text())['data'] == {'token': token64}
    sa = json.loads(next(public.glob('*serviceaccount*.json')).read_text())
    assert sa['automountServiceAccountToken'] is False
    assert json.loads(next(public.glob('*persistentvolume-*.json')).read_text())['spec']['storageClassName'] == ''
    snapshots = {str(p):p.read_bytes() for p in public.iterdir()}
    with pytest.raises(ValueError): render(*setup)
    assert snapshots == {str(p):p.read_bytes() for p in public.iterdir()}


@pytest.mark.parametrize('key,value', [
 ('UNKNOWN','anything'), ('RUNNER_NATIVE_SIZE',1), ('RUNNER_NATIVE_SIZE',''),
 ('RUNNER_NATIVE_SIZE','10Gi'), ('RUNNER_STATIC_STORAGE_CLASS','local-path'),
 ('RUNNER_SYNTHETIC_OWNER','D888737C-4830-4761-9630-B400982F8618'),
 ('RUNNER_SYNTHETIC_STATE','../../escape'), ('RUNNER_NATIVE_PV','bad/name'),
 ('RUNNER_NATIVE_PV','bad"\nname'), ('RUNNER_CONTROL_PV','${UNKNOWN}'),
 ('RUNNER_TEST_NAMESPACE','codex-trial'), ('RUNNER_APPROVED_ACCESS_MODE','ReadWriteMany')])
def test_bad_inputs_refused_before_output(setup, key, value):
    data = json.loads(setup[1].read_text()); data[key] = value
    setup[1].write_text(json.dumps(data))
    with pytest.raises(ValueError): render(*setup)
    assert not setup[3].exists() and not setup[4].exists()


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'same-pv', 'unknown-placeholder',
    'remaining-placeholder', 'secret-in-public', 'secret-permissions', 'secret-link', 'secret-newline', 'output-link'])
def test_rejects_ambiguous_or_unsafe_render(setup, damage):
    templates, values, token, public, private = setup
    data = json.loads(values.read_text())
    if damage == 'missing':
        data.pop('RUNNER_NATIVE_PV'); values.write_text(json.dumps(data))
    elif damage == 'duplicate':
        values.write_text(values.read_text().replace('{','{"RUNNER_NATIVE_PV":"duplicate",',1))
    elif damage == 'same-pv':
        data['RUNNER_CONTROL_PV'] = data['RUNNER_NATIVE_PV']; values.write_text(json.dumps(data))
    elif damage in ('unknown-placeholder','remaining-placeholder','secret-in-public'):
        marker = {'unknown-placeholder':'${UNKNOWN}', 'remaining-placeholder':'${bad}',
                  'secret-in-public':'${RUNNER_PRIVATE_TRANSPORT_TOKEN_BASE64}'}[damage]
        templates[0].write_text(templates[0].read_text().replace('${RUNNER_TEST_NAMESPACE}', marker,1))
    elif damage == 'secret-permissions': token.chmod(0o644)
    elif damage == 'secret-link':
        actual = token.with_name('real-token'); token.rename(actual); token.symlink_to(actual)
    elif damage == 'secret-newline': token.write_text(token.read_text()+'\n')
    elif damage == 'output-link': public.symlink_to(public.with_name('other'))
    with pytest.raises((ValueError, OSError)): render(*setup)
    assert not private.exists()


def test_cli_never_logs_secret_on_success_or_failure(setup):
    templates, values, token, public, private = setup
    args = [sys.executable, '-B', '-m', 'ops.runner_render', '--check', '--values', str(values),
            '--secret-file', str(token), '--output', str(public), '--private-output', str(private)]
    for path in templates: args += ['--template', str(path)]
    for expected in (0, 1):
        result = subprocess.run(args, capture_output=True, text=True, timeout=3)
        assert result.returncode == expected
        output = result.stdout+result.stderr
        assert token.read_text() not in output
        assert base64.b64encode(token.read_bytes()).decode() not in output
        assert str(token) not in output


@pytest.mark.parametrize('kind', ['fifo', 'socket', 'device'])
def test_cli_rejects_special_secret_without_waiting(setup, kind):
    templates, values, token, public, private = setup
    token.unlink()
    with socket.socket(socket.AF_UNIX) as endpoint:
        if kind == 'fifo':
            os.mkfifo(token, 0o600)  # No writer: blocking open would hang.
        elif kind == 'socket':
            endpoint.bind(str(token))
            token.chmod(0o600)
        else:
            token = Path('/dev/null')
        args = [sys.executable, '-B', '-m', 'ops.runner_render', '--check',
                '--values', str(values), '--secret-file', str(token),
                '--output', str(public), '--private-output', str(private)]
        for path in templates:
            args += ['--template', str(path)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=3)
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == 'Preparation render refused; private values and paths are not printed\n'
    assert not public.exists() and not private.exists()


def test_fifo_swapped_after_lstat_is_rejected(setup, monkeypatch):
    token = setup[2]
    original_open = os.open
    def swap(path, flags, *args, **kwargs):
        if path == token:
            assert flags & os.O_NONBLOCK
            token.unlink()
            os.mkfifo(token, 0o600)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swap)
    with pytest.raises(ValueError):
        render(*setup)
    assert not setup[3].exists() and not setup[4].exists()


@pytest.mark.parametrize('damage', ['short', 'long', 'hardlink', 'private-exists', 'nested'])
def test_additional_review_rejections(setup, damage):
    templates, values, token, public, private = setup
    if damage in ('short', 'long'):
        token.write_bytes(b'x' * (31 if damage == 'short' else 257))
    elif damage == 'hardlink':
        os.link(token, token.with_name('linked-token'))
    elif damage == 'private-exists':
        private.mkdir()
        (private/'marker').write_text('preserve')
    else:
        private = public/'nested'
    with pytest.raises(ValueError):
        render(templates, values, token, public, private)
    assert not public.exists()
    if damage == 'private-exists':
        assert (private/'marker').read_text() == 'preserve'


def test_reversed_templates_keep_output_order(setup):
    templates, values, token, public, private = setup
    assert render(list(reversed(templates)), values, token, public, private) == 10
    assert [row['file'][:2] for row in json.loads((public/'sha256.json').read_text())] == [f'{i:02d}' for i in range(10)]
