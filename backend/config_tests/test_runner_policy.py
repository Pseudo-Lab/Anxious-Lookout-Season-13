from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from ops.runner_policy import candidate, literal, load_inventory, plan_for, render

ROOT = Path('/draft')


@pytest.fixture
def inputs():
    return json.loads((ROOT/'schema/synthetic-values.json').read_text())


def test_dispositions_cover_plan_and_top_level_schema(inputs):
    inventory = load_inventory(ROOT/'schema/pod-v1.36.4.json')
    manifest, positive, validations, cases, rows = candidate(inputs, inventory)
    assert rows == json.loads((ROOT/'schema/dispositions.synthetic.json').read_text())
    paths = {r['path'] for r in rows}
    for name, prefix in [('io.k8s.api.core.v1.PodSpec', 'object.spec'),
                         ('io.k8s.apimachinery.pkg.apis.meta.v1.ObjectMeta', 'object.metadata')]:
        assert all(prefix+'.'+key in paths for key in inventory['definitions'][name]['properties'])
    def walk(value, path):
        assert path in paths
        if isinstance(value, dict):
            for k, v in value.items():
                # Label map entries are covered by exact map equality.
                if path != 'object.metadata.labels': walk(v, path+'.'+k)
        elif isinstance(value, list):
            for i, v in enumerate(value): walk(v, f'{path}[{i}]')
    for key, value in manifest['spec'].items():
        if key == 'containers':
            for field, item in value[0].items(): walk(item, 'object.spec.containers[0].'+field)
        else: walk(value, 'object.spec.'+key)
    assert len(cases) >= len(validations)
    assert {c['validation'] for c in cases} == set(range(len(validations)))
    assert all(x['expression'] != 'false' for x in validations)
    assert all(len(x['expression']) < 10000 for x in validations)
    assert positive['spec']['tolerations'][0] != positive['spec']['tolerations'][1]


def test_render_hashes_gate_separation_and_no_overwrite(tmp_path):
    out = tmp_path/'render'
    args = (ROOT/'schema/synthetic-values.json', ROOT/'schema/pod-v1.36.4.json', ROOT/'network.json.in', out)
    render(*args)
    hashes = json.loads((out/'sha256.json').read_text())
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    for row in hashes:
        assert hashlib.sha256(before[row['file']]).hexdigest() == row['sha256']
        assert b'${' not in before[row['file']]
    assert json.loads(before['gate-retained-00.json'])['spec']['validations'][0]['expression'] == 'false'
    assert all(x['expression'] != 'false' for x in json.loads(before['gate-removal-candidate-00.json'])['spec']['validations'])
    assert json.loads(before['gate-removal-candidate-02.json'])['spec']['validations'][0]['expression'] == 'false'
    assert json.loads(before['quota-1.json'])['metadata']['resourceVersion'] == '123'
    assert json.loads(before['quota-0.json'])['spec']['hard']['count/pods'] == '0'
    with pytest.raises(ValueError): render(*args)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


@pytest.mark.parametrize('field,value', [('image','image:latest'),('callback','https://user:secret@callback.example'),
    ('callback','https://callback.example/?token=secret'),('callbackPort','443'),
    ('callbackNamespace',"x' || true"),('quotaResourceVersion','0'),('extra','value')])
def test_invalid_inputs(inputs, field, value):
    inputs[field] = value
    with pytest.raises(ValueError): plan_for(inputs)


def test_literal_escaping_is_not_expression_interpolation():
    attack = 'a"\\\n\t\' || true //'
    assert json.loads(literal(attack)) == attack
    assert '\\n' in literal(attack) and '\\"' in literal(attack)


@pytest.mark.parametrize('target', ['inventory', 'network'])
def test_unreviewed_schema_or_template_refused_before_output(tmp_path, target):
    inventory = ROOT/'schema/pod-v1.36.4.json'
    network = ROOT/'network.json.in'
    altered = tmp_path/'altered.json'
    altered.write_bytes((inventory if target == 'inventory' else network).read_bytes()+b'\n')
    if target == 'inventory': inventory = altered
    else: network = altered
    with pytest.raises(ValueError):
        render(ROOT/'schema/synthetic-values.json', inventory, network, tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_policy_cli_refuses_fifo_input_without_waiting(tmp_path):
    import os
    import subprocess
    import sys
    fifo = tmp_path/'values'
    os.mkfifo(fifo)
    result = subprocess.run([sys.executable, '-B', '-m', 'ops.runner_policy',
        '--values', str(fifo), '--inventory', str(ROOT/'schema/pod-v1.36.4.json'),
        '--network-template', str(ROOT/'network.json.in'), '--output', str(tmp_path/'out')],
        timeout=3, capture_output=True, text=True)
    assert result.returncode == 1 and result.stderr == 'Policy candidate render refused\n'
    assert not (tmp_path/'out').exists()
