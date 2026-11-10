"""#68 U2 (user decision 2026-11-10, #68 issuecomment-6763432576): one append-only row in the resources-6 ledger
for its unknown claim 27c8a751-a43a-4281-a163-e0bebc2fece9 (T6, run-x, message POST answered 503).

What the row means (and nothing more): the user accepted, as a risk, an ASSUMED deduction of 200,000 input / 5,000
output tokens for that one claim, and a gate exception for that one claim only (v6-turns.py). The actual usage of
the claim stays unknown; 200,000/5,000 is not a measured value and not an enforced limit (v6pre-turns.py L418 uses
those numbers only as start estimates; L1120/L1176 record an overrun after the fact). The claim itself is not
touched: its status stays `unknown`, it has no usage, and no other byte of the file changes.

Run once, as root on the guest, by PM after review approval and PM go:

    python3 -I -B /root/native68/v6-settle.py

Steps: take the V6 lock (evidence/v6pre.lock, non-blocking); lstat and read the ledger; refuse unless every pin
holds (file sha256 e83369a4…, root 0600 regular, exactly the six V6-pre keys and no `settlements`, rateLimited 0,
exactly one claim, that claim's id and canonical sha256 as pinned, the bytes exactly the writer's own format
json.dumps(sort_keys=True, indent=1) ending in "\\n}"); build the row; compute the new bytes and check them before
writing (the old bytes up to the closing brace are kept byte for byte, `settlements` is the only added key, every
old key equal); write them through an O_EXCL temporary file, fsync, rename and fsync the directory; read the file
back and check the same again plus the sha256 of what was written.

rc: 0 appended and verified; 2 usage / not root; 3 the lock is held (nothing read or written); 4 refused before
any write (a pin did not hold, or the computed bytes failed the check: nothing written); 9 the write or the
read-back check failed: the outcome is unknown, do not rerun, read the ledger with `v6-turns.py check`.
Output: fixed fields only (sha256 before/after, key names, claim and row counts, row id, recordedAt, problems as
fixed phrases). No usage, conversation, thread or notes values are printed.
"""
import fcntl
import hashlib
import json
import os
import stat
import sys
import time
import uuid

LEDGER = '/root/native68/evidence/ledger-resources-6.json'
LOCK = '/root/native68/evidence/v6pre.lock'
PINS = {
    'sha256': 'e83369a42ec690ae604b041d02055e8d03e780d342e459c105aef51126620592',
    'claim': '27c8a751-a43a-4281-a163-e0bebc2fece9',
    'claimSha256': 'f9055176dca046a3907970eb8ebfdbb01a2249f9616e214c5057b1f90af60ff9',
}
LIMITS = {'turns': 6, 'input': 900000, 'output': 20000}
OLD_KEYS = {'bundle', 'claims', 'created', 'createdBy', 'limits', 'rateLimited'}
DECISION = 'https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/68#issuecomment-6763432576'
REASON = ('message POST answered 503, usage unknown; assumed deduction 200000 input / 5000 output accepted as a risk '
          'by user decision 2026-11-10; actual usage unknown (not a measured value, not an enforced limit)')


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def writer_format(value):
    return json.dumps(value, sort_keys=True, indent=1).encode()


def precheck(raw, info, pins=PINS):
    """Problems (fixed phrases) with the current ledger; [] when every pin holds. `info`: (regular, uid, mode)."""
    problems = []
    regular, uid, mode = info
    if not (regular and uid == 0 and mode == 0o600):
        problems.append('ledger is not a root 0600 regular file')
    if hashlib.sha256(raw).hexdigest() != pins['sha256']:
        problems.append('ledger sha256 is not the pinned value')
    try:
        value = json.loads(raw)
    except ValueError:
        return problems + ['ledger is not JSON']
    if not isinstance(value, dict):
        return problems + ['ledger is not an object']
    if set(value) != OLD_KEYS:
        problems.append('ledger keys are not exactly the V6-pre keys (a settlements key or another key exists, or one is missing)')
    if value.get('bundle') != 'resources-6' or value.get('limits') != LIMITS:
        problems.append('ledger bundle or limits differ')
    if type(value.get('rateLimited')) is not int or value.get('rateLimited') != 0:
        problems.append('ledger rateLimited is not 0')
    claims = value.get('claims')
    if not isinstance(claims, list) or len(claims) != 1:
        problems.append('ledger does not hold exactly one claim')
    else:
        claim = claims[0]
        if not isinstance(claim, dict) or claim.get('id') != pins['claim']:
            problems.append('the claim id is not the pinned id')
        elif canonical_sha(claim) != pins['claimSha256']:
            problems.append('the claim differs from its pinned record')
    if writer_format(value) != raw or not raw.endswith(b'\n}'):
        problems.append('ledger bytes are not in the writer format')
    return problems


def build_row(row_id, recorded_at, tool, pins=PINS):
    return {'id': row_id, 'kind': 'assumed-deduction-risk-acceptance', 'bundle': 'resources-6',
            'claim': pins['claim'], 'claimSha256': pins['claimSha256'], 'ledgerSha256Before': pins['sha256'],
            'assumedDeduction': {'input': 200000, 'output': 5000}, 'actualUsage': 'unknown', 'reason': REASON,
            'decision': DECISION, 'decidedOn': '2026-11-10', 'recordedAt': recorded_at,
            'recordedBy': 'v6-settle.py (run by PM)', 'tool': tool}


def new_bytes(raw, row):
    value = json.loads(raw)
    value['settlements'] = [row]
    return writer_format(value)


def postcheck(old_raw, new_raw, row):
    """Problems (fixed phrases) with new_raw as the result of appending `row` to old_raw; [] when it is exactly that."""
    problems = []
    if not old_raw.endswith(b'\n}') or len(new_raw) <= len(old_raw) or new_raw[:len(old_raw) - 2] != old_raw[:-2]:
        problems.append('the old bytes are not kept up to the closing brace')
    try:
        old, new = json.loads(old_raw), json.loads(new_raw)
    except ValueError:
        return problems + ['old or new bytes are not JSON']
    if not isinstance(new, dict) or set(new) != set(old) | {'settlements'} or 'settlements' in old:
        problems.append('settlements is not the only added key')
    elif any(new[k] != old[k] for k in old):
        problems.append('an old key changed')
    elif new['settlements'] != [row]:
        problems.append('the settlements list is not exactly the one row')
    if isinstance(new, dict) and writer_format(new) != new_raw:
        problems.append('new bytes are not in the writer format')
    return problems


def durable_write(path, data):
    temporary = '%s.%d.settle.tmp' % (path, os.getpid())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    folder = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def apply(raw, info, writer, reader, row_id, recorded_at, tool, pins=PINS):
    """(rc, fields). writer(bytes) writes the ledger; reader() reads it back. No other effect."""
    fields = {'sha256Before': hashlib.sha256(raw).hexdigest()}
    problems = precheck(raw, info, pins)
    if problems:
        return 4, dict(fields, problems=problems, written=False)
    row = build_row(row_id, recorded_at, tool, pins)
    data = new_bytes(raw, row)
    problems = postcheck(raw, data, row)
    if problems:
        return 4, dict(fields, problems=problems, written=False)
    try:
        writer(data)
    except Exception as failure:
        return 9, dict(fields, problems=['write failed (%s)' % type(failure).__name__], written='unknown')
    try:
        back = reader()
    except Exception as failure:
        return 9, dict(fields, problems=['read-back failed (%s)' % type(failure).__name__], written='unknown')
    problems = postcheck(raw, back, row)
    if hashlib.sha256(back).hexdigest() != hashlib.sha256(data).hexdigest():
        problems.append('read-back bytes differ from the bytes written')
    value = json.loads(back) if not problems else {}
    fields.update(sha256After=hashlib.sha256(back).hexdigest(), rowId=row_id, recordedAt=recorded_at,
                  claimsBefore=1, settlementsBefore=0, claimsAfter=len(value.get('claims', [])) if value else None,
                  settlementsAfter=len(value.get('settlements', [])) if value else None, keysAfter=sorted(value),
                  problems=problems, written=True if not problems else 'unknown')
    return (0 if not problems else 9), fields


def main(argv):
    if len(argv) != 1:
        print('usage: v6-settle.py (no arguments)')
        return 2
    if os.geteuid() != 0:
        print('run as root')
        return 2
    tool = hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest()
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(json.dumps({'rc': 3, 'problems': ['another V6-pre/V6 action holds the lock; nothing read or written']}))
            return 3
        try:
            info = os.lstat(LEDGER)
            raw = open(LEDGER, 'rb').read()
        except OSError as failure:
            print(json.dumps({'rc': 4, 'problems': ['ledger lookup failed (%s); nothing written' % type(failure).__name__]}))
            return 4
        recorded_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        def reader():
            return open(LEDGER, 'rb').read()
        rc, fields = apply(raw, (stat.S_ISREG(info.st_mode), info.st_uid, stat.S_IMODE(info.st_mode)),
                           lambda data: durable_write(LEDGER, data), reader, str(uuid.uuid4()), recorded_at, tool)
        fields.update(rc=rc, tool=tool)
        print(json.dumps(fields, sort_keys=True))
        return rc
    finally:
        os.close(fd)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
