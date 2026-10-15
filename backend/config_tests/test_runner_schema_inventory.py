import json
from pathlib import Path

import pytest
from ops.runner_schema_inventory import ROOTS, inventory, references


def test_pinned_inventory_closes_all_references_and_preserves_constraints():
    raw = Path('/schema/swagger.json').read_bytes()
    result = inventory(raw)
    committed = Path('/inventory/pod-v1.36.4.json').read_bytes()
    assert (json.dumps(result, indent=2, sort_keys=True) + '\n').encode() == committed
    definitions = result['definitions']
    assert set(ROOTS) <= definitions.keys()
    original = json.loads(raw)['definitions']
    for name, schema in definitions.items():
        assert set(references(schema)) <= definitions.keys()
        assert set(schema.get('properties', {})) == set(original[name].get('properties', {}))
        assert schema.get('required') == original[name].get('required')
        for field, spec in schema.get('properties', {}).items():
            for key, value in original[name]['properties'][field].items():
                if key.startswith('x-kubernetes-'):
                    assert spec[key] == value
    assert 'nodeName' in definitions[ROOTS[0]]['properties']
    assert 'runtimeClassName' in definitions[ROOTS[0]]['properties']
    assert 'annotations' in definitions[ROOTS[1]]['properties']
    assert 'io.k8s.api.core.v1.PodStatus' not in definitions
    assert inventory(raw) == result


def test_schema_drift_is_rejected():
    with pytest.raises(ValueError, match='digest'):
        inventory(Path('/schema/swagger.json').read_bytes() + b'\n')
