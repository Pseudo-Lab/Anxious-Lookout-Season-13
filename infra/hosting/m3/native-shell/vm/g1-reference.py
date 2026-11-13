"""#68 G1 §C reference launch (design g1-r3 r1 §C): a diagnosis-only comparison through the V6x path. Off by default.

Loaded by g1-harness.py from REF_B64, which g1-dotcodex.sh sends only with its second argument `reference`. The
harness checks the sha256 against REF_SHA. It is called at most once per run: after a case's gate failed and after
stop_case ended that case's app-server and collected its exit status. It never changes a verdict, a gate result, a
stop reason or the conclusion. A success is a comparison observation, not a pass.

launch(ctx) runs one
    codex sandbox -P native -C <work> -- python3 -I -c PROBE create <work>/ctl/ref0
- as the harness's own child, in a new session, stdin /dev/null;
- with the case's app-server environment, cwd and uid;
- reading at most CAP bytes of each stream into memory; the rest is read and counted, not kept;
- stopping at TIMEOUT_S with SIGKILL to the launch's process group.

Then it:
- waits for the launch itself, so its exit status goes to Popen.wait, never to the harness's reap_all;
- collects every process the harness inherited as subreaper: each is pinned by pidfd and start time, SIGKILLed and
  reaped, until none is left or GONE_S passes.

Returns (record, clean). clean is False when any process is still there or the end was not confirmed; the harness
then reports contamination. The kept text is only passed to the harness's diagnose(); it is never written.
"""
import os
import select
import signal
import subprocess
import time

CAP = 64 * 1024     # bytes kept per stream (the app-server's 1 MiB cap does not apply to this direct child)
TIMEOUT_S = 30
GONE_S = 10


def read_capped(fds, deadline, cap=CAP):
    """Read every fd to EOF or until the deadline. Keep at most `cap` bytes per fd and count everything read.
    Returns (kept, seen, timed_out)."""
    kept = {fd: bytearray() for fd in fds}
    seen = dict.fromkeys(fds, 0)
    live = set(fds)
    while live:
        left = deadline - time.monotonic()
        if left <= 0:
            return kept, seen, True
        ready, _, _ = select.select(sorted(live), [], [], min(left, 0.5))
        for fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b''
            if not chunk:
                live.discard(fd)
                continue
            seen[fd] += len(chunk)
            room = cap - len(kept[fd])
            if room > 0:
                kept[fd] += chunk[:room]
    return kept, seen, False


def collect(ctx, gone_s=GONE_S):
    """Every process now under the harness (its subreaper children): pinned, SIGKILLed, reaped, until none is left
    or gone_s passes. Returns (none_left, signalled_count, rounds)."""
    deadline = time.monotonic() + gone_s
    signalled, rounds = 0, 0
    while True:
        ctx['reap_all']()
        kids = ctx['own_children']()
        if not kids:
            return True, signalled, rounds
        if time.monotonic() >= deadline:
            return False, signalled, rounds
        rounds += 1
        for pid in kids:
            p = ctx['Pin'](pid)
            if p.send(signal.SIGKILL):
                signalled += 1
            p.close()
        time.sleep(0.2)


def launch(ctx, timeout_s=TIMEOUT_S, cap=CAP, gone_s=GONE_S):
    """ctx: argv, env, cwd, target (the probe's create path), op, Pin, reap_all, own_children, parse_probe,
    diagnose, record_fields (the probe record keys to keep)."""
    rec = {'path': 'codex_sandbox_cli', 'cap_bytes': cap, 'timeout_s': timeout_s}
    before = ctx['own_children']()
    if before:  # not expected after stop_case (it checked); nothing is started then
        rec.update(skipped='children_present', children_before=len(before))
        return rec, False
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(ctx['argv'], env=ctx['env'], cwd=ctx['cwd'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        rec.update(spawn_error=type(e).__name__)
        return rec, True
    pin = ctx['Pin'](proc.pid)
    rec['pinned'] = pin.ok()
    out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
    kept, seen, timed_out = read_capped((out_fd, err_fd), t0 + timeout_s, cap)
    group_killed = None
    if timed_out:
        try:  # the launch's own process group: its id is the launch's pid, not reaped yet, so still ours
            os.killpg(proc.pid, signal.SIGKILL)
            group_killed = True
        except OSError:
            group_killed = False
    try:
        rc = proc.wait(timeout=gone_s)
    except subprocess.TimeoutExpired:
        pin.send(signal.SIGKILL)
        try:
            rc = proc.wait(timeout=gone_s)
        except subprocess.TimeoutExpired:
            rc = None
    pin.close()
    for f in (proc.stdout, proc.stderr):
        try:
            f.close()
        except OSError:
            pass
    none_left, signalled, rounds = collect(ctx, gone_s)
    stdout = bytes(kept[out_fd]).decode('utf-8', 'replace')
    stderr = bytes(kept[err_fd]).decode('utf-8', 'replace')
    out_complete = seen[out_fd] <= cap and not timed_out
    record = ctx['parse_probe'](stdout, ctx['op']) if out_complete else None
    received = {'stdout': seen[out_fd], 'stderr': seen[err_fd]}
    diag = ctx['diagnose'](stdout, stderr, rc, ctx['op'], record is not None, received)
    rec.update(rc=rc, timed_out=timed_out, group_killed=group_killed, seconds=round(time.monotonic() - t0, 2),
               stdout_truncated=seen[out_fd] > cap, stderr_truncated=seen[err_fd] > cap,
               record={k: record.get(k) for k in ctx['record_fields']} if record else None,
               diag=diag, effect=os.path.lexists(ctx['target']),
               end_confirmed=rc is not None, none_left=none_left, signalled_after=signalled, collect_rounds=rounds)
    return rec, bool(none_left and rc is not None)
