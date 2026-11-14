"""#90 P5 r6 part a (PM 2026-11-15 after turns-r5 stopped at S4a), development VM only, READ ONLY: (1) the api Services
behind hop ② (why `service_hop()` found "not exactly one api Service"), (2) the pins an r6 continuation must bind.
Nothing is written. Run as root on the guest with the reviewed bytes on stdin:

    sudo -n python3 -I -B - < i90-p5-r6-pins.py

Output, JSON lines, fixed keys only:
- `services`: every Service in m2-hosting whose selector is exactly {app: hosting-api}: its name (only when it is one of
  the fixed names api / api-internal, else `other`), clusterIP present (yes / no / None-headless), and its ports as
  (name from the fixed list http / internal / other, port number);
- `pins`: the turns-r5 and disable-r5 run names (fixed patterns), the sha256 of their exit.json bytes, their rc, their
  typed fields from fixed lists (rc, stepRc, stoppedAt, verdicts, the four cleanup fields: value or null or missing), and
  the budget entries (n, step, outcome from fixed lists).
rc: 0 printed; 3 refused (not root, the Service lookup failed).
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys

EVIDENCE = '/root/issue90/evidence'
K3S = '/usr/local/bin/k3s'
SERVICE_NAMES = ('api', 'api-internal')
PORT_NAMES = ('http', 'internal')
STEPS = ('S0', 'S1', 'S2', 'S3', 'S4a', 'S4b', 'S5', 'S6')
OUTCOMES = ('accepted', 'reserved', 'send raised')


def out(kind, **fields):
    print(json.dumps({'kind': kind, **fields}, sort_keys=True), flush=True)


def read(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
        raise OSError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        return stream.read(1 << 20)


def services():
    done = subprocess.run([K3S, 'kubectl', '-n', 'm2-hosting', 'get', 'services', '-o', 'json'],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False)
    if done.returncode != 0:
        return None
    found = []
    for item in json.loads(done.stdout).get('items', []):
        spec = item.get('spec') or {}
        if spec.get('selector') != {'app': 'hosting-api'}:
            continue
        name = (item.get('metadata') or {}).get('name')
        ip = spec.get('clusterIP')
        found.append({'name': name if name in SERVICE_NAMES else 'other',
                      'clusterIP': 'None-headless' if ip == 'None' else bool(ip),
                      'ports': [{'name': p.get('name') if p.get('name') in PORT_NAMES else 'other',
                                 'port': p.get('port') if type(p.get('port')) is int else None}
                                for p in spec.get('ports') or [] if isinstance(p, dict)]})
    return sorted(found, key=lambda x: x['name'])


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
    for action in ('turns-r5', 'disable-r5'):
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
    try:
        found = services()
    except Exception:
        found = None
    if found is None:
        out('refused', reason='the Service lookup failed')
        return 3
    out('services', selectorHostingApi=found, count=len(found))
    pins()
    return 0


if __name__ == '__main__':
    sys.exit(main())
