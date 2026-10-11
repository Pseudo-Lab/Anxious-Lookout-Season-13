"""Offline, network-none selfcheck inside the production Runner; mock queries only."""
import json
from pathlib import Path
import platform
import sys
import time

from access_runner_dns_prepare import RUNTIME_PROGRAM

source = Path(__file__).with_name('access_dns_diagnose.py').read_text().split("\nif __name__ == '__main__':")[0]
namespace = {'__name__': 'offline_fixture'}
exec(source+RUNTIME_PROGRAM, namespace)
calls = []
def probe(name, server, tcp=False):
    calls.append((name, server, tcp))
    return {'status': 'reply', 'rcode': 0, 'answersPresent': True}
value = namespace['runner_image_dns'](lambda: 'nameserver 10.43.0.10\noptions ndots:5\n', probe)
assert len(calls) == 4 and value['libcQueries'] == 0 and '10.43.0.10' not in json.dumps(value)
assert value['nativeStarted'] is False and value['sourceCredentialRead'] is False
if sys.argv[1:] == ['--deadline']:
    class Resolver:
        def __init__(self, *_): pass
        def read_text(self): return 'nameserver 10.43.0.10\n'
    def stalled(*_, **__):
        try: time.sleep(20)
        except Exception: return {}
    namespace.update(__name__='__main__', Path=Resolver, query=stalled)
    started = time.monotonic()
    try:
        exec(RUNTIME_PROGRAM, namespace)
        raise AssertionError('deadline did not stop program')
    except SystemExit as stopped:
        assert stopped.code == 1 and 9 <= time.monotonic()-started < 13
    print('{"productionRunnerDeadlinePassed":true,"networkRequested":false}')
    raise SystemExit(0)
assert sys.argv[1:] == []
print(json.dumps({'productionRunnerOfflineProgramPassed': True, 'mockHelperAttempts': len(calls),
                  'runtime': {'machine': platform.machine(), 'python': platform.python_version(),
                              'libcFamily': platform.libc_ver()[0], 'libcVersion': platform.libc_ver()[1]}}))
