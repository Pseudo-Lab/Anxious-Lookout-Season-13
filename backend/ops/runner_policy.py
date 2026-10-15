"""Generate a review candidate, never apply policies or change cluster gates."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID
from urllib.parse import urlsplit

from app.runner_controller import Identity
from app.runner_kube import Plan, Reference
from ops.runner_render import encoded, require, unique, write_new

NAMESPACE = 'codex-runner-rehearsal'
OWNER = 'd888737c-4830-4761-9630-b400982f8618'
STATE = 'a9617ace-fa4b-4bbc-b70b-2188f0efe3e6'
SPEC = 'io.k8s.api.core.v1.PodSpec'
META = 'io.k8s.apimachinery.pkg.apis.meta.v1.ObjectMeta'
INPUTS = {'image', 'callback', 'callbackNamespace', 'callbackServiceAccount', 'callbackPort', 'quotaResourceVersion'}


def literal(value):
    # CEL accepts JSON double-quoted string escapes; values are encoded as literals,
    # never interpolated into a quoted template expression.
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'))


def plan_for(values):
    require(type(values) is dict and set(values) == INPUTS)
    require(all(type(v) is str and v and v.isascii() for v in values.values()))
    require(re.fullmatch(r'[1-9][0-9]*', values['quotaResourceVersion']))
    for key in ('callbackNamespace', 'callbackServiceAccount'):
        require(re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?', values[key]))
    require(re.fullmatch(r'[1-9][0-9]{0,4}', values['callbackPort']))
    require(int(values['callbackPort']) <= 65535)
    url = urlsplit(values['callback'])
    require(url.scheme == 'https' and url.hostname and url.hostname.isascii()
            and re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', url.hostname)
            and not url.username and not url.password and not url.query and not url.fragment
            and (url.port or 443) == int(values['callbackPort']))
    require(not any(ord(c) < 33 or c in '\\"\'' for c in values['callback']))
    return Plan(Identity(UUID(OWNER), UUID(STATE), 1), values['image'],
        Reference('native-synthetic', UUID('9d65a601-31bd-40ea-b23d-cd13fb4ea11a'), 'codex-runner-rehearsal-native-a9617ace'),
        Reference('control-synthetic', UUID('0daaef7f-07b3-4178-9a83-11fece308b08'), 'codex-runner-rehearsal-control-a9617ace'),
        Reference('transport-synthetic', UUID('41b9ad3d-a0be-47eb-8812-91aee26efdc8')),
        values['callback'], 'runner-workload')


def defaults(manifest):
    value = deepcopy(manifest)
    spec = value['spec']
    spec.update(serviceAccount='runner-workload', schedulerName='default-scheduler', priority=0,
                preemptionPolicy='PreemptLowerPriority', tolerations=[
        {'key': 'node.kubernetes.io/'+key, 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300}
        for key in ('not-ready', 'unreachable')])
    spec['containers'][0].update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File', resources={})
    return value


class Envelope:
    def __init__(self, inventory):
        self.defs = inventory['definitions']
        self.rows = []

    def resolve(self, schema):
        while '$ref' in schema:
            schema = self.defs[schema['$ref'].removeprefix('#/definitions/')]
        return schema

    def exact(self, path, schema, expected, original):
        """Typed property selection, never object-key iteration or object literals."""
        schema = self.resolve(schema)
        mode = 'exact' if original is not None else 'server-default-whitelist'
        self.rows.append({'path': path, 'disposition': mode, 'expected': expected})
        if schema.get('type') == 'object' and 'properties' in schema:
            require(type(expected) is dict)
            require(set(expected) <= schema['properties'].keys())
            checks = []
            for key, child in sorted(schema['properties'].items()):
                field = path+'.'+key
                if key in expected:
                    orig = original.get(key) if isinstance(original, dict) else None
                    checks.append('has('+field+') && ('+self.exact(field, child, expected[key], orig)+')')
                else:
                    self.rows.append({'path': field, 'disposition': 'absent',
                        'subtree': child.get('$ref', child.get('type', 'unknown')),
                        'negative': sample(self.resolve(child), self)})
                    checks.append('!has('+field+')')
            return '('+' && '.join(checks)+')' if checks else 'true'
        if schema.get('type') == 'array':
            require(type(expected) is list)
            parts = [path+'.size() == '+str(len(expected))]
            for index, item in enumerate(expected):
                orig = original[index] if isinstance(original, list) and index < len(original) else None
                parts.append(self.exact(f'{path}[{index}]', schema['items'], item, orig))
            return '('+' && '.join(parts)+')'
        if schema.get('type') == 'object':
            # Free-form maps (labels) have scalar values; exact size/keys/value.
            require(type(expected) is dict)
            return path+' == '+literal(expected)
        return path+' == '+literal(expected)


def candidate(values, inventory):
    plan = plan_for(values)
    manifest = plan.manifest(NAMESPACE)
    positive = defaults(manifest)
    builder = Envelope(inventory)
    validations = []
    cases = []

    def add(label, expression, path=None, bad=None):
        require(len(expression.encode()) < 10000)
        validations.append({'expression': expression, 'message': 'runner-envelope: '+label})
        cases.append({'validation': len(validations)-1, 'path': path, 'bad': bad})

    add('creator/create only', "request.operation == 'CREATE' && request.userInfo.username == "+literal('system:serviceaccount:'+NAMESPACE+':runner-controller'))
    add('resource', "object.apiVersion == 'v1' && object.kind == 'Pod'", ['kind'], 'NotPod')
    # Creation-only metadata: server-assigned evidence fields are kept separate
    # from workload overrides. Values are not usable as execution exceptions.
    meta_schema = builder.defs[META]['properties']
    for key, schema in sorted(meta_schema.items()):
        path = 'object.metadata.'+key
        if key in manifest['metadata']:
            expression = 'has('+path+') && ('+builder.exact(path, schema, manifest['metadata'][key], manifest['metadata'][key])+')'
            value = manifest['metadata'][key]
            bad = dict(value, **{'unexpected': 'value'}) if isinstance(value, dict) else 'unexpected'
            add('metadata.'+key, expression, ['metadata', key], bad)
        elif key in ('uid', 'resourceVersion', 'creationTimestamp', 'managedFields', 'generation'):
            # Schema types constrain evidence; never used to choose Pod targets.
            builder.rows.append({'path': path, 'disposition': 'server-default-whitelist',
                'expected': 'server evidence only; no execution authority; CREATE wrapper rejects caller-supplied metadata'})
        else:
            builder.rows.append({'path': path, 'disposition': 'absent', 'subtree': schema.get('$ref', schema.get('type'))})
            add('metadata.'+key, '!has('+path+')', ['metadata', key], sample(builder.resolve(schema), builder))

    # Split large container/volume objects into bounded validations. All guards
    # remain in each expression so mutations yield false rather than index errors.
    properties = builder.defs[SPEC]['properties']
    for key, schema in sorted(properties.items()):
        path = 'object.spec.'+key
        if key not in positive['spec']:
            builder.rows.append({'path': path, 'disposition': 'absent', 'subtree': schema.get('$ref', schema.get('type'))})
            add('spec.'+key, '!has('+path+')', ['spec', key], sample(builder.resolve(schema), builder))
            continue
        expected = positive['spec'][key]
        orig = manifest['spec'].get(key)
        if key in ('containers', 'volumes'):
            builder.rows.append({'path': path, 'disposition': 'exact', 'expected': 'ordered cardinality '+str(len(expected))})
            guard = 'has('+path+') && '+path+'.size() == '+str(len(expected))
            add('spec.'+key+'.count', guard, ['spec', key], [])
            if key == 'containers':
                fields = builder.resolve(schema['items'])['properties']
                for field, child in sorted(fields.items()):
                    location = path+'[0].'+field
                    if field in expected[0]:
                        expr = 'has('+location+') && ('+builder.exact(location, child, expected[0][field], orig[0].get(field))+')'
                        bad = mutated(expected[0][field])
                        if expected[0][field] == {}:
                            props = builder.resolve(child)['properties']
                            member = sorted(props)[0]
                            bad = {member: sample(builder.resolve(props[member]), builder)}
                    else:
                        builder.rows.append({'path': location, 'disposition': 'absent', 'subtree': child.get('$ref', child.get('type'))})
                        expr = '!has('+location+')'
                        bad = sample(builder.resolve(child), builder)
                    add('container.'+field, guard+' && ('+expr+')', ['spec', key, 0, field], bad)
            else:
                for i, item in enumerate(expected):
                    expr = builder.exact(f'{path}[{i}]', schema['items'], item, orig[i])
                    add('volume.'+str(i), guard+' && ('+expr+')', ['spec', key, i, 'name'], 'unexpected')
        else:
            expr = builder.exact(path, schema, expected, orig)
            if key in ('hostNetwork', 'hostPID', 'hostIPC'):
                expr = '!has('+path+') || ('+expr+')'
                builder.rows[-1]['serialization'] = 'false nonpointer bool may be omitted by native JSON serialization'
            else:
                expr = 'has('+path+') && ('+expr+')'
            add('spec.'+key, expr, ['spec', key], mutated(expected))
    # Add nested field mutations to the owning expression, beyond one negative
    # per expression. Parent absence forbids its entire referenced subtree.
    indexes = {v['message'].removeprefix('runner-envelope: '): i for i, v in enumerate(validations)}
    for row in builder.rows:
        path = row['path']
        parts = [int(p) if p.isdigit() else p for p in re.findall(r'[A-Za-z_][A-Za-z_0-9]*|[0-9]+', path)][1:]
        if path.startswith('object.spec.containers[0].'):
            key = 'container.'+parts[3]
        elif path.startswith('object.spec.volumes['):
            key = 'volume.'+str(parts[2])
        elif path.startswith('object.spec.'):
            key = 'spec.'+parts[1]
        else:
            key = 'metadata.'+parts[1]
        if key not in indexes: continue  # server evidence fields, no workload grant
        if row['disposition'] == 'absent':
            if 'negative' not in row: continue  # already covered by top-level case
            bad = row['negative']
        else:
            value = row['expected']
            if not isinstance(value, (str, bool, int)): continue
            if path in ('object.spec.containers', 'object.spec.volumes'): continue
            bad = mutated(value)
        cases.append({'validation': indexes[key], 'path': parts, 'bad': bad})
    return manifest, positive, validations, cases, builder.rows


def mutated(value):
    if type(value) is bool: return not value
    if type(value) is int: return value+1
    if type(value) is str: return value+'-changed'
    if type(value) is list: return [] if value else ['changed']
    if type(value) is dict: return {'unexpected': 'changed'}
    raise ValueError('Unhandled mutation')


def sample(schema, builder):
    if schema.get('format') == 'date-time': return '2026-10-15T00:00:00Z'
    kind = schema.get('type')
    if kind == 'boolean': return True
    if kind == 'integer': return 1
    if kind == 'number': return 1.0
    if kind == 'array': return []
    if kind == 'object': return {}
    return 'unexpected'


INVENTORY_SHA = '83fc7e81dfb8aec4a15d50c80f166a9ec240d15d8de1ea665bcd887fe54a70e7'


def public_bytes(path):
    require(path.absolute() == path.resolve() and stat.S_ISREG(path.lstat().st_mode))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= 1024*1024)
        raw = source.read(1024*1024+1)
    require(len(raw) <= 1024*1024)
    return raw


def load_inventory(path):
    raw = public_bytes(path)
    require(hashlib.sha256(raw).hexdigest() == INVENTORY_SHA)
    return json.loads(raw)


def policy_objects(validations, gated):
    namespace_selector = {'matchLabels': {'kubernetes.io/metadata.name': NAMESPACE}}
    out = []
    for name, rules, checks in (
        ('runner-envelope-rehearsal', [{'apiGroups': [''], 'apiVersions': ['v1'],
          'operations': ['CREATE', 'UPDATE'], 'resources': ['pods'], 'scope': 'Namespaced'}],
         ([{'expression': 'false', 'message': 'runner-draft-gate: pending exact render and observation approval'}] if gated else []) + validations),
        ('runner-subresources-rehearsal', [
          {'apiGroups': [''], 'apiVersions': ['v1'], 'operations': ['UPDATE'], 'resources': ['pods/ephemeralcontainers'], 'scope': 'Namespaced'},
          {'apiGroups': [''], 'apiVersions': ['v1'], 'operations': ['CONNECT'], 'resources': ['pods/exec', 'pods/attach', 'pods/portforward'], 'scope': 'Namespaced'}],
         [{'expression': 'false', 'message': 'runner-subresource: forbidden'}])):
        out.append({'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicy',
            'metadata': {'name': name}, 'spec': {'failurePolicy': 'Fail',
            'matchConstraints': {'matchPolicy': 'Exact', 'namespaceSelector': namespace_selector, 'resourceRules': rules},
            'validations': checks}})
        out.append({'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicyBinding',
            'metadata': {'name': name}, 'spec': {'policyName': name, 'validationActions': ['Deny'],
            'matchResources': {'namespaceSelector': namespace_selector}}})
    return out


def render(values_file, inventory_file, network_template, output):
    values = json.loads(public_bytes(values_file), object_pairs_hook=unique)
    inv = load_inventory(inventory_file)
    manifest, positive, validations, cases, rows = candidate(values, inv)
    network_values = {'RUNNER_TEST_NAMESPACE': NAMESPACE, 'RUNNER_API_PORT': '6443',
        'RUNNER_CALLBACK_NAMESPACE': values['callbackNamespace'], 'RUNNER_CALLBACK_SERVICE_ACCOUNT': values['callbackServiceAccount'],
        'RUNNER_CALLBACK_PORT': values['callbackPort'], 'RUNNER_DNS_NAMESPACE': 'kube-system',
        'RUNNER_DNS_APP_LABEL': 'kube-dns', 'RUNNER_CALLBACK_DNS_NAME': urlsplit(values['callback']).hostname}
    from ops.runner_render import substitute
    used = set()
    network_raw = public_bytes(network_template)
    require(hashlib.sha256(network_raw).hexdigest() ==
            'b3fc36fe59bf504c6372ea7b9466eff73c07b7e37eeaabf6059bf4a613d21597')
    network = substitute(json.loads(network_raw), network_values, used)
    require(used == set(network_values))
    require(network.get('kind') == 'List' and len(network['items']) == 3)
    files = {'plan.json': manifest, 'synthetic-defaulted.json': positive,
        'dispositions.json': rows, 'suite.json': {'positive': positive, 'validations': validations, 'cases': cases,
            'literalProbes': [{'expression': literal(s), 'expected': s} for s in
                ['a" || true //', "single'quote", 'back\\slash', 'line\nreturn\rtab\t', '\u2028']]},
        'inputs.json': values}
    for i, obj in enumerate(network['items']):
        files[f'network-{i:02}.json'] = obj
    for gated in (True, False):
        prefix = 'gate-retained' if gated else 'gate-removal-candidate'
        for i, obj in enumerate(policy_objects(validations, gated)):
            files[f'{prefix}-{i:02}.json'] = obj
    # Distinct files are review artifacts, never a multi-document apply bundle.
    for count in ('0', '1'):
        files[f'quota-{count}.json'] = {'apiVersion': 'v1', 'kind': 'ResourceQuota',
            'metadata': {'name': 'runner-bootstrap-no-pods', 'namespace': NAMESPACE,
                         'resourceVersion': values['quotaResourceVersion']},
            'spec': {'hard': {'count/pods': count}}}
    output = output.absolute()
    require(output == output.resolve() and not output.exists() and output.parent.is_dir())
    payloads = {name: encoded(obj) for name, obj in files.items()}
    require(all(b'${' not in data for data in payloads.values()))
    output.mkdir(mode=0o700)
    for filename, payload in payloads.items():
        write_new(output/filename, payload)
    write_new(output/'sha256.json', encoded([{'file': f, 'sha256': hashlib.sha256(p).hexdigest()}
                                           for f, p in sorted(payloads.items())]))
    return len(validations)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--values', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--network-template', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        count = render(args.values, args.inventory, args.network_template, args.output)
    except Exception:
        parser.exit(1, 'Policy candidate render refused\n')
    print(f'REVIEW ONLY: {count} validations rendered; no gate, quota or cluster changed')


if __name__ == '__main__':
    main()
