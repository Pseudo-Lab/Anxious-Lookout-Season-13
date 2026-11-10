"""#68 G2: the runner-owned empty workspace `.codex` placeholder (design g2 r2, design (v) r5).

Ported from the reviewed scratch helper r2 (`dot_codex.py` 2ca0793e) with the design g2 §3.1 stage adapter: every
expected filesystem call runs under a fixed stage, and an `OSError` there becomes `Refused(reason, stage, errno)`.
The runner entry points log one line with names only (event, phase, stage, reason, errno name, ino) and turn every
refusal into `policy_refused` / `local.workspace_dot_codex`. No path, no file content and no exception text is ever
logged. A programming error (not `Refused`, not an `OSError` at a stage) is not converted: it propagates to the
runner's existing handlers and is never an allow.

What this module does:
- `ensure_placeholder` (once per Native, before Codex and credentials): the state table of design (v) §3.7. With no
  `.codex` and no corrupt record, it creates an empty dir in `root/.placeholder-staging`, publishes it as
  `work/.codex` with renameat2(RENAME_NOREPLACE), verifies it, fsyncs, then writes the record
  `root/dot-codex-placeholder.json`.
- `check_placeholder` (the `.codex` part of check_local: start, launch, every turn): absent is allowed (as before);
  present is allowed only if it is exactly the recorded empty object.

What it does not do: delete or rename an existing workspace entry. The only removal is the staging copy after a
failed publish, and only if it is still the same empty object.

Limits (design (v) §3.2, §3.6, §4.1): the check is not atomic; the record is not a permanent object id (inode reuse
with a restored mtime matches); correctness rests on the trust premises T1–T3 and P3. Deployment scope (S1): owner
…5805 on the development VM only, until G4; see native-shell/README.md section 6.
"""
import ctypes
import ctypes.util
import datetime
import errno
import json
import logging
import os
import secrets
import stat
from contextlib import contextmanager

NAME = '.codex'
RECORD = 'dot-codex-placeholder.json'
STAGING = '.placeholder-staging'
RENAME_NOREPLACE = 1
MODE = 0o755
CHECK = 'local.workspace_dot_codex'
LOG_KEYS = frozenset({'event', 'phase', 'stage', 'reason', 'errno', 'when', 'ino', 'replaced', 'staging_left'})


class Refused(Exception):
    """A fixed refusal: reason and stage are fixed names, errno is an errno name or None."""

    def __init__(self, reason, stage=None, errno_name=None, **fields):
        super().__init__(reason)
        self.reason, self.stage, self.errno, self.fields = reason, stage, errno_name, fields


def errno_name(error):
    return errno.errorcode.get(error.errno, 'other') if getattr(error, 'errno', None) is not None else None


@contextmanager
def stage(name, reason, **fields):
    """An OSError inside becomes Refused(reason, stage=name, errno); Refused passes through; anything else propagates
    unchanged (design g2 §3.1)."""
    try:
        yield
    except Refused:
        raise
    except OSError as error:
        raise Refused(reason, name, errno_name(error), **fields) from None


def _libc_renameat2():
    libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
    try:
        fn = libc.renameat2
    except AttributeError:
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int

    def renameat2(src_fd, src, dst_fd, dst, flags):
        if fn(src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), flags) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    return renameat2


def default_renameat2(src_fd, src, dst_fd, dst, flags):
    impl = _libc_renameat2()
    if impl is None:
        raise OSError(errno.ENOSYS, 'renameat2 unavailable')
    impl(src_fd, src, dst_fd, dst, flags)


# --- pure parts (helper r2, unchanged) ---------------------------------------------------------------------------
def decide(record_state, name_present):
    """State table (design (v) §3.7). record_state: 'none' | 'valid' | 'corrupt'. 'create' | 'check' | refusal."""
    if record_state == 'corrupt':
        return 'refuse:record_invalid'
    if not name_present:
        return 'create'
    if record_state == 'none':
        return 'refuse:not_runner_owned'
    return 'check'


def match_problem(record, st, entries, euid):
    """None when the opened object matches the record (design (v) §3.2); else a fixed reason."""
    if not stat.S_ISDIR(st.st_mode):
        return 'not_a_directory'
    if st.st_uid != euid or stat.S_IMODE(st.st_mode) != MODE:
        return 'identity_mismatch'
    if (st.st_dev, st.st_ino, st.st_mtime_ns) != (record['dev'], record['ino'], record['mtime_ns']):
        return 'identity_mismatch'
    if entries:
        return 'not_empty'
    return None


RECORD_KEYS = {'version', 'dev', 'ino', 'mtime_ns', 'createdAt'}


def valid_record(value):
    """Exact schema: the five keys only; version the int 1 (not True); dev/ino/mtime_ns non-negative ints."""
    return (isinstance(value, dict) and set(value) == RECORD_KEYS
            and type(value['version']) is int and value['version'] == 1
            and all(type(value[k]) is int and value[k] >= 0 for k in ('dev', 'ino', 'mtime_ns'))
            and isinstance(value['createdAt'], str))


class RecordWriteError(Exception):
    """phase: 'before_replace' (no new record is visible) or 'after_replace' (a valid record is visible, its
    durability is unknown)."""

    def __init__(self, when, err):
        super().__init__(when)
        self.when, self.err = when, err


# --- record I/O ----------------------------------------------------------------------------------------------
def read_record(state):
    """('none', None) | ('valid', value) | ('corrupt', None). ENOENT is the normal 'none' branch; every other
    failure, including fstat/read errors, is 'corrupt' (stage record_read, design g2 §3.1)."""
    path = os.path.join(state, RECORD)
    try:
        # O_NONBLOCK: a FIFO (or other special file) must not block the open; it is refused by the fstat below.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'none', None
    except OSError:
        return 'corrupt', None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_nlink != 1 or st.st_size > 65536):
            return 'corrupt', None
        try:
            value = json.loads(os.read(fd, 65537))
        except (ValueError, UnicodeError):
            return 'corrupt', None
        return ('valid', value) if valid_record(value) else ('corrupt', None)
    except OSError:
        return 'corrupt', None
    finally:
        os.close(fd)


def write_record(state, value, trace, ops=None):
    """Raises RecordWriteError('before_replace' | 'after_replace', errno name). ops: fixed fault hooks
    (fsync / replace) for the tests."""
    ops = ops or {}
    fsync, replace = ops.get('fsync', os.fsync), ops.get('replace', os.replace)
    path = os.path.join(state, RECORD)
    temporary = os.path.join(state, '.record-' + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, json.dumps(value, sort_keys=True).encode())
            fsync(fd)
            trace.append('fsync:record-file')
        finally:
            os.close(fd)
        if os.path.islink(path):
            raise OSError(errno.ELOOP, 'record path is a link')
        replace(temporary, path)
    except OSError as e:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise RecordWriteError('before_replace', errno_name(e)) from None
    try:
        dfd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fsync(dfd)
            trace.append('fsync:state-dir')
        finally:
            os.close(dfd)
    except OSError as e:
        raise RecordWriteError('after_replace', errno_name(e)) from None


# --- effects -------------------------------------------------------------------------------------------------
def _open_staging(path):
    """Stage `staging`: the private staging dir (0700-only bits, euid). Made if missing."""
    with stage('staging', 'staging_failed'):
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with stage('staging', 'staging_failed'):
            st = os.fstat(fd)
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise Refused('staging_invalid', 'staging')
    except BaseException:
        os.close(fd)
        raise
    return fd


def _name_present(wfd):
    """Stage `name_lookup`: ENOENT is the normal 'absent' branch; any other error refuses lookup_failed."""
    with stage('name_lookup', 'lookup_failed'):
        try:
            os.lstat(NAME, dir_fd=wfd)
            return True
        except FileNotFoundError:
            return False


def _open_work(work):
    with stage('work_open', 'workspace_unavailable'):
        return os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def ensure(work, state, now=None, renameat2=default_renameat2, hooks=None, trace=None):
    """The start step (design (v) §3.1/§3.7, design g2 §2/§3.1). Returns an event dict; raises Refused."""
    hooks, trace = hooks or {}, trace if trace is not None else []
    now = now or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    record_state, record = read_record(state)
    wfd = _open_work(work)
    try:
        action = decide(record_state, _name_present(wfd))
        if action.startswith('refuse:'):
            raise Refused(action.split(':', 1)[1], 'record_read' if action == 'refuse:record_invalid' else 'name_lookup')
        if action == 'check':
            return check(work, state, record=record, hooks=hooks)
        sfd = _open_staging(os.path.join(state, STAGING))
        try:
            rnd = 'p-' + secrets.token_hex(12)
            with stage('create', 'staging_failed'):
                os.mkdir(rnd, MODE, dir_fd=sfd)
            with stage('create', 'staging_failed', staging_left=True):
                cfd = os.open(rnd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=sfd)
            try:  # the creation fd stays open until the record is durable
                with stage('create', 'staging_failed', staging_left=True):
                    os.fchmod(cfd, MODE)
                    made = os.fstat(cfd)
                if 'before_publish' in hooks:
                    hooks['before_publish'](sfd, rnd, wfd)
                try:
                    renameat2(sfd, rnd, wfd, NAME, RENAME_NOREPLACE)
                except OSError as e:
                    left = True
                    try:
                        now_st = os.lstat(rnd, dir_fd=sfd)
                        if (now_st.st_dev, now_st.st_ino) == (made.st_dev, made.st_ino) and not os.listdir(cfd):
                            os.rmdir(rnd, dir_fd=sfd)
                            left = False
                    except OSError:
                        pass
                    raise Refused('publish_failed', 'publish', errno_name(e), staging_left=left) from None
                if 'after_publish' in hooks:
                    hooks['after_publish'](wfd)
                with stage('verify', 'identity_mismatch'):
                    pfd = os.open(NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wfd)
                try:
                    with stage('verify', 'verify_failed'):
                        pst = os.fstat(pfd)
                        entries = os.listdir(pfd)
                    if (pst.st_dev, pst.st_ino) != (made.st_dev, made.st_ino) or entries:
                        raise Refused('identity_mismatch', 'verify')
                finally:
                    os.close(pfd)
                fsync = hooks.get('publish_fsync', os.fsync)
                with stage('publish_sync', 'publish_sync_failed'):
                    fsync(wfd)
                    trace.append('fsync:work-dir')
                    fsync(sfd)
                    trace.append('fsync:staging-dir')
                if 'before_record' in hooks:
                    hooks['before_record']()
                value = {'version': 1, 'dev': pst.st_dev, 'ino': pst.st_ino, 'mtime_ns': pst.st_mtime_ns,
                         'createdAt': now}
                try:
                    write_record(state, value, trace, hooks.get('ops'))
                except RecordWriteError as e:
                    raise Refused('record_write_failed', 'record_write', e.err, when=e.when) from None
                trace.append('close:creation-fd')
            finally:
                os.close(cfd)
        finally:
            os.close(sfd)
        event = {'event': 'created', 'ino': pst.st_ino}
        if record_state == 'valid':
            event['replaced'] = record['ino']
        return event
    finally:
        os.close(wfd)


def check(work, state, record=None, hooks=None):
    """The `.codex` part of check_local (design (v) §3.2). NOT atomic. Returns an event; raises Refused."""
    hooks = hooks or {}
    if record is None:
        record_state, record = read_record(state)
    else:
        record_state = 'valid'
    if record_state == 'corrupt':
        raise Refused('record_invalid', 'record_read')  # before the name lookup: both columns refuse
    wfd = _open_work(work)
    try:
        if not _name_present(wfd):
            return {'event': 'absent'}
        action = decide(record_state, True)
        if action.startswith('refuse:'):
            raise Refused(action.split(':', 1)[1], 'name_lookup')
        with stage('check_open', 'not_a_directory'):
            fd = os.open(NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wfd)
        try:
            if 'during_check' in hooks:
                hooks['during_check'](wfd)
            with stage('check_open', 'lookup_failed'):
                st, entries = os.fstat(fd), os.listdir(fd)
            problem = match_problem(record, st, entries, os.geteuid())
            if problem:
                raise Refused(problem, 'check_open')
            # The path is looked up again and compared with the opened object. This narrows, but does not close,
            # the window: a swap after this line is not detected (design (v) §3.2, §3.6).
            with stage('check_open', 'identity_mismatch'):
                again = os.lstat(NAME, dir_fd=wfd)
            if (again.st_dev, again.st_ino) != (st.st_dev, st.st_ino):
                raise Refused('identity_mismatch', 'check_open')
            return {'event': 'matched', 'ino': st.st_ino}
        finally:
            os.close(fd)
    finally:
        os.close(wfd)


# --- runner entry points -------------------------------------------------------------------------------------
def log_line(fields):
    """One log line; only the fixed keys, names and numbers (design g2 §3, U1: the ino number is allowed)."""
    clean = {k: v for k, v in fields.items() if k in LOG_KEYS and v is not None}
    level = logging.WARNING if clean.get('event') == 'refused' else logging.INFO
    logging.getLogger('uvicorn.error').log(level, 'runner_dot_codex_placeholder %s', json.dumps(clean, sort_keys=True))


def _refused(phase, refusal):
    from .native_policy import refuse
    fields = {'event': 'refused', 'phase': phase, 'stage': refusal.stage, 'reason': refusal.reason,
              'errno': refusal.errno}
    if refusal.fields.get('staging_left'):
        fields['staging_left'] = True
    if refusal.fields.get('when') in ('before_replace', 'after_replace'):
        fields['when'] = refusal.fields['when']
    log_line(fields)
    refuse(CHECK)


def ensure_placeholder(root, work, **test_hooks):
    """Native start (phase 'start'): design g2 §2. Refusal -> policy_refused / local.workspace_dot_codex."""
    try:
        event = ensure(str(work), str(root), **test_hooks)
    except Refused as refusal:
        _refused('start', refusal)
    log_line(dict(event, phase='start'))
    return event


def check_placeholder(root, work, phase, hooks=None):
    """check_local's `.codex` part at start / launch / turn_pre (design g2 §3.2): one line per call."""
    try:
        event = check(str(work), str(root), hooks=hooks)
    except Refused as refusal:
        _refused(phase, refusal)
    log_line(dict(event, phase=phase))
    return event
