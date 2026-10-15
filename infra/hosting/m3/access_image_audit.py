"""Offline Docker OCI archive chain -> immutable production reference. No import."""
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path


def audit(archive, metadata_file, inspect_file, repository, source_sha):
    if not re.fullmatch(r'[a-f0-9]{40}', source_sha):
        raise ValueError()
    metadata = json.loads(Path(metadata_file).read_text())
    info = json.loads(Path(inspect_file).read_text())[0]
    target = metadata['containerimage.digest']
    with tarfile.open(archive, 'r') as image:
        def read(digest):
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
                raise ValueError()
            data = image.extractfile('blobs/sha256/'+digest.split(':')[1]).read()
            if 'sha256:'+hashlib.sha256(data).hexdigest() != digest:
                raise ValueError()
            return json.loads(data)
        descriptor = read(target)
        if 'manifests' in descriptor:
            matches = [v for v in descriptor['manifests'] if v.get('platform', {}).get('os') == 'linux'
                       and v.get('platform', {}).get('architecture') == 'arm64']
            if len(matches) != 1:
                raise ValueError()
            manifest = matches[0]['digest']
        else:
            manifest = target
        body = read(manifest)
        config_digest = body['config']['digest']
        config = read(config_digest)
        if (info['Id'] not in {target, config_digest} or config['architecture'] != 'arm64'
                or config['os'] != 'linux' or config['config']['Labels']['org.opencontainers.image.revision'] != source_sha):
            raise ValueError()
        # Verify every layer rather than trusting descriptor names alone.
        for layer in body['layers']:
            value = hashlib.sha256()
            source = image.extractfile('blobs/sha256/'+layer['digest'].split(':')[1])
            while block := source.read(1_000_000):
                value.update(block)
            if 'sha256:'+value.hexdigest() != layer['digest']:
                raise ValueError()
    with open(archive, 'rb') as source:
        archive_digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'sourceSha': source_sha, 'reference': repository+'@'+target, 'dockerId': info['Id'],
            'targetDigest': target, 'arm64Manifest': manifest, 'configDigest': config_digest,
            'archiveSha256': archive_digest, 'productionTarget': True, 'imported': False}


if __name__ == '__main__':
    try:
        print(json.dumps(audit(*sys.argv[1:])))
    except Exception:
        raise SystemExit('Production access image audit failed; no import performed')
