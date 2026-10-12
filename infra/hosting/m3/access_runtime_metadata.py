"""Read-only selected Docker evidence validation; never selects a runtime."""
import json
from pathlib import Path
import re

TOOL_IMAGE = 'sha256:e1cd49c5e8d36fae0689e727c06459529bbc67e823e08385602ea534c6397450'
FILES = {'default': 'clock-runtime-default', 'names': 'clock-runtime-names', 'own': 'clock-runtime-own'}
FIELDS = {'name', 'id', 'imageId', 'imageRef', 'user', 'runtime', 'isolation', 'usernsMode',
          'networkMode', 'pidMode', 'readonlyRootfs', 'privileged', 'capDrop', 'securityOpt'}


def validate_runtime_metadata(descriptor, evidence):
    unknown = {'status': 'unknown', 'reasons': [], 'daemon': None, 'controller': None}
    try:
        if not isinstance(descriptor, dict): raise ValueError('descriptor_missing')
        cid, name = descriptor.get('containerId'), descriptor.get('containerName')
        if (type(cid) is not str or not re.fullmatch('[a-f0-9]{64}', cid)
                or type(name) is not str or not re.fullmatch(r'numeric-controller-[0-9]+-[0-9]+-clock', name)
                or descriptor.get('image') != TOOL_IMAGE): raise ValueError('creation_binding_missing')
        if descriptor.get('identityVerified') is not True: raise ValueError('own_identity_unverified')
        if descriptor.get('withinBudget') is not True: raise ValueError('metadata_late_or_partial')
        directory = Path(evidence)
        raw = {}
        for key, filename in FILES.items():
            target = directory/(filename+'.stdout'); code = directory/(filename+'.exit')
            if target.is_symlink() or code.is_symlink() or target.stat().st_size > 65536: raise ValueError('invalid_evidence_file')
            if code.read_text().strip() != '0': raise ValueError('read_failed_or_unavailable')
            raw[key] = target.read_text()
        default = json.loads(raw['default'])
        names = raw['names'].splitlines()
        valid_name = lambda n: type(n) is str and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', n)
        if not valid_name(default) or not names or len(names) > 64 or len(set(names)) != len(names) or any(not valid_name(n) for n in names) or default not in names:
            raise ValueError('runtime_names_invalid')
        header, payload = raw['own'].split('\n', 1)
        if header.split() != [cid, TOOL_IMAGE, '/'+name]: raise ValueError('own_identity_mismatch')
        own = json.loads(payload)
        if not isinstance(own, dict) or set(own) != FIELDS: raise ValueError('selected_fields_incomplete_or_extra')
        if own['id'] != cid or own['name'] != '/'+name or own['imageId'] != TOOL_IMAGE or own['imageRef'] != TOOL_IMAGE:
            raise ValueError('own_identity_mismatch')
        if any(type(own[k]) is not str for k in FIELDS-{'readonlyRootfs','privileged','capDrop','securityOpt'}): raise ValueError('field_type_invalid')
        if not own['runtime'] or own['runtime'] not in names: raise ValueError('effective_runtime_unknown')
        if (own['user'] != '0:0' or own['networkMode'] != 'none' or own['pidMode'] not in ('', 'private')
                or own['readonlyRootfs'] is not True or own['privileged'] is not False
                or own['capDrop'] != ['ALL'] or not isinstance(own['securityOpt'], list)
                or any(type(v) is not str for v in own['securityOpt']) or 'no-new-privileges' not in own['securityOpt']):
            raise ValueError('invocation_profile_mismatch')
        return {'status': 'collected', 'reasons': [], 'daemon': {'defaultRuntime': default, 'runtimeNames': names},
                'controller': own, 'runtimeSelectionAuthorized': False}
    except Exception as error:
        # Only fixed validator labels; no arbitrary Docker output/paths/env.
        reason = str(error) if type(error) is ValueError and re.fullmatch('[a-z_]+', str(error)) else 'malformed_or_unavailable'
        unknown['reasons'] = [reason]; unknown['runtimeSelectionAuthorized'] = False
        return unknown
