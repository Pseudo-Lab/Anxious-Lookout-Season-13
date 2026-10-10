"""Read-only selected-process field filter; no auth/config/cache/native RPC.

Proc buffers may contain secret bytes. No unknown/env value is decoded, printed or
persisted; operators must separately approve these exact bounded metadata inputs.
"""
import json
import sys
from pathlib import Path

LIMIT = 131072
MODES = ('file', 'keyring', 'auto', 'ephemeral')
ENV_KEYS = ('HOME', 'CODEX_HOME', 'OPENAI_API_KEY', 'OPENAI_FEDERATION_RULE_ID',
            'OPENAI_IDENTITY_TOKEN_FILE', 'OPENAI_WORKLOAD_IDENTITY_CONTEXT')


def read_buffer(path):
    with path.open('rb') as source:
        value = source.read(LIMIT+1)
    if len(value) > LIMIT or (value and not value.endswith(b'\0')):
        raise ValueError('Invalid bounded process buffer')
    return value.split(b'\0')[:-1] if value else []


def fields(argv, environment):
    if not argv or not argv[0]:
        raise ValueError('Missing selected command')
    present = {key: any(item.partition(b'=')[0] == key.encode('ascii') for item in environment) for key in ENV_KEYS}
    overrides = []
    profile = remote = no_daemon = other_config = False
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == b'--':
            break
        profile |= argument in (b'--profile', b'-p') or argument.startswith(b'--profile=')
        remote |= argument == b'--remote' or argument.startswith(b'--remote=')
        no_daemon |= argument == b'--no-daemon'
        expression = None
        if argument in (b'-c', b'--config'):
            index += 1
            if index >= len(argv):
                raise ValueError('Missing config value')
            expression = argv[index]
        elif argument.startswith(b'--config='):
            expression = argument[len(b'--config='):]
        elif argument.startswith(b'-c') and len(argument) > 2:
            expression = argument[2:]
        if expression is not None:
            key, separator, value = expression.partition(b'=')
            if separator and key.strip() == b'cli_auth_credentials_store':
                scalar = value.strip()
                mode = next((mode for mode in MODES if scalar in
                             (mode.encode(), ('"'+mode+'"').encode(), ("'"+mode+"'").encode())), None)
                overrides.append(mode or 'unrecognized')
            else:
                other_config = True
        index += 1
    return {'argvObservation': 'lexical_not_cli_resolution',
            'environmentObservation': 'proc_initial_environment_not_live_auth',
            'environmentPresence': present, 'profileFlagPresent': profile,
            'remoteFlagPresent': remote, 'noDaemonFlagPresent': no_daemon,
            'cliStoreOverride': overrides[-1] if overrides else None,
            'storeOverrideCount': len(overrides), 'otherConfigOverridePresent': other_config,
            'workloadIdentitySelectionMarked': present['OPENAI_FEDERATION_RULE_ID'] or present['OPENAI_IDENTITY_TOKEN_FILE'],
            'workloadIdentityConfigurationValidated': False, 'activeAuthMode': 'not_observed',
            'activeStoreBinding': 'not_established', 'resolvedCredentialBackend': 'not_observed',
            'rawProcessBuffersRead': True, 'secretValuesDecoded': False,
            'credentialFilesRead': False, 'authRpcExecuted': False}


def main():
    try:
        result = fields(read_buffer(Path('/inputs/cmdline')), read_buffer(Path('/inputs/environ')))
    except (OSError, ValueError):
        print('Selected process fields unavailable; raw values are not printed', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
