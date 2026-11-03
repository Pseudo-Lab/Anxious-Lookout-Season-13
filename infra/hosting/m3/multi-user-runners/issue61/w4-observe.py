"""#61 dev-VM gate W4 (observation): read-only R1 verdict for the …5804 runner that w4-enter.py started.
Run by back (root in the guest), after the idle interval, as often as needed until the deadline:

    python3 -I /root/issue61/w4-observe.py /root/issue61/evidence/w4-<stamp> [--wait]

Reads the entry evidence (run.json: Pod UID, Pod start, deadline, conversation snapshot) and the live state. It
never changes anything: SELECTs inside BEGIN READ ONLY, kubectl get/logs, and nothing else. --wait polls every
30 s until a verdict or the deadline (Pod start + 60 min); it never extends the deadline.

R1 done (rc 0) only if ALL hold:
  - the stop of …5804 for generation 4 is completed with a version 3 receipt for this Pod UID, its
    unavailable.reason == "auth_revoked" (the controller accepted it only after /health of this runner named the
    same failure: accept_unavailable / health_failure_matches);
  - its proof names this Pod UID with evidence "pod-deleted";
  - the registry is idle_stopped at generation 4 on the same state, without a Pod; the Pod (by name) is gone;
  - no conversation changed against the entry snapshot.
R1 not done (rc 4): the stop completed with any other receipt (v1 clean drain, v2 terminal, v4 operator), or for
  another Pod, or the release does not match; or conversations changed.
Pending (rc 5): no completed stop yet. At or after the deadline this is final: "R1 not done at the deadline",
  with the live state printed; nothing is extended, retried or reclaimed (PM decides).
rc 3: the evidence or a lookup could not be read (nothing concluded).
"""
import calendar
import json
import os
import subprocess
import sys
import time

OWNER = '00000000-0000-4000-8000-000000005804'
STATE = '5ff583e9-abe1-4ef8-b277-4a5afead6b80'
GENERATION = 4
POD_NAME = 'runner-' + OWNER.replace('-', '')
K3S = '/usr/local/bin/k3s'
POLL = max(1, min(30, int(os.environ.get('W4_POLL_SECONDS', 30))))  # the harness may only shorten it


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
             "'stop',(SELECT json_build_object('stopId',s.stop_id,'pod',s.pod_uid,'completed',s.completed_at,"
             "'receipt',s.receipt,'proof',s.proof) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id "
             "AND s.generation=%d),"
             "'openStops',(SELECT count(*) FROM runner_termination.stops s WHERE s.owner_id=r.owner_id "
             "AND s.completed_at IS NULL)) FROM runner_runtime.registry r WHERE r.owner_id='%s';" % (GENERATION, OWNER))
CONV_SQL = ("SELECT json_build_object('n',count(*),'digest',coalesce(md5(string_agg(id::text||':'||state||':'||"
            "version::text,',' ORDER BY id)),'-')) FROM research.conversations;")


def observe(entry):
    pod_uid = entry['pod']['uid']
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
            'stop': {'stopId': stop.get('stopId'), 'pod': stop.get('pod'), 'completed': stop.get('completed'),
                     'receiptVersion': receipt.get('version'), 'unavailable': receipt.get('unavailable'),
                     'proofPod': proof.get('podUid'), 'evidence': proof.get('evidence')},
            'livePod': live_pod, 'conversations': conv}
    if not stop.get('completed'):
        return 'pending', seen, []
    problems = []
    if receipt.get('version') != 3:
        problems.append('receipt version %s (not the R1 v3 receipt)' % receipt.get('version'))
    if (receipt.get('unavailable') or {}).get('reason') != 'auth_revoked':
        problems.append('unavailable reason %s' % (receipt.get('unavailable') or {}).get('reason'))
    if stop.get('pod') != pod_uid or receipt.get('podUid') != pod_uid:
        problems.append('stop/receipt for another Pod')
    if proof.get('podUid') != pod_uid or proof.get('evidence') != 'pod-deleted':
        problems.append('proof is not pod-deleted for this Pod')
    if (state.get('state'), state.get('phase'), state.get('generation'), state.get('pod')) != \
            (STATE, 'idle_stopped', GENERATION, None):
        problems.append('registry is not idle_stopped gen %d without a Pod' % GENERATION)
    if live_pod == pod_uid:
        problems.append('the Pod still exists')
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
        try:
            with open(os.path.join(argv[1], 'run.json')) as stream:
                entry = json.load(stream)
            deadline = calendar.timegm(time.strptime(entry['pod']['deadline'], '%Y-%m-%dT%H:%M:%SZ'))
            pod_uid = entry['pod']['uid']
            entry['before']['conversations']
        except Exception:
            raise Stop(3, 'entry evidence unreadable (no proved Pod recorded)')
        say('W4 Pod %s; deadline %s' % (pod_uid, entry['pod']['deadline']))
        while True:
            verdict, seen, problems = observe(entry)
            at = time.time()
            say('observed %s: %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(at)),
                                     json.dumps(seen, sort_keys=True, default=str)))
            if verdict != 'pending' or len(argv) == 2 or at >= deadline:
                break
            time.sleep(max(1, min(POLL, deadline - at)))
        lines = controller_lines()
        say('controller outcome lines for …5804 (last 400 log lines): %s'
            % ('lookup failed' if lines is None else ' -> '.join(lines) or 'none'))
        if verdict == 'done':
            say('R1 done: v3 receipt auth_revoked for this Pod, pod-deleted proof, released, conversations unchanged')
            return finish(0)
        if verdict == 'not-done':
            raise Stop(4, 'R1 not done: %s' % '; '.join(problems))
        if time.time() >= deadline:
            raise Stop(5, 'R1 not done at the deadline: no completed stop; nothing is extended, retried or reclaimed; '
                          'PM decides')
        raise Stop(5, 'pending: no completed stop yet (deadline %s)' % entry['pod']['deadline'])
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
