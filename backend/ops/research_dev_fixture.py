"""Emit-only #97 development TLS fixture. No Kubernetes/network/process calls."""
import argparse
import base64
import copy
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HOST = 'research97.dev.invalid'
ORIGIN = 'https://' + HOST
PEER = '198.18.97.2'
NAME = 'issue97-r2-dev'
NS = 'm2-hosting'
ENCODING = {key: False for key in ('allowEncodedSlash', 'allowEncodedBackSlash',
    'allowEncodedNullCharacter', 'allowEncodedSemicolon', 'allowEncodedPercent',
    'allowEncodedQuestionMark', 'allowEncodedHash')}


def measurement(image):
    from .native_measure import measurement_pod
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', image):
        raise ValueError('Reviewed runner manifest digest required')
    pod = measurement_pod(NAME, 'codex-runner', image)
    # Explicit dev-only identity difference for existing target ingress rules.
    # No owner/state/generation or registered runner name, no credentials/Service.
    pod['metadata']['labels'] = {'anxious.io/purpose': NAME, 'anxious.io/managed': 'user-runner'}
    pod['spec']['hostAliases'] = [{'ip': PEER, 'hostnames': [HOST, 'wrong97.dev.invalid']}]
    pod['spec']['activeDeadlineSeconds'] = 900
    return pod


def resource(kind, spec):
    return {'apiVersion': 'traefik.io/v1alpha1', 'kind': kind,
            'metadata': {'name': NAME, 'namespace': NS, 'labels': {'anxious.io/purpose': NAME}}, 'spec': spec}


def certificates(now):
    """One-hour CA and leaf, exact synthetic SAN. Private outputs never printed."""
    ca_key, leaf_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(2))
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
            .issuer_name(ca_name).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return (ca.public_bytes(serialization.Encoding.PEM), leaf.public_bytes(serialization.Encoding.PEM),
            leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))


def bundle(now):
    ca, leaf, key = certificates(now)
    route = resource('IngressRoute', {'entryPoints': ['websecure'], 'tls': {'secretName': NAME}, 'routes': [
        {'kind': 'Rule', 'match': 'Host(`' + HOST + '`) && (Path(`/api`) || PathPrefix(`/api/`))'
         ' && !(Path(`/api/internal`) || PathPrefix(`/api/internal/`))',
         'priority': 20, 'middlewares': [{'name': NAME}], 'services': [{'name': 'api', 'port': 8080}]}]})
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'kubernetes.io/tls',
              'metadata': copy.deepcopy(route['metadata']),
              'data': {'tls.crt': base64.b64encode(leaf + ca).decode(), 'tls.key': base64.b64encode(key).decode()}}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [secret, resource('Middleware', {'encodedCharacters': ENCODING}), route]}, ca


def origin_patch(deployment):
    """UID/RV-guarded origin-only patch. No image, flag, RBAC or route mutation."""
    meta = deployment['metadata']
    if (deployment['kind'] != 'Deployment' or meta['name'] != 'api' or meta['namespace'] != NS
            or not meta.get('uid') or not meta.get('resourceVersion')):
        raise ValueError('Unexpected deployment identity')
    containers = deployment['spec']['template']['spec']['containers']
    if len(containers) != 1 or containers[0]['name'] != 'api':
        raise ValueError('Unexpected API layout')
    env = copy.deepcopy(containers[0].get('env', []))
    if len({entry['name'] for entry in env}) != len(env):
        raise ValueError('Duplicate env')
    env = [entry for entry in env if entry['name'] != 'AUTH_ORIGIN']
    env.append({'name': 'AUTH_ORIGIN', 'value': ORIGIN})
    return [{'op': 'test', 'path': '/metadata/uid', 'value': meta['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
            {'op': 'add', 'path': '/spec/template/spec/containers/0/env', 'value': env}]


def origin_restore(current, baseline):
    expected = copy.deepcopy(baseline['spec'])
    expected['template']['spec']['containers'][0]['env'] = origin_patch(baseline)[-1]['value']
    if current['metadata']['uid'] != baseline['metadata']['uid'] or current['spec'] != expected:
        raise ValueError('API changed during fixture window; review before restoring')
    patch = origin_patch(current)[:2]
    original = baseline['spec']['template']['spec']['containers'][0]
    path = '/spec/template/spec/containers/0/env'
    return patch + ([{'op': 'add', 'path': path, 'value': original['env']}]
                    if 'env' in original else [{'op': 'remove', 'path': path}])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--api-baseline', required=True)
    parser.add_argument('--restore-current')
    parser.add_argument('--runner-image')
    args = parser.parse_args()
    os.umask(0o077)
    out = Path(args.out)
    out.mkdir(mode=0o700)  # Refuse reuse, including a link/existing directory.
    deployment = json.loads(Path(args.api_baseline).read_text())
    if args.restore_current:
        current = json.loads(Path(args.restore_current).read_text())
        (out / 'origin.restore.patch.private.json').write_text(json.dumps(origin_restore(current, deployment)))
        print(json.dumps({'restore_prepared': True, 'execution': False}))
        return
    objects, ca = bundle(datetime.now(timezone.utc))
    (out / 'objects.private.json').write_text(json.dumps(objects))
    (out / 'ca.pem').write_bytes(ca)
    (out / 'origin.patch.private.json').write_text(json.dumps(origin_patch(deployment)))
    (out / 'baseline.env.private.json').write_text(json.dumps(deployment['spec']['template']['spec']['containers'][0].get('env', [])))
    if args.runner_image:
        (out / 'measurement.json').write_text(json.dumps(measurement(args.runner_image)))
    # CA signing key is not retained. The leaf key exists only in the TLS Secret file.
    print(json.dumps({'prepared': True, 'origin': ORIGIN, 'peer': PEER, 'execution': False}))


if __name__ == '__main__':
    main()
