"""#68 C design r3 (v), scratchpad helper only: a runner-owned empty `.codex` placeholder.

Not production code: not wired into backend/runner, not committed. It implements the design memo r3 §3.1, §3.2 and
§3.7 so that Docker unit experiments can exercise the state table, the failure paths and the limits (non-atomic
allow check, record not a permanent object id). What it can show: the helper's logic. What it cannot show: P1–P3,
T1–T3 (Codex behaviour, sandbox protection, writer exclusion).

Layout (paths are parameters): `state` holds the record file and `.placeholder-staging`; `work` is the workspace.
"""
import ctypes
import ctypes.util
import errno
import json
import os
import secrets
import stat

NAME = '.codex'
RECORD = 'dot-codex-placeholder.json'
STAGING = '.placeholder-staging'
RENAME_NOREPLACE = 1
MODE = 0o755


class Refused(Exception):
    def __init__(self, reason, **fields):
        super().__init__(reason)
        self.reason, self.fields = reason, fields


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


# Pure parts --------------------------------------------------------------------------------------------------
def decide(record_state, name_present):
    """State table (memo r3 §3.7). record_state: 'none' | 'valid' | 'corrupt'. Returns 'create' | 'check' | refusal."""
    if record_state == 'corrupt':
        return 'refuse:record_invalid'
    if not name_present:
        return 'create'
    if record_state == 'none':
        return 'refuse:not_runner_owned'
    return 'check'


def match_problem(record, st, entries, euid):
    """None when the opened object matches the record (§3.2); else a fixed reason. st: os.stat_result of the fd."""
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
    """stage: 'before_replace' (no new record is visible) or 'after_replace' (a valid record is visible, its
    durability is unknown)."""
    def __init__(self, stage, err):
        super().__init__(stage)
        self.stage, self.err = stage, err


# Record I/O (read_private / write_private equivalents) -------------------------------------------------------
def read_record(state):
    """('none', None) | ('valid', value) | ('corrupt', None). Never raises for content problems."""
    path = os.path.join(state, RECORD)
    try:
        # O_NONBLOCK: a FIFO (or other special file) must not block the open; it is refused by the fstat below.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'none', None
    except OSError:
        return 'corrupt', None  # a link (ELOOP), a FIFO without writer on some systems (ENXIO), unreadable
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
    finally:
        os.close(fd)


def write_record(state, value, trace, ops=None):
    """Raises RecordWriteError('before_replace' | 'after_replace', errno name). ops: injectable fsync/replace."""
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
        raise RecordWriteError('before_replace', errno.errorcode.get(e.errno, 'other')) from None
    try:
        dfd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fsync(dfd)
            trace.append('fsync:state-dir')
        finally:
            os.close(dfd)
    except OSError as e:
        raise RecordWriteError('after_replace', errno.errorcode.get(e.errno, 'other')) from None


# Effects -----------------------------------------------------------------------------------------------------
def _open_dir(path, create=False):
    try:
        if create:
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                pass
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        raise Refused('staging_failed', errno=errno.errorcode.get(e.errno, 'other')) from None
    st = os.fstat(fd)
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
        os.close(fd)
        raise Refused('staging_invalid')
    return fd


def ensure(work, state, now='1970-01-01T00:00:00Z', renameat2=default_renameat2, hooks=None, trace=None):
    """Startup step (memo r3 §3.1/§3.7). Returns an event dict; raises Refused with a fixed reason."""
    hooks, trace = hooks or {}, trace if trace is not None else []
    record_state, record = read_record(state)
    wfd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.lstat(NAME, dir_fd=wfd)
            present = True
        except FileNotFoundError:
            present = False
        action = decide(record_state, present)
        if action.startswith('refuse:'):
            raise Refused(action.split(':', 1)[1])
        if action == 'check':
            return check(work, state, record=record)
        sfd = _open_dir(os.path.join(state, STAGING), create=True)
        try:
            rnd = 'p-' + secrets.token_hex(12)
            try:
                os.mkdir(rnd, MODE, dir_fd=sfd)
                cfd = os.open(rnd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=sfd)
            except OSError as e:
                raise Refused('staging_failed', errno=errno.errorcode.get(e.errno, 'other')) from None
            try:  # the creation fd stays open until the record is durable (B1)
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
                    raise Refused('publish_failed', errno=errno.errorcode.get(e.errno, 'other'),
                                  staging_left=left) from None
                if 'after_publish' in hooks:
                    hooks['after_publish'](wfd)
                try:
                    pfd = os.open(NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wfd)
                except OSError:
                    raise Refused('identity_mismatch') from None
                try:
                    pst = os.fstat(pfd)
                    if (pst.st_dev, pst.st_ino) != (made.st_dev, made.st_ino) or os.listdir(pfd):
                        raise Refused('identity_mismatch')
                finally:
                    os.close(pfd)
                os.fsync(wfd)
                trace.append('fsync:work-dir')
                os.fsync(sfd)
                trace.append('fsync:staging-dir')
                if 'before_record' in hooks:
                    hooks['before_record']()
                value = {'version': 1, 'dev': pst.st_dev, 'ino': pst.st_ino, 'mtime_ns': pst.st_mtime_ns,
                         'createdAt': now}
                try:
                    write_record(state, value, trace, hooks.get('ops'))
                except RecordWriteError as e:
                    # before_replace: no new record; the published .codex is unrecorded -> refused next time.
                    # after_replace: a valid record is visible but may not survive a crash; this start refuses;
                    # the next start sees either that record (-> matched, the object is the runner's own) or no
                    # record (-> not_runner_owned). Neither path accepts an object the runner did not create.
                    raise Refused('record_write_failed', stage=e.stage, errno=e.err) from None
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
    """The `.codex` part of check_local (memo r3 §3.2). NOT atomic: see §3.6; returns an event or raises Refused."""
    hooks = hooks or {}
    if record is None:
        record_state, record = read_record(state)
    else:
        record_state = 'valid'
    if record_state == 'corrupt':
        raise Refused('record_invalid')  # before the name lookup: the state table refuses in both columns
    wfd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.lstat(NAME, dir_fd=wfd)
        except FileNotFoundError:
            return {'event': 'absent'}
        action = decide(record_state, True)
        if action.startswith('refuse:'):
            raise Refused(action.split(':', 1)[1])
        try:
            fd = os.open(NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wfd)
        except OSError as e:
            raise Refused('not_a_directory', errno=errno.errorcode.get(e.errno, 'other')) from None
        try:
            if 'during_check' in hooks:
                hooks['during_check'](wfd)
            problem = match_problem(record, os.fstat(fd), os.listdir(fd), os.geteuid())
            if problem:
                raise Refused(problem)
            # The path is looked up again and compared with the opened object. This narrows, but does not close,
            # the window: a swap after this line is not detected (memo r3 §3.2, §3.6).
            try:
                again = os.lstat(NAME, dir_fd=wfd)
            except OSError:
                raise Refused('identity_mismatch') from None
            st = os.fstat(fd)
            if (again.st_dev, again.st_ino) != (st.st_dev, st.st_ino):
                raise Refused('identity_mismatch')
            return {'event': 'matched', 'ino': st.st_ino}
        finally:
            os.close(fd)
    finally:
        os.close(wfd)
