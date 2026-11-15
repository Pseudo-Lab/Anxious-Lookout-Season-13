"""#90 P5 r7 pins (PM 2026-11-15: if the hop ② look confirms the #62 host-ingress block, an S5 / S6 round of at most 2
turns follows), development VM only, READ ONLY: the pins an r7 continuation must bind. Nothing is written. Run as root
on the guest with the reviewed bytes on stdin:

    sudo -n python3 -I -B - < i90-p5-r7-pins.py

Output, JSON lines, fixed keys only (`pins`): the turns-r6 and disable-r6 evidence run names (fixed patterns; the
evidence names, which may be one second after the unit names), the sha256 of their exit.json bytes, their rc, their
typed fields from fixed lists (rc, stepRc, stoppedAt, verdicts, the four cleanup fields: value or `null` or `missing`),
and the budget entries (n, step, outcome from fixed lists). Same reading rules as i90-p5-r6-pins.py (r6a), without the
Service listing.
rc: 0 printed; 3 refused (not root).
"""
import hashlib
import json
import os
import re
import stat
import sys

EVIDENCE = '/root/issue90/evidence'
STEPS = ('S0', 'S1', 'S2', 'S3', 'S4a', 'S4b', 'S5', 'S6')
OUTCOMES = ('accepted', 'reserved', 'send raised')
ACTIONS = ('turns-r6', 'disable-r6')


def out(kind, **fields):
    print(json.dumps({'kind': kind, **fields}, sort_keys=True), flush=True)


def read(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
        raise OSError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or os.fstat(stream.fileno()).st_ino != info.st_ino:
            raise OSError()
        return stream.read(1 << 20)


def typed(value):
    """A field as JSON type and value, for fixed scalars only (review: the decision binds typed fields)."""
    if value is None:
        return 'null'
    if value is True or value is False:
        return value
    if type(value) is int:
        return value
    return 'other'


def fields(done):
    """The exit's fields from fixed lists: present or missing, typed; stoppedAt only as a fixed step name."""
    if not isinstance(done, dict):
        return None
    out_ = {}
    for key in ('rc', 'stepRc', 'runnerReleased', 'clientsGone', 'privateRemoved', 'sessionRevoked'):
        out_[key] = typed(done[key]) if key in done else 'missing'
    stopped = done.get('stoppedAt', 'missing')
    out_['stoppedAt'] = stopped if stopped in STEPS or stopped == 'missing' else ('null' if stopped is None else 'other')
    verdicts = done.get('verdicts') if isinstance(done.get('verdicts'), dict) else None
    out_['verdicts'] = None if verdicts is None else {k if k in STEPS + ('release',) else 'other': typed(v)
                                                      for k, v in verdicts.items()}
    return out_


def pins():
    value = {}
    for action in ACTIONS:
        found = sorted(x for x in os.listdir(EVIDENCE) if re.fullmatch(r'i90-p5-%s-\d{8}T\d{6}Z' % action, x))
        if len(found) != 1:
            value[action] = {'runs': len(found)}
            continue
        try:
            raw = read(os.path.join(EVIDENCE, found[0], 'exit.json'))
            done = json.loads(raw)
            rc = done.get('rc') if isinstance(done, dict) and type(done.get('rc')) is int else None
        except Exception:
            raw, rc, done = None, None, None
        value[action] = {'run': found[0], 'exitSha256': hashlib.sha256(raw).hexdigest() if raw is not None else None,
                         'rc': rc, 'fields': fields(done)}
    try:
        turns = json.loads(read(os.path.join(EVIDENCE, 'i90-p5-budget.json'))).get('turns')
        value['budget'] = [{'n': t.get('n') if type(t.get('n')) is int else None,
                            'step': t.get('step') if t.get('step') in STEPS else 'unknown',
                            'outcome': t.get('outcome') if t.get('outcome') in OUTCOMES else 'other'}
                           for t in turns if isinstance(t, dict)]
    except Exception:
        value['budget'] = None
    out('pins', **value)


def main():
    if os.geteuid() != 0:
        out('refused', reason='not root')
        return 3
    pins()
    return 0


if __name__ == '__main__':
    sys.exit(main())
