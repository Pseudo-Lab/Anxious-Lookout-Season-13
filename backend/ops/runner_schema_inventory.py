"""Offline inventory of the pinned Kubernetes Pod admission schema, not CEL validation."""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE = 'https://raw.githubusercontent.com/kubernetes/kubernetes/v1.36.4/api/openapi-spec/swagger.json'
SHA256 = 'dcede2063da1d7ad62ecb5af8adb6d7fabd0b52385a7fa0048afb491dac90450'
ROOTS = ('io.k8s.api.core.v1.PodSpec', 'io.k8s.apimachinery.pkg.apis.meta.v1.ObjectMeta')


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in sorted(value.items())
                if k not in ('description', 'externalDocs', 'example')}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == '$ref':
                if not item.startswith('#/definitions/'):
                    raise ValueError('Nonlocal schema reference')
                yield item.removeprefix('#/definitions/')
            else:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def inventory(raw):
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('Unreviewed Kubernetes schema digest')
    definitions = json.loads(raw)['definitions']
    selected = {}
    pending = list(ROOTS)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        schema = compact(definitions[name])
        selected[name] = schema
        pending.extend(references(schema))
    return {
        'source': SOURCE, 'sourceSha256': SHA256,
        'roots': list(ROOTS),
        'scope': 'Pod spec and metadata reachable types; excludes Pod status. No CEL compilation or admission/defaulting proof.',
        'definitions': dict(sorted(selected.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.schema.read_bytes())
    with args.output.open('x') as target:
        target.write(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(f'Inventory: {len(result["definitions"])} reachable definitions; no CEL/admission claim')


if __name__ == '__main__':
    main()
