"""#90 P5 r4 (review r3 majors), development VM only, READ ONLY: the pins the continuation decision binds, read from
the one s0 evidence directory. Nothing is written. Run as root on the guest with the reviewed bytes on stdin:

    sudo -n python3 -I -B - < i90-p5-s0-pins.py

One JSON line: the s0 run name (fixed pattern), whether run.json's conversation has the full UUID shape, the sha256 of
the conversation UUID (the UUID itself is not printed), the sha256 of exit.json's bytes, exit.json's top-level key
names (only from the fixed list the script writes; others counted), its verdict keys (fixed list; others counted),
and the type of runnerReleased / privateRemoved (null / bool / missing). rc 0 printed; 3 refused.
"""
import hashlib
import json
import os
import re
import stat
import sys

EVIDENCE = '/root/issue90/evidence'
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
EXIT_KEYS = ('rc', 'stepRc', 'verdicts', 'stoppedAt', 'runnerReleased', 'clientsGone', 'privateRemoved',
             'sessionRevoked', 'finished')
VERDICT_KEYS = ('S0', 'release')


def read(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
        raise OSError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        return stream.read(1 << 20)


def kind(done, key):
    if key not in done:
        return 'missing'
    return 'null' if done[key] is None else type(done[key]).__name__


def main():
    if os.geteuid() != 0:
        print(json.dumps({'refused': 'not root'}))
        return 3
    runs = sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-p5-s0-\d{8}T\d{6}Z', x))
    if len(runs) != 1:
        print(json.dumps({'refused': 'not exactly one s0 evidence directory'}))
        return 3
    try:
        run = json.loads(read(os.path.join(EVIDENCE, runs[0], 'run.json')))
        raw = read(os.path.join(EVIDENCE, runs[0], 'exit.json'))
        done = json.loads(raw)
    except Exception:
        print(json.dumps({'refused': 'run.json or exit.json unreadable'}))
        return 3
    conversation = run.get('conversation') if isinstance(run, dict) else None
    shaped = isinstance(conversation, str) and UUID.fullmatch(conversation) is not None
    verdicts = done.get('verdicts') if isinstance(done, dict) and isinstance(done.get('verdicts'), dict) else {}
    print(json.dumps({
        's0Run': runs[0], 'conversationUuidShape': shaped,
        'conversationSha256': hashlib.sha256(conversation.encode()).hexdigest() if shaped else None,
        'exitSha256': hashlib.sha256(raw).hexdigest(),
        'exitKeys': sorted(k for k in done if k in EXIT_KEYS) if isinstance(done, dict) else [],
        'exitUnknownKeys': sum(1 for k in done if k not in EXIT_KEYS) if isinstance(done, dict) else None,
        'verdictKeys': sorted(k for k in verdicts if k in VERDICT_KEYS),
        'verdictUnknownKeys': sum(1 for k in verdicts if k not in VERDICT_KEYS),
        'runnerReleased': kind(done, 'runnerReleased'), 'privateRemoved': kind(done, 'privateRemoved')},
        sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
