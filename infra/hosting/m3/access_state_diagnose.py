"""Read-only stopped web state summary. Never reads source/auth/token/journal contents."""
import json
import os
import stat
import time
from pathlib import Path


def read(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or info.st_nlink != 1 or info.st_size > 1_000_000):
            raise ValueError()
        data = source.read(1_000_001)
        if len(data) > 1_000_000:
            raise ValueError()
        return json.loads(data)


def summary(native, control, owner, state_id, window=None):
    root, private = Path(native), Path(control)
    for directory in (root, private):
        info = directory.lstat()
        if directory != directory.resolve() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError()
    report = {'sourceCredentialRead': False, 'stateModified': False, 'nativeStarted': False,
              'bindingMatches': None, 'ledgerInstalled': False,
              'projectionExists': (private/'access-projection.json').exists(),
              'brokerExists': (root/'broker.json').exists(),
              'unexpectedNativeAuthFile': (root/'codex'/'auth.json').exists()}
    binding = read(root/'state-binding.json')
    report['bindingMatches'] = binding == {'version': 1, 'ownerId': owner, 'stateId': state_id}
    if not report['bindingMatches']:
        raise ValueError()
    path = private/'access-ledger.json'
    if not path.exists():
        return report
    ledger = read(path)
    if (ledger.get('version') != 1 or ledger.get('owner') != owner or ledger.get('stateId') != state_id
            or not isinstance(ledger.get('requests'), list) or not isinstance(ledger.get('windows'), list)
            or type(ledger.get('deadline')) is not int or type(ledger.get('windowStartedAt')) is not int):
        raise ValueError()
    blocked = ledger.get('blocked')
    if blocked not in {None, 'auth_expired', 'auth_revoked', 'model_unavailable', 'provider_rate_limited', 'policy_refused', 'unavailable'}:
        raise ValueError()
    if type(ledger.get('modelBlocked', False)) is not bool:
        raise ValueError()
    report.update(ledgerInstalled=True, lifetimeReservations=len(ledger['requests']),
                  windowReservations=sum(v['windowId'] == ledger['windowId'] for v in ledger['requests']),
                  windowCount=len(ledger['windows']), selectedWindowMatches=ledger['windowId'] == window if window else None,
                  deadlinePassed=time.time() >= ledger['deadline'],
                  windowTimeLimitPassed=time.time() >= ledger['windowStartedAt']+900,
                  blockedReason=blocked, modelBlocked=ledger.get('modelBlocked', False))
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(summary('/native/access-web', '/control/access-web', os.environ['RUNNER_ACCOUNT_ID'],
                                 os.environ['RUNNER_STATE_ID'], os.getenv('DIAG_WINDOW_ID'))))
    except Exception:
        raise SystemExit('Stopped web state diagnosis refused; private values are not printed')
