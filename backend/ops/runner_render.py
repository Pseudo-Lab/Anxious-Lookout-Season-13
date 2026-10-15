"""Offline, fail-closed renderer for reviewed rehearsal preparation JSON only."""
import argparse
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

TEMPLATES = ('00-foundation.json.in', '05-static-volumes.json.in',
             '10-static-claims.json.in', '20-transport-secret.json.in')
KEYS = {'RUNNER_TEST_NAMESPACE', 'RUNNER_SYNTHETIC_OWNER', 'RUNNER_SYNTHETIC_STATE',
        'RUNNER_NATIVE_PV', 'RUNNER_CONTROL_PV', 'RUNNER_NATIVE_SIZE', 'RUNNER_CONTROL_SIZE',
        'RUNNER_STATIC_STORAGE_CLASS', 'RUNNER_APPROVED_ACCESS_MODE'}
SECRET = 'RUNNER_PRIVATE_TRANSPORT_TOKEN_BASE64'
PLACEHOLDER = re.compile(r'\$\{([A-Z0-9_]+)\}')


def require(value):
    if not value:
        raise ValueError('Preparation render refused')


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def read_json(path):
    require(path.stat().st_size <= 1024 * 1024)
    return json.loads(path.read_text(), object_pairs_hook=unique)


def checked_values(path):
    values = read_json(path)
    require(type(values) is dict and set(values) == KEYS)
    require(all(type(v) is str and '${' not in v for v in values.values()))
    require(all(v or k == 'RUNNER_STATIC_STORAGE_CLASS' for k, v in values.items()))
    require(values['RUNNER_TEST_NAMESPACE'] == 'codex-runner-rehearsal')
    require(values['RUNNER_STATIC_STORAGE_CLASS'] == '')
    require(values['RUNNER_APPROVED_ACCESS_MODE'] == 'ReadWriteOnce')
    for key in ('RUNNER_SYNTHETIC_OWNER', 'RUNNER_SYNTHETIC_STATE'):
        require(str(UUID(values[key])) == values[key])
    require(values['RUNNER_SYNTHETIC_OWNER'] != values['RUNNER_SYNTHETIC_STATE'])
    for key in ('RUNNER_NATIVE_PV', 'RUNNER_CONTROL_PV'):
        require(len(values[key]) <= 63 and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', values[key]))
    require(values['RUNNER_NATIVE_PV'] != values['RUNNER_CONTROL_PV'])
    # Current reviewed small rehearsal only; do not silently expand capacity.
    require(values['RUNNER_NATIVE_SIZE'] == values['RUNNER_CONTROL_SIZE'] == '1Gi')
    return values


def private_token(path):
    require(path.absolute() == path.resolve())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
        token = source.read(257)
    require(re.fullmatch(rb'[!-~]{32,256}', token) is not None)
    return base64.b64encode(token).decode('ascii')


def substitute(value, values, used, path=()):
    if isinstance(value, dict):
        require(all('${' not in key for key in value))
        return {key: substitute(item, values, used, path + (key,)) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, values, used, path + (i,)) for i, item in enumerate(value)]
    if not isinstance(value, str):
        return value  # Preserve JSON number/bool/null types, never parse strings as JSON.
    def replace(match):
        key = match[1]
        require(key in values)
        if key == SECRET:
            require(path == ('data', 'token') and value == '${' + SECRET + '}')
        used.add(key)
        return values[key]
    result = PLACEHOLDER.sub(replace, value)
    require('${' not in result)
    return result


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode()


def write_new(path, payload):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())


def render(templates, inputs, token_file, output, private_output):
    output, private_output = output.absolute(), private_output.absolute()
    require(len(templates) == len(TEMPLATES) and {p.name for p in templates} == set(TEMPLATES))
    require(all(p.is_file() and p.absolute() == p.resolve() for p in templates))
    values = checked_values(inputs)
    values[SECRET] = private_token(token_file)
    used, objects = set(), []
    by_name = {p.name: p for p in templates}
    for name in TEMPLATES:
        raw = read_json(by_name[name])
        if raw.get('kind') == 'List':
            require(type(raw.get('items')) is list)
            require(SECRET not in json.dumps(raw))
            items = raw['items']
        else:
            require(name == '20-transport-secret.json.in' and raw.get('kind') == 'Secret')
            items = [raw]
        for item in items:
            objects.append(substitute(item, values, used))
    require(used == KEYS | {SECRET})
    require([o['kind'] for o in objects] == ['Namespace', 'ResourceQuota', 'NetworkPolicy',
        'ServiceAccount', 'ServiceAccount', 'PersistentVolume', 'PersistentVolume',
        'PersistentVolumeClaim', 'PersistentVolumeClaim', 'Secret'])
    public_files, private_files = {}, {}
    for index, obj in enumerate(objects):
        name = obj['metadata']['name']
        require(isinstance(name, str) and len(name) <= 63 and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', name))
        filename = f'{index:02d}-{obj["kind"].lower()}-{name}.json'
        if obj['kind'] == 'Secret':
            require(obj.get('data') == {'token': values[SECRET]})
            private_files[filename] = encoded(obj)
            redacted = deepcopy(obj)
            redacted['data']['token'] = '<REDACTED: NOT FOR APPLY>'
            public_files[filename.replace('.json', '.redacted.json')] = encoded(redacted)
        else:
            require(values[SECRET] not in json.dumps(obj))
            public_files[filename] = encoded(obj)
    for directory in (output, private_output):
        require(directory.absolute() == directory.resolve() and not directory.exists())
        require(directory.parent.is_dir())
    require(output != private_output and output not in private_output.parents and private_output not in output.parents)
    # Validation/rendering finished before any output. On I/O failure preserve
    # partial private evidence; never overwrite, clean up, or retry automatically.
    output.mkdir(mode=0o700)
    private_output.mkdir(mode=0o700)
    for directory, files in ((output, public_files), (private_output, private_files)):
        manifest = []
        for filename, payload in files.items():
            write_new(directory / filename, payload)
            manifest.append({'file': filename, 'sha256': hashlib.sha256(payload).hexdigest(),
                             'redacted': filename.endswith('.redacted.json')})
        write_new(directory / 'sha256.json', encoded(manifest))
    return len(objects)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, action='append', required=True)
    parser.add_argument('--values', type=Path, required=True)
    parser.add_argument('--secret-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--private-output', type=Path, required=True)
    parser.add_argument('--check', action='store_true', required=True)
    args = parser.parse_args()
    try:
        count = render(args.template, args.values, args.secret_file, args.output, args.private_output)
    except Exception:
        parser.exit(1, 'Preparation render refused; private values and paths are not printed\n')
    print(f'PASS rendered {count} preparation objects; no cluster action; private Secret excluded from logs')


if __name__ == '__main__':
    main()
