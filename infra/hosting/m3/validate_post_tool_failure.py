"""Docker-only assertion of the actual post-callback HTTP400 counterexample."""
import json
from pathlib import Path

reports = []
for line in Path('/negative.jsonl').read_text().splitlines():
    try: value = json.loads(line)
    except ValueError: continue
    if isinstance(value, dict) and 'acceptancePassed' in value:
        reports.append(value)
assert len(reports) == 1
report = reports[0]
expected = 1 if report['transport'] == 'stdio' else 2
assert report['acceptancePassed'] is False
assert report['postToolFailuresInjected'] == expected
assert report['actualStockToolCalls'] == report['callbackCount'] == expected
assert report['exactSessionCallbacks'] is True  # Authority matched; later completion failed.
assert report['toolOutputs'] == report['completedTurns'] == 0
assert report['failedTurns'] == len(report['failures']) == expected
assert len(report['permissionProbeResults']) == expected
assert report['nativeExitCode'] == 0 and report['authJsonPersisted'] is False
print(json.dumps({'postToolFailureRejected': True, 'transport': report['transport'],
                  'callbacksBeforeFailure': expected, 'acceptancePassed': False}))
