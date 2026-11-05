"""#61 dev-VM gate W4 (observation): read-only R1 verdict for the …5804 runner that w4-enter.py started.
Run by back (root in the guest), after the idle interval, as often as needed until the deadline:

    python3 -I /root/issue61/w4-observe.py /root/issue61/evidence/w4-<stamp> [--wait]

Never changes anything: SELECTs inside BEGIN READ ONLY, kubectl get/logs. --wait polls every 30 s until a verdict
or the deadline; every lookup and sleep is capped by the remaining wait budget; the deadline is never extended.

Entry evidence certifies only if run.json shows: result 0 (or 70: only the session revoke unproved), proof.ok
true, the POST answered 200/202 with a UUID Idempotency-Key, a Pod UID, Pod start, and deadline == Pod start +
60 min (recomputed here), and the entry conversation snapshot. Otherwise the observation runs and prints, but is
diagnostic only (rc 6): failed or incomplete entry evidence never certifies the new-image R1 gate.

R1 done (rc 0) only if ALL hold:
  - the …5804 stop of generation 4 is completed, AT OR BEFORE the deadline, for this Pod UID; its receipt is
    version 3 with ownerId/stateId/generation/podUid/stopId of exactly that stop and unavailable.reason ==
    "auth_revoked" (accepted by the controller only after /health of this runner named the same failure);
  - its proof names this Pod UID, evidence "pod-deleted", verified at or before the deadline;
  - no open stop for …5804; the registry idle_stopped at generation 4 on the same state without a Pod;
  - NO Pod named runner-…5804 exists (a replacement is reported separately);
  - no conversation changed against the entry snapshot.
R1 not done (rc 4): a completed stop that fails any of these (another receipt version, another linkage, a late
  completion: kept and printed as evidence), a live Pod, an open stop, changed conversations.
Pending (rc 5): no completed stop yet; at or after the deadline this is final: "R1 not done at the deadline"
  (nothing is extended, retried or reclaimed; PM decides).
rc 6: entry evidence does not certify (diagnostic observation printed). rc 3: unreadable evidence or lookup.
"""
import calendar
import json
import os
import re
import subprocess
import sys
import time
import uuid

OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
GENERATION = 4
POD_NAME = 'runner-' + OWNER.replace('-', '')
K3S = '/usr/local/bin/k3s'
DEADLINE_MINUTES = 60
POLL = max(1, min(30, int(os.environ.get('W4_POLL_SECONDS', 30))))  # the harness may only shorten it
BUDGET = {'until': None}  # --wait: lookups are capped by the time left (at least 10 s for the final reading)


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def say(text):
    try:
        print(text, flush=True)
    except Exception:
        pass


def run(cmd, timeout=60, stdin=None):
    if BUDGET['until'] is not None:
        timeout = min(timeout, max(10, BUDGET['until'] - time.time()))
    try:
        done = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout, check=False)
    except Exception:
        return 124, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip()


def one(tag, body):
    text = 'BEGIN READ ONLY;\n/*w4o:%s*/ %s\nCOMMIT;\n' % (tag, body)
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres',
                   '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], 60, text.encode())
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        rows = None
    if rc != 0 or rows is None or len(rows) != 1 or not isinstance(rows[0], dict):
        raise Stop(3, 'database lookup failed (%s)' % tag)
    return rows[0]


STATE_SQL = ("SELECT json_build_object('state',r.state_id,'phase',r.phase,'generation',r.generation,"
             "'revision',r.revision,'pod',r.pod_uid,"
             "'stop',(SELECT json_build_object('stopId',s.stop_id,'owner',s.owner_id,'stateId',s.state_id,"
             "'generation',s.generation,'pod',s.pod_uid,'completed',s.completed_at,"
             "'completedEpoch',extract(epoch FROM s.completed_at),'receipt',s.receipt,'proof',s.proof) "
             "FROM runner_termination.stops s WHERE s.owner_id=r.owner_id AND s.generation=%d),"
             "'openStops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id "
             "AND s.completed_at IS NULL)) FROM runner_runtime.registry r WHERE r.owner_id='%s';" % (GENERATION, OWNER))
CONV_SQL = ("SELECT json_build_object('n',count(*),'digest',coalesce(md5(string_agg(id::text||':'||state||':'||"
            "version::text,',' ORDER BY id)),'-')) FROM research.conversations;")


def utc(text):
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


def iso_epoch(text):
    """Seconds of an ISO-8601 time with an explicit UTC offset of +00:00 (the controller's isoformat)."""
    match = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?\+00:00', text or '')
    if not match:
        return None
    return utc(match.group(1) + 'Z') + float(match.group(2) or 0)


def load_entry(path):
    """(entry, certifying, reasons). Raises Stop(3) when nothing usable is recorded."""
    try:
        with open(os.path.join(path, 'run.json')) as stream:
            entry = json.load(stream)
        pod = entry['pod']
        pod_uid, start, deadline = pod['uid'], pod['start'], pod['deadline']
        uuid.UUID(pod_uid)
        before = entry['before']['conversations']
        if not (isinstance(before, dict) and set(before) == {'n', 'digest'}):
            raise ValueError
    except Exception:
        raise Stop(3, 'entry evidence unreadable (no Pod, deadline and conversation snapshot recorded)') from None
    reasons = []
    if entry.get('result') not in (0, 70):
        reasons.append('entry result %s (not 0/70)' % entry.get('result'))
    if (entry.get('proof') or {}).get('ok') is not True:
        reasons.append('entry instance/image proof not ok')
    post = entry.get('post') or {}
    try:
        uuid.UUID(post.get('idempotencyKey') or '')
    except ValueError:
        reasons.append('no Idempotency-Key recorded')
    if post.get('status') not in (200, 202):
        reasons.append('POST status %s' % post.get('status'))
    try:
        if utc(deadline) != utc(start) + DEADLINE_MINUTES * 60:
            reasons.append('deadline is not Pod start + %d min' % DEADLINE_MINUTES)
    except Exception:
        reasons.append('Pod start/deadline unreadable')
    return entry, not reasons, reasons


def observe(entry):
    pod_uid, deadline = entry['pod']['uid'], utc(entry['pod']['deadline'])
    state = one('state', STATE_SQL)
    rc, out = run([K3S, 'kubectl', '-n', 'codex-runner', 'get', 'pod', POD_NAME, '-o',
                   'jsonpath={.metadata.uid}', '--ignore-not-found'])
    if rc != 0:
        raise Stop(3, 'pod lookup failed')
    live_pod = out or None
    conv = one('conv', CONV_SQL)
    conv = {'n': conv.get('n'), 'digest': conv.get('digest')}
    stop = state.get('stop') or {}
    receipt, proof = stop.get('receipt') or {}, stop.get('proof') or {}
    seen = {'registry': {k: state.get(k) for k in ('state', 'phase', 'generation', 'revision', 'pod', 'openStops')},
            'stop': {k: stop.get(k) for k in ('stopId', 'pod', 'completed')},
            'receipt': {k: receipt.get(k) for k in ('version', 'ownerId', 'stateId', 'generation', 'podUid', 'stopId',
                                                    'unavailable')},
            'proof': {k: proof.get(k) for k in ('podUid', 'evidence', 'verifiedAt')},
            'livePod': live_pod, 'conversations': conv}
    if not stop.get('completed'):
        return 'pending', seen, []
    problems = []
    if receipt.get('version') != 3:
        problems.append('receipt version %s (not the R1 v3 receipt)' % receipt.get('version'))
    if (receipt.get('unavailable') or {}).get('reason') != 'auth_revoked':
        problems.append('unavailable reason %s' % (receipt.get('unavailable') or {}).get('reason'))
    if (stop.get('owner'), stop.get('stateId'), stop.get('generation'), stop.get('pod')) != \
            (OWNER, STATE, GENERATION, pod_uid):
        problems.append('the generation-%d stop is not this owner/state/Pod' % GENERATION)
    if (receipt.get('ownerId'), receipt.get('stateId'), receipt.get('generation'), receipt.get('podUid'),
            receipt.get('stopId')) != (OWNER, STATE, GENERATION, pod_uid, stop.get('stopId')):
        problems.append('receipt linkage (owner/state/generation/Pod/stopId) differs from the stop')
    if proof.get('podUid') != pod_uid or proof.get('evidence') != 'pod-deleted':
        problems.append('proof is not pod-deleted for this Pod')
    completed = stop.get('completedEpoch')
    verified = iso_epoch(proof.get('verifiedAt'))
    if not isinstance(completed, (int, float)) or completed > deadline:
        problems.append('stop completed %s, after the deadline %s (kept as evidence)'
                        % (stop.get('completed'), entry['pod']['deadline']))
    if verified is None or verified > deadline:
        problems.append('proof verified %s, not at or before the deadline' % proof.get('verifiedAt'))
    if state.get('openStops') != 0:
        problems.append('%s open stop(s) for …5804' % state.get('openStops'))
    if (state.get('state'), state.get('phase'), state.get('generation'), state.get('pod')) != \
            (STATE, 'idle_stopped', GENERATION, None):
        problems.append('registry is not idle_stopped gen %d without a Pod' % GENERATION)
    if live_pod is not None:
        problems.append('a Pod named %s exists (%s)' % (POD_NAME, 'the W4 Pod' if live_pod == pod_uid
                                                         else 'a REPLACEMENT, uid %s' % live_pod))
    if conv != entry['before']['conversations']:
        problems.append('conversations changed since the entry snapshot')
    return ('done' if not problems else 'not-done'), seen, problems


def controller_lines():
    rc, out = run([K3S, 'kubectl', '-n', 'runner-control', 'logs', 'deployment/runner-controller', '--tail=400'])
    lines = []
    for line in out.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get('owner') == OWNER and set(value) == {'owner', 'stage', 'result'}:
            lines.append(value['result'])
    return lines if rc == 0 else None


def main(argv):
    try:
        if len(argv) not in (2, 3) or (len(argv) == 3 and argv[2] != '--wait'):
            raise Stop(3, 'usage: w4-observe.py <w4 evidence dir> [--wait]')
        wait = len(argv) == 3
        entry, certifying, reasons = load_entry(argv[1])
        deadline = utc(entry['pod']['deadline'])
        say('W4 Pod %s; deadline %s; entry evidence %s' % (entry['pod']['uid'], entry['pod']['deadline'],
                                                           'certifies' if certifying else
                                                           'DOES NOT certify (%s): diagnostic only' % '; '.join(reasons)))
        if wait:
            BUDGET['until'] = deadline
        while True:
            verdict, seen, problems = observe(entry)
            at = time.time()
            say('observed %s: %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(at)),
                                     json.dumps(seen, sort_keys=True, default=str)))
            if verdict != 'pending' or not wait or at >= deadline:
                break
            time.sleep(max(1, min(POLL, deadline - at)))
        lines = controller_lines()
        say('controller outcome lines for …5804 (last 400 log lines): %s'
            % ('lookup failed' if lines is None else ' -> '.join(lines) or 'none'))
        if verdict == 'not-done':
            raise Stop(4 if certifying else 6, 'R1 not done: %s' % '; '.join(problems))
        if verdict == 'pending':
            if time.time() >= deadline:
                raise Stop(5 if certifying else 6, 'R1 not done at the deadline: no completed stop; nothing is extended, '
                                                   'retried or reclaimed; PM decides')
            raise Stop(5 if certifying else 6, 'pending: no completed stop yet (deadline %s)' % entry['pod']['deadline'])
        if not certifying:
            raise Stop(6, 'the stop matches, but the entry evidence does not certify the new-image R1 gate '
                          '(diagnostic only): %s' % '; '.join(reasons))
        say('R1 done: v3 receipt auth_revoked for this Pod and stop, pod-deleted proof, both before the deadline, '
            'released, no Pod by name, conversations unchanged')
        return finish(0)
    except Stop as stop:
        say(stop.message)
        return finish(stop.code)
    except Exception:
        say('unexpected error (nothing concluded)')
        return finish(3)


def finish(code):
    say('result rc=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
