"""#61 dev-VM gate W3: R2 operator reclaim of the blocked …5804 runner, run once by the user as root in the guest.

    python3 -I /root/issue61/w3-reclaim.py --expected-running N

N is the number of …5804 `running` conversations the user's go was given with (W0: 0). R2 fails those with
codex_runner_lost; if the number differs now, W3 refuses before anything runs and prints the current one, so a
new go can be given with it.

1. precheck (read-only): W2 finished cleanly (no pending/uncertain attempt) and runner-controller is the
   Deployment W2 created and Ready; …0048/…5802 already released by the dead-runner path; …5804's registry on
   Pod b9bbc857 (runner_unreachable/stopping) with no receipt; the live Pod b9bbc857 Running with its runner
   container running; …5804's running conversations == N.
2. reclaim: exactly the reviewed CLI inside the controller Pod; its JSON line is parsed (stage, result, owner,
   podUid, stopId) and printed unchanged.
3. release check, read-only, with a wall-clock deadline (600 s; every lookup timeout and sleep is capped by the
   time left): Pod b9bbc857 gone, registry idle_stopped without a Pod, and the stop with the reclaim's stopId
   completed with a v4 receipt and proof {podUid b9bbc857, evidence pod-deleted}.

rc: 0 reclaimed and released; 3 precheck refused, nothing ran; 4 reclaim refused before opening a stop (nothing
changed); 5 reclaim failed or its answer is unusable (do not rerun; back checks); 6 accepted but not proved
released before the deadline (do not rerun; back checks); 7 reclaim refused AFTER opening a stop (code and
stopOpenedByThisRun printed): no R2 receipt was written, but the stop intent exists and the runner may have
drained normally (drain_accepted), so the read-only release check runs for that stop with the normal-path
receipts (v1 clean drain or v3 unavailable drain) and prints "normal release confirmed=True|False"; never rerun
reclaim on rc 7 without back's read-only check.
Fixed messages only: no DSN, token or command error text.
"""
import json
import re
import os
import subprocess
import sys
import time

K3S = '/usr/local/bin/k3s'
OWNER_0048 = '00000000-0000-4000-8000-000000000048'
OWNER_5802 = '00000000-0000-4000-8000-000000005802'
OWNER_5804 = '00000000-0000-4000-8000-000000005804'
POD_5804 = 'b9bbc857-cd36-4a7b-89b5-1ca7c7d3b61b'
NAME_5804 = 'runner-' + OWNER_5804.replace('-', '')
EVIDENCE = '/root/issue61/evidence'
WAIT = 600
# Only shortens the wait (harness); never longer than WAIT.
WAIT = max(1, min(WAIT, int(os.environ.get('W3_WAIT_SECONDS', WAIT))))
AFTER_OPEN = {'drain_accepted', 'drain_status', 'drain_unreachable'}  # refusals after request_stop


class Stop(Exception):
    def __init__(self, rc, message):
        super().__init__(message)
        self.rc, self.message = rc, message


def say(text):
    print(text, flush=True)


def run(args, timeout, stdin=None):
    """(rc, stdout) of a fixed command; rc 124 on timeout or failure to start. Output is never echoed raw."""
    try:
        done = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return 124, ''
    except Exception:
        return 124, ''
    return done.returncode, done.stdout.decode('utf-8', 'replace')


def kget(kind, ns, name, timeout=30):
    rc, out = run([K3S, 'kubectl', '-n', ns, 'get', kind, name, '-o', 'json', '--ignore-not-found'], timeout)
    if rc != 0:
        return 'failed'
    return json.loads(out) if out.strip() else None


def rows(tag, query, timeout=60):
    """JSON rows (one json_build_object per line) of a READ ONLY query, or None if the lookup failed."""
    text = 'BEGIN READ ONLY;\n/*w3:%s*/ %s\nCOMMIT;\n' % (tag, query)
    rc, out = run([K3S, 'kubectl', '-n', 'm2-hosting', 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres',
                   '-d', 'hosting', '-Atq', '-v', 'ON_ERROR_STOP=1', '-f', '-'], timeout, stdin=text.encode())
    if rc != 0:
        return None
    try:
        parsed = [json.loads(line) for line in out.splitlines() if line.strip()]
    except ValueError:
        return None
    return parsed if all(isinstance(row, dict) for row in parsed) else None  # Anything else: lookup failed.


def precheck(expect):
    if os.geteuid() != 0:
        raise Stop(3, 'precheck: not root')
    # The live W2: the one evidence directory that was not rolled back, finished cleanly and recorded the
    # controller Deployment it created (a rolled-back or partial W2 never qualifies).
    dirs = sorted(d for d in os.listdir(EVIDENCE) if d.startswith('w2-')) if os.path.isdir(EVIDENCE) else []
    live = []
    for d in dirs:
        started = os.path.exists(os.path.join(EVIDENCE, d, 'rollback-started.json'))
        if os.path.exists(os.path.join(EVIDENCE, d, 'rolled-back.json')):
            continue  # certified complete rollback
        if started:
            raise Stop(3, 'precheck: W2 evidence %s has a started but uncertified rollback; back checks' % d)
        try:
            with open(os.path.join(EVIDENCE, d, 'changes.json')) as stream:
                changes = json.load(stream)
            uid = changes['created'].get('Deployment runner-control/runner-controller')
            clean = not changes.get('pending') and not changes['uncertain'] and not changes['creationUncertain']
        except Exception:
            raise Stop(3, 'precheck: W2 evidence %s unreadable' % d) from None
        if not clean:
            # An unresolved mutation of any W2 run that was not certified rolled back blocks R2.
            raise Stop(3, 'precheck: W2 evidence %s has a pending/uncertain attempt; back checks' % d)
        if uid:
            live.append((d, uid))
    say('W2 evidence: %s; live: %s' % (', '.join(dirs) or 'none', ', '.join(d for d, _ in live) or 'none'))
    if len(live) != 1:
        raise Stop(3, 'precheck: expected exactly one clean W2 run that created runner-controller, found %d' % len(live))
    want = live[0][1]
    ctrl = kget('deployment', 'runner-control', 'runner-controller')
    if ctrl in (None, 'failed') or ctrl['metadata']['uid'] != want or (ctrl.get('status') or {}).get('readyReplicas') != 1:
        raise Stop(3, 'precheck: runner-controller is not the Ready Deployment W2 created (want %s)' % want)
    registry = rows('registry', "SELECT json_build_object('owner',owner_id,'phase',phase,'generation',generation,"
                    "'revision',revision,'pod',pod_uid) FROM runner_runtime.registry WHERE owner_id IN "
                    "('%s','%s','%s') ORDER BY owner_id;" % (OWNER_0048, OWNER_5802, OWNER_5804))
    stops = rows('stops', "SELECT json_build_object('owner',owner_id,'stop',stop_id,'pod',pod_uid,'receipt',"
                 "receipt->>'version') FROM runner_termination.stops WHERE completed_at IS NULL ORDER BY owner_id;")
    running = rows('running', "SELECT json_build_object('running',count(*)) FROM research.conversations "
                   "WHERE owner_id='%s' AND state='running';" % OWNER_5804)
    if registry is None or stops is None or not running:
        raise Stop(3, 'precheck: database lookup failed')
    for row in registry:
        say('registry %s' % json.dumps(row, sort_keys=True))
    for row in stops:
        say('open stop %s' % json.dumps(row, sort_keys=True))
    by = {row['owner']: row for row in registry}
    for owner in (OWNER_0048, OWNER_5802):
        if (by.get(owner) or {}).get('phase') != 'idle_stopped' or by[owner].get('pod') is not None:
            raise Stop(3, 'precheck: %s is not released yet' % owner)
    r = by.get(OWNER_5804) or {}
    if r.get('phase') not in {'runner_unreachable', 'stopping'} or r.get('pod') != POD_5804:
        raise Stop(3, 'precheck: …5804 registry is %s/%s' % (r.get('phase'), r.get('pod')))
    if any(s['owner'] == OWNER_5804 and s.get('receipt') is not None for s in stops):
        raise Stop(3, 'precheck: …5804 stop already has a receipt')
    pod = kget('pod', 'codex-runner', NAME_5804)
    if pod in (None, 'failed'):
        raise Stop(3, 'precheck: …5804 Pod lookup failed or Pod missing')
    states = {c.get('name'): next(iter(c.get('state') or {}), None) for c in pod['status'].get('containerStatuses', [])}
    if (pod['metadata']['uid'], pod['status'].get('phase'), bool(pod['metadata'].get('deletionTimestamp')),
            states.get('runner')) != (POD_5804, 'Running', False, 'running'):
        raise Stop(3, 'precheck: …5804 Pod is not b9bbc857 Running with its runner container running')
    now = running[0]['running']
    say('…5804 running conversations now: %d (go given for %d); R2 fails them with codex_runner_lost' % (now, expect))
    if now != expect:
        raise Stop(3, 'precheck: running conversations changed since the go (%d, go was for %d); '
                      'ask for a new go with %d' % (now, expect, now))
    return r


def reclaim():
    say('reclaim: %s' % time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    rc, out = run([K3S, 'kubectl', '-n', 'runner-control', 'exec', 'deploy/runner-controller', '--', 'python', '-B',
                   '-m', 'ops.runner_controller_service', 'reclaim', '--owner', OWNER_5804, '--pod-uid', POD_5804], 120)
    lines = [l for l in out.splitlines() if l.startswith('{')]
    try:
        answer = json.loads(lines[-1]) if lines else None
    except ValueError:
        answer = None
    say('reclaim output: %s (rc %d)' % (lines[-1] if lines else 'none', rc))
    if not isinstance(answer, dict) or answer.get('stage') != 'reclaim':
        raise Stop(5, 'reclaim answer unusable: do not rerun; back checks read-only')
    return rc, answer


R2_RECEIPTS = {'4'}            # an accepted reclaim must end with its own operator receipt
NORMAL_RECEIPTS = {'1', '3'}   # after a refusal that opened a stop: a clean drain (v1) or an unavailable drain (v3)


def released(stop_id, deadline, receipts):
    """Read-only, bounded by the wall-clock deadline. Released means: Pod b9bbc857 gone, registry idle_stopped
    without a Pod, and the stop `stop_id` completed with a receipt version in `receipts` and proof
    {podUid b9bbc857, evidence pod-deleted}. Returns (ok, last observation)."""
    seen = {}
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return False, seen
        pod = kget('pod', 'codex-runner', NAME_5804, timeout=min(30, left))
        left = deadline - time.monotonic()
        state = rows('release', "SELECT json_build_object('phase',r.phase,'pod',r.pod_uid,'completed',s.completed_at,"
                     "'receipt',s.receipt->>'version','proofPod',s.proof->>'podUid','evidence',s.proof->>'evidence',"
                     "'boot',s.proof->>'bootId','drain',s.receipt->'operator'->>'drainStatus','reason',"
                     "s.receipt->'operator'->'health'->>'reason') FROM runner_runtime.registry r LEFT JOIN "
                     "runner_termination.stops s ON s.owner_id=r.owner_id AND s.stop_id='%s' WHERE r.owner_id='%s';"
                     % (stop_id, OWNER_5804), timeout=min(60, max(1, left))) if left > 0 else None
        seen = {'pod': 'lookup failed' if pod == 'failed' else (None if pod is None else pod['metadata']['uid']),
                'row': state[0] if state else 'lookup failed'}
        row = state[0] if state else {}
        if pod is None and row.get('phase') == 'idle_stopped' and row.get('pod') is None and row.get('completed') \
                and row.get('receipt') in receipts and row.get('proofPod') == POD_5804 and row.get('evidence') == 'pod-deleted':
            return True, seen
        left = deadline - time.monotonic()
        if left <= 0:
            return False, seen
        time.sleep(min(10, left))


def main(argv):
    try:
        if len(argv) != 3 or argv[1] != '--expected-running' or not re.fullmatch(r'[0-9]{1,6}', argv[2]):
            raise Stop(3, 'usage: w3-reclaim.py --expected-running N (N: the running count the go was given for)')
        precheck(int(argv[2]))
        rc, answer = reclaim()
        if rc == 0 and answer.get('result') == 'accepted':
            if answer.get('owner') != OWNER_5804 or answer.get('podUid') != POD_5804 or not answer.get('stopId'):
                raise Stop(5, 'reclaim accepted for another owner/Pod or without stopId: do not rerun; back checks')
            stop_id, after_open = answer['stopId'], False
        elif rc == 3 and answer.get('result') == 'refused':
            observed = answer.get('observed') or {}
            if answer.get('code') not in AFTER_OPEN:
                say('reclaim refused (%s) before opening a stop: nothing changed' % answer.get('code'))
                raise Stop(4, 'reclaim refused')
            say('reclaim refused (%s) after opening a stop (stopOpenedByThisRun=%s): no R2 receipt was written; '
                'the stop intent exists and the controller may finish it on its normal path'
                % (answer.get('code'), observed.get('stopOpenedByThisRun')))
            stop_id, after_open = observed.get('stopId'), True
            if not stop_id:
                raise Stop(5, 'refusal after opening a stop without stopId: do not rerun; back checks')
        else:
            raise Stop(5, 'reclaim failed (rc %d): do not rerun; back checks read-only' % rc)
        ok, seen = released(stop_id, time.monotonic() + WAIT, NORMAL_RECEIPTS if after_open else R2_RECEIPTS)
        say('release check %s: %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), json.dumps(seen, sort_keys=True)))
        if after_open:
            # Not an R2 success: no operator receipt. Report whether the normal path finished that stop.
            raise Stop(7, 'normal release confirmed=%s (receipt v1/v3, stop completed, Pod deleted); not an R2 '
                          'reclaim; do not rerun reclaim, back checks read-only' % ok)
        if not ok:
            raise Stop(6, 'accepted but not proved released within %d s: do not rerun; back checks read-only' % WAIT)
    except Stop as stop:
        say(stop.message)
        say('result rc=%d' % stop.rc)
        return stop.rc
    except Exception:
        say('unexpected error (no detail printed): do not rerun; back checks read-only')
        say('result rc=5')
        return 5
    say('result rc=0')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
