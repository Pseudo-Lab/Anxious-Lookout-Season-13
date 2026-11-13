"""#68 G5 restart (G5 design r2 §4 step 7, the second half of the (a′) fix evidence): the gen 13 model-free start of
owner …5805's runner after the G5 turn, on the development VM only (S1). A separate unit with its own go, after the
g5-turn unit and its post b2 are accepted. Root on the guest:

    python3 -I -B /root/native68/g5-restart.py check <X>   # read-only: changes nothing, writes no file
    systemd-run … python3 -I -B /root/native68/g5-restart.py g5r <X>   # once

<X> is the reviewed sha256 of /root/native68/g5-expected.json.

What it shows: after a turn that went through thread/start (gen 12), the next start (gen 13) passes check_local and
the policy checks: no runner_policy_check line (so no `local.codex_home_config` refusal), the recorded placeholder
matched (start, start, launch on the G3a ino), and CODEX_HOME/config.toml and rules absent with the E3 kept file, at
the precheck, every read and after the natural idle stop. Together with g5-turn's B1 / B2 this is the (a′) fix
evidence (G5 design §0 / §4.4).

What it is: a wrapper over the pinned g5-start.py r2 (2856cd29…, committed 05533a7; its main never called), read
once, its sha256 checked, the hashed bytes compiled. Everything of g5-start.py is used unchanged (its loader of
g3-start.py / v6pre-turns.py / v6-turns.py, wire, pre_g5, action_g5s, check_g5, the read hooks and the CODEX_HOME
judge), except these module names, replaced before wire: GEN_G5 = 13 (so its precheck expects gen 12, the entry and
the idle wait are gen 13), ACTION = 'g5r', guard_g5 (below), reference_g5 (below), open_evidence_g5 (below; the
directory evidence/g5-restart-g5r-<UTC>), SCRIPT_SHA (this file's own sha256), and after wire the loaded g3-start's
ledgers_ok (r1, review g5-turn-r0 major 1: g5-start pins the ledgers BEFORE the turn; ledgers_ok_r verifies the
ledgers AFTER it: resources-6 at its pin, isolation-42 = the turn's recorded ledgersAfter and = its ledgersBefore plus
only the turn's own v6 / G5 / settled claim; used at the precheck and after the idle stop). g5-start.py's own text stays: the
run record's pass key is `g5s` and its verdict says "not evidence of the fix (no thread/start)" about this start
itself; the evidence here is that this start came after the turn's thread/start (the guard requires it).
Guard (rc 3): no g5-restart-* run; exactly one g5-turn-* run whose exit rc is 0 or 14, whose ledger rule held (r1) and
whose run.json has B1 and
B2 with no counterexample and no CODEX_HOME problem (the turn ran and the file stayed absent).
Previous Pod: the g5-turn run's recorded Pod (the gen 12 Pod).
rc: g5-start.py's (3 refused before any change; 4 entry / Pod / start / read failed, a CODEX_HOME problem at a read
or after the idle stop, an idle-wait failure; 7; 70; 11; 12; 9; 130). A nonzero rc is never rerun.
"""
import hashlib
import json
import os
import re
import sys
import time
import types

G5S_PATH, G5S_SHA = '/root/native68/g5-start.py', '2856cd2962e0a95dbdc27440946ce9e6b671fab515dfddab8bc73608581d3920'
GEN, ACTION = 13, 'g5r'
# r1: resources-6 is not touched by the G5 turn (post g5s value, as in g5-turn.py LEDGER_PINS)
RES6_PIN = '51e7ae651e8dcb79213082362fef2bd6873c11f2000b9567719974c61b6eafeb'
TURN_RUN = r'g5-turn-\d{8}T\d{6}Z'
RESTART_RUN = r'g5-restart-g5r-\d{8}T\d{6}Z'

G5S = None


def load_pinned(path, sha, name):
    data = open(path, 'rb').read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit('%s is not %s; nothing done' % (path, sha[:8]))
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(data, path, 'exec'), module.__dict__)
    return module


def turn_record():
    turns = G5S.runs(TURN_RUN)
    if len(turns) != 1:
        return None, None
    try:
        return turns[0], G5S.G3.run_record(turns[0])
    except (OSError, ValueError):
        return turns[0], None


def guard_r(action):
    P = G5S.P
    if action != ACTION:
        raise P.Stop(3, 'only g5r exists here')
    if G5S.runs(RESTART_RUN):
        raise P.Stop(3, 'g5r already ran; a second run is a PM decision')
    name, rec = turn_record()
    ok = (rec is not None and P.exit_rc(name) in (0, 14) and rec.get('ledgerCheck') == "only this run's claim"
          and all(isinstance(rec.get(b), dict) and rec[b].get('counterexample') is None
                  and rec[b].get('codexProblem') is None for b in ('B1', 'B2')))
    if not ok:
        raise P.Stop(3, 'g5r needs exactly one g5-turn run (rc 0 or 14, its ledger rule held) with B1 and B2 clean, '
                        'CODEX_HOME included')


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def ledgers_ok_r():
    """r1 (review g5-turn-r0 major 1): replaces the pinned g3-start ledgers_ok (its pins are the ledgers BEFORE the
    turn) for the restart's precheck and its after-idle check. The ledgers after the G5 turn, verified, never re-pinned
    from the current files: resources-6 at its raw pin and canonically equal to the turn's ledgersBefore; isolation-42
    canonically equal to the turn's recorded ledgersAfter, and differing from its ledgersBefore only by the turn's own
    claim (the recorded g5Claim, present exactly once, v6 / G5 / settled, run = the turn's evidence directory)."""
    P = G5S.P
    name, rec = turn_record()
    if not isinstance(rec, dict):
        return False
    try:
        raw = {b: open(P.LEDGERS[b][0], 'rb').read() for b in ('isolation-42', 'resources-6')}
        iso, res = json.loads(raw['isolation-42']), json.loads(raw['resources-6'])
    except (OSError, ValueError, KeyError):
        return False
    before, after, claim_id = rec.get('ledgersBefore') or {}, rec.get('ledgersAfter') or {}, rec.get('g5Claim')
    if hashlib.sha256(raw['resources-6']).hexdigest() != RES6_PIN or not isinstance(iso, dict):
        return False
    if canonical(res) != (before.get('resources-6') or {}).get('canonical'):
        return False
    if canonical(iso) != (after.get('isolation-42') or {}).get('canonical'):
        return False
    mine = [c for c in iso.get('claims', []) if isinstance(c, dict) and c.get('id') == claim_id]
    if not claim_id or len(mine) != 1:
        return False
    if (mine[0].get('allocation'), mine[0].get('item'), mine[0].get('status'), mine[0].get('run')) != (
            'v6', 'G5', 'settled', name):
        return False
    rest = dict(iso, claims=[c for c in iso['claims'] if not (isinstance(c, dict) and c.get('id') == claim_id)])
    return canonical(rest) == (before.get('isolation-42') or {}).get('canonical')


def reference_r():
    """(ino, before, previous Pod): the G3a reference and the g5-turn run's Pod (the gen 12 Pod)."""
    G3, P = G5S.G3, G5S.P
    g3a = G3.g3_runs('g3a')
    name, rec = turn_record()
    try:
        ino, before = G3.g3a_reference(G3.run_record(g3a[0])) if len(g3a) == 1 else (None, None)
    except (OSError, ValueError, AttributeError):
        ino = None
    pod = rec.get('pod') if isinstance(rec, dict) else None
    if ino is None or not pod:
        raise P.Stop(3, 'the G3a / g5-turn reference is incomplete; nothing changed')
    return ino, before, pod


def open_evidence_r(action):
    P, G3 = G5S.P, G5S.G3
    ev = os.path.join(P.EVIDENCE, 'g5-restart-%s-%s' % (action, time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())))
    os.mkdir(ev, 0o700)
    fd = os.open(os.path.join(ev, 'out.txt'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    P.OUT['stream'] = os.fdopen(fd, 'w')
    P.record(ev, action=action, started=P.now_utc(), script=G5S.SCRIPT_SHA, g5s=G5S_SHA, g3=G5S.G3_SHA,
             base=G3.BASE_SHA, v6=G3.V6_SHA, expected=G3.EXPECTED['sha'], image=P.RUNNER_REF)
    P.say('evidence: %s (script %s, g5s %s, g3 %s)' % (ev, G5S.SCRIPT_SHA[:8], G5S_SHA[:8], G5S.G3_SHA[:8]))
    return ev


def main(argv):
    global G5S
    if len(argv) != 3 or argv[1] not in ('check', ACTION):
        print('usage: g5-restart.py check|g5r <sha256 of g5-expected.json>')
        return 2
    if os.geteuid() != 0:
        print('run as root')
        return 2
    own = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    try:  # a pin or expected-file refusal before the dispatcher: rc 3, nothing done
        G5S = load_pinned(G5S_PATH, G5S_SHA, 'g5_start')
        G5S.GEN_G5, G5S.ACTION, G5S.SCRIPT_SHA = GEN, ACTION, own
        G5S.guard_g5, G5S.reference_g5, G5S.open_evidence_g5 = guard_r, reference_r, open_evidence_r
        G5S.G3 = G5S.load_pinned(G5S.G3_PATH, G5S.G3_SHA, 'g3_start')
        G5S.G3.EXPECTED_FILE = G5S.EXPECTED_FILE
        image, plan = G5S.G3.load_expected(argv[2])
        G5S.wire(image, plan, argv[2])
        G5S.G3.ledgers_ok = ledgers_ok_r  # r1: the verified post-turn ledgers (precheck and after the idle stop)
    except SystemExit as refused:
        print(refused)
        return 3
    P = G5S.P
    if argv[1] == 'check':
        try:
            return G5S.check_g5()
        except P.Stop as stop:
            P.say('check stopped: %s' % stop.message)
            return stop.code
    return P.main(argv[:2])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
