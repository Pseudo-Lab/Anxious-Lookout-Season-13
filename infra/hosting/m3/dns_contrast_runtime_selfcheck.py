"""Network-none Docker smoke for the actual Runner's own PID1/metadata/tmpfs claim."""
import json
from pathlib import Path
import sys
import time

import access_dns_contrast_exec as launch
import access_dns_contrast_runtime as runtime
import access_runner_dns_prepare as direct

receipt = json.loads(sys.stdin.read())
expected = {key: receipt[key] for key in ('podUid', 'podName', 'startTicks', 'startNs', 'cmdlineSha256', 'nonce')}
expected['holderCommand'] = launch.holder_command(receipt['nonce'])
if '--counter' in sys.argv:
    expected.update(counterMode=True, counterTimeNamespace=str(Path('/proc/self/ns/time').readlink()),
                    counterBeforeEndNs=time.clock_gettime_ns(time.CLOCK_BOOTTIME))
helper = Path('/checks/access_dns_diagnose.py').read_text().split("\nif __name__ == '__main__':")[0]
program = helper+direct.RUNTIME_PROGRAM
namespace = {'__name__': 'definitions'}
exec(program.split("\nif __name__ == '__main__':")[0], namespace)
calls = []
def mock_query(name, resolver, tcp=False):
    calls.append((name, tcp))
    return {'status': 'reply', 'rcode': 0, 'answersPresent': True}
namespace['query'] = mock_query
if '--repeat' in sys.argv:
    try: runtime.run_once(expected, namespace)
    except FileExistsError: pass
    else: raise AssertionError('claim must prevent a second exec')
    assert calls == []
    print('{"duplicateExecRefused":true,"queryCalls":0}')
else:
    result = runtime.run_once(expected, namespace)
    assert calls == [(name, tcp) for name in ('chatgpt.com', 'api.m2-hosting.svc.cluster.local') for tcp in (False, True)]
    assert result['helperAttempts'] == 4 and result['libcQueries'] == 0
    if '--counter' in sys.argv:
        assert result['counterBudget']['timeNamespace'] == expected['counterTimeNamespace']
        assert result['counterBudget']['enteredNs'] < result['counterBudget']['startNs']+18_000_000_000
        assert result['counterBudget']['finishedNs'] < result['counterBudget']['startNs']+28_000_000_000
    print(json.dumps({'actualRunnerPid1OwnershipPassed': True, 'counterProfilePassed': '--counter' in sys.argv,
                      'mockHelperCalls': 4, 'actualDnsQueries': 0}))
