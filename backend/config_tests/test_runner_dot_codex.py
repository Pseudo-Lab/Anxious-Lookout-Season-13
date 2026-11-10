"""#68 G2: the runner-owned `.codex` placeholder (runner/dot_codex.py; design g2 r2 `71ff8348…`, design (v) r5).

Pure unit tests on a real filesystem (tmp_path on tmpfs, /dev/shm for a second filesystem). No mock of the module
or of the filesystem; fixed fault hooks only where a condition cannot be made real (fsync failure, ENOSYS, a
swap at a fixed point). No Codex, no VM, no network.

Covered:
- the reviewed helper r2's cases (state table, creation and match, misattribution, record states, failure and
  interruption paths, identity during the check, and the LIMIT rows), ported;
- design g2 §3.1: every stage turns an OSError into a fixed stage / reason / errno, with what is left behind and the
  next start; the normal ENOENT branches are kept; a programming error propagates and is never an allow;
- the runner entry points: refusal -> policy_refused / local.workspace_dot_codex; one log line per event with the
  fixed keys only and no path or content (PM note c); re-entry in the same root and from a fresh process;
- r1: the README section 6 restore procedure with the shared `start_after_restore` helper of the integration tests.

What these tests cannot show: P1–P5 and T1–T3 (Codex behaviour, sandbox protection, PV stability). Those are G3 / G4.
"""
import ast
import errno
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.codex_policy import CodexFailure
from runner import dot_codex as d

RUNNER = Path(__file__).resolve().parents[1] / 'runner'


@pytest.fixture
def dirs(tmp_path):
    state, work = tmp_path / 'state', tmp_path / 'work'
    state.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    return str(work), str(state)


def codex(work):
    return os.path.join(work, d.NAME)


def record_bytes(state):
    p = os.path.join(state, d.RECORD)
    return open(p, 'rb').read() if os.path.exists(p) else None


def refused(fn, *args, **kwargs):
    with pytest.raises(d.Refused) as caught:
        fn(*args, **kwargs)
    return caught.value


def staging_names(state):
    p = os.path.join(state, d.STAGING)
    return sorted(os.listdir(p)) if os.path.isdir(p) else None


# === ported from helper r2 ==========================================================================================
@pytest.mark.parametrize('record_state,present,expected', [
    ('none', False, 'create'), ('none', True, 'refuse:not_runner_owned'),
    ('valid', False, 'create'), ('valid', True, 'check'),
    ('corrupt', False, 'refuse:record_invalid'), ('corrupt', True, 'refuse:record_invalid')])
def test_state_table(record_state, present, expected):
    assert d.decide(record_state, present) == expected


def test_create_then_match_then_restart(dirs):
    work, state = dirs
    event = d.ensure(work, state)
    assert event['event'] == 'created'
    st = os.lstat(codex(work))
    assert oct(st.st_mode & 0o777) == oct(0o755) and os.listdir(codex(work)) == []
    rec = json.loads(record_bytes(state))
    assert (rec['dev'], rec['ino'], rec['mtime_ns']) == (st.st_dev, st.st_ino, st.st_mtime_ns)
    assert d.check(work, state) == {'event': 'matched', 'ino': st.st_ino}
    assert d.ensure(work, state) == {'event': 'matched', 'ino': st.st_ino}  # a restart finds the same object
    assert staging_names(state) == []


def test_durability_order(dirs):
    work, state = dirs
    trace = []
    d.ensure(work, state, trace=trace)
    assert trace == ['fsync:work-dir', 'fsync:staging-dir', 'fsync:record-file', 'fsync:state-dir',
                     'close:creation-fd']


def test_real_renameat2_noreplace(dirs):
    work, state = dirs
    os.mkdir(os.path.join(state, 'a'))
    os.mkdir(os.path.join(work, 'b'))
    sfd, wfd = os.open(state, os.O_RDONLY), os.open(work, os.O_RDONLY)
    try:
        with pytest.raises(OSError) as e:
            d.default_renameat2(sfd, 'a', wfd, 'b', d.RENAME_NOREPLACE)
        assert e.value.errno == errno.EEXIST
        assert os.path.isdir(os.path.join(state, 'a')) and os.path.isdir(os.path.join(work, 'b'))
    finally:
        os.close(sfd)
        os.close(wfd)


def test_pre_existing_codex_without_record_refused(dirs):
    work, state = dirs
    os.mkdir(codex(work), 0o755)
    assert refused(d.ensure, work, state).reason == 'not_runner_owned'
    assert record_bytes(state) is None and os.path.isdir(codex(work))


def test_external_codex_after_placeholder_gone_refused(dirs):
    work, state = dirs
    d.ensure(work, state)
    os.rename(codex(work), os.path.join(work, 'kept'))  # placeholder moved away (trusted actor only)
    os.mkdir(codex(work), 0o755)  # another empty dir, same uid/mode
    assert refused(d.check, work, state).reason == 'identity_mismatch'
    assert refused(d.ensure, work, state).reason == 'identity_mismatch'


def test_valid_record_name_absent_recreates_and_reports_replaced(dirs):
    work, state = dirs
    old = d.ensure(work, state)['ino']
    os.rename(codex(work), os.path.join(work, 'kept'))
    event = d.ensure(work, state)
    assert event['event'] == 'created' and event['replaced'] == old
    assert os.path.isdir(os.path.join(work, 'kept'))  # never touched


@pytest.mark.parametrize('damage', ['json', 'fields', 'mode', 'link'])
def test_corrupt_record_refused_never_overwritten(dirs, damage):
    work, state = dirs
    p = os.path.join(state, d.RECORD)
    if damage == 'json':
        open(p, 'w').write('{')
    elif damage == 'fields':
        open(p, 'w').write(json.dumps({'version': 1}))
    elif damage == 'mode':
        open(p, 'w').write(json.dumps({'version': 1, 'dev': 1, 'ino': 1, 'mtime_ns': 1, 'createdAt': 'x'}))
        os.chmod(p, 0o644)
    else:
        open(os.path.join(state, 'elsewhere'), 'w').write('{}')
        os.symlink('elsewhere', p)
    before = os.readlink(p) if damage == 'link' else record_bytes(state)
    e = refused(d.ensure, work, state)
    assert (e.reason, e.stage) == ('record_invalid', 'record_read')
    assert not os.path.lexists(codex(work))
    assert (os.readlink(p) if damage == 'link' else record_bytes(state)) == before
    os.mkdir(codex(work), 0o755)
    assert refused(d.check, work, state).reason == 'record_invalid'


def test_symlink_codex_refused(dirs):
    work, state = dirs
    d.ensure(work, state)
    os.rename(codex(work), os.path.join(work, 'kept'))
    os.symlink('kept', codex(work))
    assert refused(d.check, work, state).reason == 'not_a_directory'


def test_content_added_refused(dirs):
    work, state = dirs
    d.ensure(work, state)
    open(os.path.join(codex(work), 'config.toml'), 'w').close()
    assert refused(d.check, work, state).reason in ('not_empty', 'identity_mismatch')


def test_content_added_then_removed_refused(dirs):
    work, state = dirs
    d.ensure(work, state)
    f = os.path.join(codex(work), 'x')
    open(f, 'w').close()
    os.unlink(f)
    assert refused(d.check, work, state).reason == 'identity_mismatch'  # the directory mtime changed


def test_eexist_race_leaves_external_untouched(dirs):
    work, state = dirs

    def sneak(sfd, rnd, wfd):
        os.mkdir(d.NAME, 0o755, dir_fd=wfd)
        open(os.path.join(codex(work), 'external'), 'w').close()
    e = refused(d.ensure, work, state, hooks={'before_publish': sneak})
    assert (e.reason, e.stage, e.errno) == ('publish_failed', 'publish', 'EEXIST')
    assert e.fields['staging_left'] is False
    assert os.listdir(codex(work)) == ['external'] and record_bytes(state) is None
    assert staging_names(state) == []
    assert refused(d.ensure, work, state).reason == 'not_runner_owned'  # next start: name present, no record


def test_enosys_refuses_without_publishing(dirs):
    work, state = dirs

    def none(*a):
        raise OSError(errno.ENOSYS, 'no')
    e = refused(d.ensure, work, state, renameat2=none)
    assert (e.reason, e.stage, e.errno) == ('publish_failed', 'publish', 'ENOSYS')
    assert not os.path.lexists(codex(work)) and record_bytes(state) is None
    assert staging_names(state) == []
    assert d.ensure(work, state)['event'] == 'created'  # next start: name absent -> create again


def test_staging_entry_swapped_before_publish_is_left(dirs):
    work, state = dirs

    def swap(sfd, rnd, wfd):
        os.rename(rnd, 'moved', src_dir_fd=sfd, dst_dir_fd=sfd)
    e = refused(d.ensure, work, state, hooks={'before_publish': swap})
    assert e.reason == 'publish_failed' and e.fields['staging_left'] is True
    assert staging_names(state) == ['moved']  # not deleted: identity not proven by name
    assert not os.path.lexists(codex(work))


def test_swap_after_publish_not_recorded_then_refused(dirs):
    work, state = dirs

    def swap(wfd):
        os.rename(d.NAME, 'published-moved', src_dir_fd=wfd, dst_dir_fd=wfd)
        os.mkdir(d.NAME, 0o755, dir_fd=wfd)
    e = refused(d.ensure, work, state, hooks={'after_publish': swap})
    assert (e.reason, e.stage) == ('identity_mismatch', 'verify')
    assert record_bytes(state) is None
    assert refused(d.check, work, state).reason == 'not_runner_owned'


class Crash(Exception):
    pass


def test_interrupted_before_record_is_refused_next_time(dirs):
    work, state = dirs

    def crash():
        raise Crash()
    with pytest.raises(Crash):  # not an OSError: propagates unchanged, never converted
        d.ensure(work, state, hooks={'before_record': crash})
    assert os.path.isdir(codex(work)) and record_bytes(state) is None
    assert refused(d.ensure, work, state).reason == 'not_runner_owned'


@pytest.mark.parametrize('kind', ['file', 'mode'])
def test_staging_failure(dirs, kind):
    work, state = dirs
    p = os.path.join(state, d.STAGING)
    if kind == 'file':
        open(p, 'w').close()
        e = refused(d.ensure, work, state)
        assert (e.reason, e.stage, e.errno) == ('staging_failed', 'staging', 'ENOTDIR')
    else:
        os.mkdir(p, 0o755)
        assert (refused(d.ensure, work, state).reason, ) == ('staging_invalid', )
    assert not os.path.lexists(codex(work))


def test_swap_during_check_detected(dirs):
    work, state = dirs
    d.ensure(work, state)

    def swap(wfd):
        os.rename(d.NAME, 'moved', src_dir_fd=wfd, dst_dir_fd=wfd)
        os.mkdir(d.NAME, 0o755, dir_fd=wfd)
    assert refused(d.check, work, state, hooks={'during_check': swap}).reason == 'identity_mismatch'


def test_LIMIT_fd_checks_do_not_prove_the_current_path(dirs):
    """An opened placeholder still matches after the path was swapped: fd-only checks are not a path proof."""
    work, state = dirs
    d.ensure(work, state)
    _, rec = d.read_record(state)
    fd = os.open(codex(work), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.rename(codex(work), os.path.join(work, 'moved'))
        os.mkdir(codex(work), 0o755)
        assert d.match_problem(rec, os.fstat(fd), os.listdir(fd), os.geteuid()) is None
        assert os.lstat(codex(work)).st_ino != os.fstat(fd).st_ino
    finally:
        os.close(fd)


def test_LIMIT_swap_after_check_is_not_detectable(dirs):
    work, state = dirs
    d.ensure(work, state)
    assert d.check(work, state)['event'] == 'matched'
    os.rename(codex(work), os.path.join(work, 'moved'))
    os.mkdir(codex(work), 0o755)  # after the decision: nothing here can see it (relies on T1–T3)
    assert refused(d.check, work, state).reason == 'identity_mismatch'  # only the next check sees it


def make_record(state, record_state):
    p = os.path.join(state, d.RECORD)
    if record_state == 'valid':
        os.mkdir(os.path.join(state, 'other'), 0o755)
        st = os.lstat(os.path.join(state, 'other'))
        open(p, 'w').write(json.dumps({'version': 1, 'dev': st.st_dev, 'ino': st.st_ino, 'mtime_ns': st.st_mtime_ns,
                                       'createdAt': 'x'}))
        os.chmod(p, 0o600)
    elif record_state == 'corrupt':
        open(p, 'w').write('{')
        os.chmod(p, 0o600)


@pytest.mark.parametrize('record_state', ['none', 'valid', 'corrupt'])
@pytest.mark.parametrize('present', [False, True])
def test_state_table_applied_by_check_and_ensure(dirs, record_state, present):
    work, state = dirs
    make_record(state, record_state)
    if present:
        os.mkdir(codex(work), 0o755)  # never the recorded object here
    if record_state == 'corrupt':
        assert refused(d.check, work, state).reason == 'record_invalid'
    elif not present:
        assert d.check(work, state) == {'event': 'absent'}
    else:
        reason = refused(d.check, work, state).reason
        assert reason == ('not_runner_owned' if record_state == 'none' else 'identity_mismatch')
    if record_state == 'corrupt':
        assert refused(d.ensure, work, state).reason == 'record_invalid'
    elif not present:
        assert d.ensure(work, state)['event'] == 'created'
    else:
        reason = refused(d.ensure, work, state).reason
        assert reason == ('not_runner_owned' if record_state == 'none' else 'identity_mismatch')


def test_fifo_record_refused_without_blocking(dirs):
    import signal
    work, state = dirs
    p = os.path.join(state, d.RECORD)
    os.mkfifo(p, 0o600)

    def timeout(*a):
        raise AssertionError('read_record blocked on a FIFO')
    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(2)
    try:
        assert d.read_record(state) == ('corrupt', None)
        assert refused(d.check, work, state).reason == 'record_invalid'
        assert refused(d.ensure, work, state).reason == 'record_invalid'
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize('kind', ['directory', 'dangling-link'])
def test_non_regular_record_refused(dirs, kind):
    work, state = dirs
    p = os.path.join(state, d.RECORD)
    if kind == 'directory':
        os.mkdir(p, 0o700)
    else:
        os.symlink('missing', p)
    assert d.read_record(state) == ('corrupt', None)
    assert refused(d.check, work, state).reason == 'record_invalid'
    assert refused(d.ensure, work, state).reason == 'record_invalid'
    assert not os.path.lexists(codex(work))


@pytest.mark.parametrize('bad', [
    {'version': True}, {'version': 2}, {'extra': 1}, {'ino': -1}, {'dev': 1.0}, {'createdAt': 5}, 'missing-mtime'])
def test_strict_record_schema(dirs, bad):
    work, state = dirs
    value = {'version': 1, 'dev': 1, 'ino': 2, 'mtime_ns': 3, 'createdAt': 'x'}
    if bad == 'missing-mtime':
        del value['mtime_ns']
    else:
        value.update(bad)
    p = os.path.join(state, d.RECORD)
    open(p, 'w').write(json.dumps(value))
    os.chmod(p, 0o600)
    assert d.read_record(state) == ('corrupt', None)


@pytest.mark.parametrize('mode', [0o400, 0o640, 0o700])
def test_record_mode_must_be_exactly_0600(dirs, mode):
    work, state = dirs
    p = os.path.join(state, d.RECORD)
    open(p, 'w').write(json.dumps({'version': 1, 'dev': 1, 'ino': 2, 'mtime_ns': 3, 'createdAt': 'x'}))
    os.chmod(p, mode)
    assert d.read_record(state) == ('corrupt', None)


def failing_fsync_on(target):
    """A fixed fault hook: an fsync that fails with EIO for a regular file ('file') or a directory ('dir')."""
    import stat as st_mod

    def fsync(fd):
        is_dir = st_mod.S_ISDIR(os.fstat(fd).st_mode)
        if (target == 'dir') == is_dir:
            raise OSError(errno.EIO, 'injected')
        os.fsync(fd)
    return fsync


def test_record_write_fails_before_replace_file_fsync(dirs):
    work, state = dirs
    e = refused(d.ensure, work, state, hooks={'ops': {'fsync': failing_fsync_on('file')}})
    assert (e.reason, e.stage, e.errno, e.fields['when']) == ('record_write_failed', 'record_write', 'EIO',
                                                             'before_replace')
    assert record_bytes(state) is None and not [n for n in os.listdir(state) if n.startswith('.record-')]
    assert os.path.isdir(codex(work))  # published but unrecorded
    assert refused(d.ensure, work, state).reason == 'not_runner_owned'


def test_record_write_fails_at_replace(dirs):
    work, state = dirs

    def replace(a, b):
        raise OSError(errno.EIO, 'injected')
    e = refused(d.ensure, work, state, hooks={'ops': {'replace': replace}})
    assert e.reason == 'record_write_failed' and e.fields['when'] == 'before_replace'
    assert record_bytes(state) is None and not [n for n in os.listdir(state) if n.startswith('.record-')]
    assert refused(d.check, work, state).reason == 'not_runner_owned'


def test_record_write_fails_before_replace_keeps_old_record_identity_mismatch(dirs):
    work, state = dirs
    old = d.ensure(work, state)['ino']
    os.rename(codex(work), os.path.join(work, 'kept'))  # placeholder gone, old record still valid

    def replace(a, b):
        raise OSError(errno.EIO, 'injected')
    e = refused(d.ensure, work, state, hooks={'ops': {'replace': replace}})
    assert e.reason == 'record_write_failed' and e.fields['when'] == 'before_replace'
    assert d.read_record(state)[1]['ino'] == old  # the old record is what remains
    assert refused(d.check, work, state).reason == 'identity_mismatch'
    assert refused(d.ensure, work, state).reason == 'identity_mismatch'


def test_record_write_fails_after_replace_dir_fsync(dirs):
    work, state = dirs
    e = refused(d.ensure, work, state, hooks={'ops': {'fsync': failing_fsync_on('dir')}})
    assert e.reason == 'record_write_failed' and e.fields['when'] == 'after_replace'
    assert d.read_record(state)[0] == 'valid'
    ino = os.lstat(codex(work)).st_ino
    assert d.ensure(work, state) == {'event': 'matched', 'ino': ino}
    os.unlink(os.path.join(state, d.RECORD))  # durability lost
    assert refused(d.ensure, work, state).reason == 'not_runner_owned'


def test_LIMIT_record_is_not_a_permanent_object_id(dirs):
    """A directory carrying the recorded (dev, ino, mtime_ns), e.g. by inode reuse plus an mtime restore, matches."""
    work, state = dirs
    os.mkdir(codex(work), 0o755)
    os.utime(codex(work), ns=(1, 123456789))
    st = os.lstat(codex(work))
    open(os.path.join(state, d.RECORD), 'w').write(json.dumps(
        {'version': 1, 'dev': st.st_dev, 'ino': st.st_ino, 'mtime_ns': st.st_mtime_ns, 'createdAt': 'x'}))
    os.chmod(os.path.join(state, d.RECORD), 0o600)
    assert d.check(work, state)['event'] == 'matched'  # premise violation: only writers outside T1–T3 can do this


# === design g2 §3.1: the stage adapter (real conditions where possible) ===========================================
def test_runs_as_non_root():
    assert os.geteuid() != 0  # the permission-based stages below rely on it


@pytest.mark.parametrize('fn', ['ensure', 'check'])
def test_work_open_mode_000(dirs, fn):
    work, state = dirs
    os.chmod(work, 0)
    try:
        e = refused(getattr(d, fn), work, state)
        assert (e.stage, e.reason, e.errno) == ('work_open', 'workspace_unavailable', 'EACCES')
        assert record_bytes(state) is None and staging_names(state) is None
    finally:
        os.chmod(work, 0o700)


def test_work_open_a_file_in_place_of_the_dir(tmp_path):
    state, work = tmp_path / 'state', tmp_path / 'work'
    state.mkdir(mode=0o700)
    work.write_text('')
    e = refused(d.ensure, str(work), str(state))
    assert (e.stage, e.reason, e.errno) == ('work_open', 'workspace_unavailable', 'ENOTDIR')


@pytest.mark.parametrize('fn', ['ensure', 'check'])
def test_name_lookup_without_search_permission(dirs, fn):
    """The workspace readable but not searchable: the open succeeds, the `.codex` lstat fails EACCES."""
    work, state = dirs
    os.chmod(work, 0o400)
    try:
        e = refused(getattr(d, fn), work, state)
        assert (e.stage, e.reason, e.errno) == ('name_lookup', 'lookup_failed', 'EACCES')
        assert record_bytes(state) is None
    finally:
        os.chmod(work, 0o700)


def test_create_in_unwritable_staging(dirs):
    work, state = dirs
    p = os.path.join(state, d.STAGING)
    os.mkdir(p, 0o500)  # owned and private, but not writable
    try:
        e = refused(d.ensure, work, state)
        assert (e.stage, e.reason, e.errno) == ('create', 'staging_failed', 'EACCES')
        assert not e.fields.get('staging_left') and not os.path.lexists(codex(work))
    finally:
        os.chmod(p, 0o700)
    assert d.ensure(work, state)['event'] == 'created'  # next start: the cause gone, the table creates


def test_publish_across_filesystems_is_exdev(tmp_path):
    """Staging on /dev/shm, the workspace on /tmp: a real EXDEV; nothing published, staging emptied; the next start
    would create again (same refusal while the layout stays)."""
    shm = Path('/dev/shm')
    if not shm.is_dir() or os.stat(shm).st_dev == os.stat(tmp_path).st_dev:
        pytest.fail('this test needs /dev/shm on a filesystem other than tmp_path')
    state = shm / ('g2-' + os.urandom(6).hex())
    state.mkdir(mode=0o700)
    work = tmp_path / 'work'
    work.mkdir(mode=0o700)
    try:
        e = refused(d.ensure, str(work), str(state))
        assert (e.stage, e.reason, e.errno) == ('publish', 'publish_failed', 'EXDEV')
        assert e.fields['staging_left'] is False and staging_names(str(state)) == []
        assert not os.path.lexists(codex(str(work))) and record_bytes(str(state)) is None
        assert refused(d.ensure, str(work), str(state)).errno == 'EXDEV'
    finally:
        for name in os.listdir(state / d.STAGING) if (state / d.STAGING).is_dir() else []:
            os.rmdir(state / d.STAGING / name)
        if (state / d.STAGING).is_dir():
            os.rmdir(state / d.STAGING)
        os.rmdir(state)


def test_verify_open_fails_when_the_published_name_is_gone(dirs):
    work, state = dirs

    def gone(wfd):
        os.rename(d.NAME, 'published-moved', src_dir_fd=wfd, dst_dir_fd=wfd)
    e = refused(d.ensure, work, state, hooks={'after_publish': gone})
    assert (e.stage, e.reason, e.errno) == ('verify', 'identity_mismatch', 'ENOENT')
    assert record_bytes(state) is None
    assert d.ensure(work, state)['event'] == 'created'  # next start: no record, name absent


def test_publish_sync_failure_leaves_the_object_unrecorded(dirs):
    work, state = dirs

    def fsync(fd):
        raise OSError(errno.EIO, 'injected')
    e = refused(d.ensure, work, state, hooks={'publish_fsync': fsync})
    assert (e.stage, e.reason, e.errno) == ('publish_sync', 'publish_sync_failed', 'EIO')
    assert os.path.isdir(codex(work)) and record_bytes(state) is None  # the published object stays
    assert refused(d.ensure, work, state).reason == 'not_runner_owned'  # next start: operator
    assert refused(d.check, work, state).reason == 'not_runner_owned'


def test_check_open_on_an_unreadable_placeholder(dirs):
    work, state = dirs
    d.ensure(work, state)
    os.chmod(codex(work), 0)
    try:
        e = refused(d.check, work, state)
        assert (e.stage, e.errno) == ('check_open', 'EACCES')
    finally:
        os.chmod(codex(work), 0o755)


def test_enoent_branches_are_normal(dirs):
    """No record, no `.codex`, no staging dir: none of the ENOENT cases refuses."""
    work, state = dirs
    assert d.read_record(state) == ('none', None)
    assert d.check(work, state) == {'event': 'absent'}
    assert staging_names(state) is None
    assert d.ensure(work, state)['event'] == 'created'


def test_programming_error_propagates_and_is_never_an_allow(dirs):
    work, state = dirs

    def broken(sfd, rnd, wfd):
        raise KeyError('bug')
    with pytest.raises(KeyError):
        d.ensure(work, state, hooks={'before_publish': broken})
    with pytest.raises(KeyError):
        d.ensure_placeholder(state, work, hooks={'before_publish': broken})
    with pytest.raises(TypeError):
        with d.stage('verify', 'identity_mismatch'):
            raise TypeError('bug')


def test_every_os_error_in_a_stage_is_converted():
    with pytest.raises(d.Refused) as e:
        with d.stage('verify', 'verify_failed'):
            raise PermissionError(errno.EACCES, 'secret path /x/y')
    assert (e.value.stage, e.value.reason, e.value.errno) == ('verify', 'verify_failed', 'EACCES')
    assert '/x/y' not in str(e.value)


# === runner entry points and logs ==================================================================================
def placeholder_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith('runner_dot_codex_placeholder ')]


def parse(line):
    return json.loads(line.split(' ', 1)[1])


def test_entry_points_refuse_as_policy_refused(dirs, caplog):
    work, state = dirs
    os.mkdir(codex(work), 0o755)
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        with pytest.raises(CodexFailure) as e:
            d.ensure_placeholder(state, work)
        assert e.value.reason == 'policy_refused'
        with pytest.raises(CodexFailure):
            d.check_placeholder(state, work, 'turn_pre')
    messages = [r.getMessage() for r in caplog.records]
    assert messages.count('runner_policy_check {"result": "refused", "check": "local.workspace_dot_codex"}') == 2
    assert [parse(m) for m in placeholder_lines(caplog)] == [
        {'event': 'refused', 'phase': 'start', 'stage': 'name_lookup', 'reason': 'not_runner_owned'},
        {'event': 'refused', 'phase': 'turn_pre', 'stage': 'name_lookup', 'reason': 'not_runner_owned'}]


def test_entry_points_log_created_matched_absent_per_phase(dirs, caplog):
    work, state = dirs
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        assert d.check_placeholder(state, work, 'start') == {'event': 'absent'}
        ino = d.ensure_placeholder(state, work)['ino']
        for phase in ('start', 'launch', 'turn_pre'):
            assert d.check_placeholder(state, work, phase) == {'event': 'matched', 'ino': ino}
    assert [parse(m) for m in placeholder_lines(caplog)] == [
        {'event': 'absent', 'phase': 'start'}, {'event': 'created', 'phase': 'start', 'ino': ino},
        {'event': 'matched', 'phase': 'start', 'ino': ino}, {'event': 'matched', 'phase': 'launch', 'ino': ino},
        {'event': 'matched', 'phase': 'turn_pre', 'ino': ino}]


def test_no_path_or_content_in_any_log(tmp_path, caplog):
    """PM note c: every event and refusal stage of this module, with a marker in the root path and in a file inside a
    non-empty `.codex`; no marker and no path component reaches the log; each line has only the fixed keys."""
    marker_root, marker_content = 'MARKERROOT' + os.urandom(4).hex(), 'MARKERCONTENT' + os.urandom(4).hex()
    base = tmp_path / marker_root
    cases = []

    def fresh(label):
        name = 'case' + os.urandom(4).hex()  # a unique directory name, never a log word
        state, work = base / name / 'state', base / name / 'work'
        state.mkdir(parents=True, mode=0o700)
        work.mkdir(mode=0o700)
        cases.append(name)
        return str(work), str(state)

    def fsync_fail(fd):
        raise OSError(errno.EIO, 'injected ' + marker_root)

    def replace_fail(a, b):
        raise OSError(errno.EIO, 'injected ' + marker_root)

    def swap_staging(sfd, rnd, wfd):
        os.rename(rnd, 'moved', src_dir_fd=sfd, dst_dir_fd=sfd)
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        work, state = fresh('ok')
        d.check_placeholder(state, work, 'start')                                   # absent
        d.ensure_placeholder(state, work)                                          # created
        d.check_placeholder(state, work, 'turn_pre')                               # matched
        os.rename(codex(work), os.path.join(work, 'kept'))
        d.ensure_placeholder(state, work)                                          # created + replaced
        open(os.path.join(codex(work), marker_content), 'w').write(marker_content)
        recorded = d.read_record(state)[1]['mtime_ns']
        os.utime(codex(work), ns=(recorded, recorded))  # the mtime restored: only the content differs
        for call in (lambda: d.check_placeholder(state, work, 'launch'),          # not_empty
                     lambda: d.ensure_placeholder(state, work)):
            with pytest.raises(CodexFailure):
                call()
        refusals = [
            ('nr', lambda w, s: os.mkdir(codex(w), 0o755), {}),                    # not_runner_owned
            ('corrupt', lambda w, s: open(os.path.join(s, d.RECORD), 'w').write(marker_content), {}),
            ('staging', lambda w, s: open(os.path.join(s, d.STAGING), 'w').close(), {}),
            ('left', lambda w, s: None, {'hooks': {'before_publish': swap_staging}}),
            ('sync', lambda w, s: None, {'hooks': {'publish_fsync': fsync_fail}}),
            ('rec', lambda w, s: None, {'hooks': {'ops': {'replace': replace_fail}}}),
        ]
        for name, prepare, kw in refusals:
            w, s = fresh(name)
            prepare(w, s)
            with pytest.raises(CodexFailure):
                d.ensure_placeholder(s, w, **kw)
        w, s = fresh('perm')
        os.chmod(w, 0)
        try:
            with pytest.raises(CodexFailure):
                d.check_placeholder(s, w, 'start')
        finally:
            os.chmod(w, 0o700)
    text = caplog.text + ''.join(r.getMessage() for r in caplog.records)
    assert marker_root not in text and marker_content not in text
    for part in Path(str(tmp_path)).parts[1:] + tuple(cases):
        assert ('/' + part) not in text and ('"' + part + '"') not in text
    lines = placeholder_lines(caplog)
    events = {parse(x)['event'] for x in lines}
    reasons = {parse(x).get('reason') for x in lines}
    assert events == {'absent', 'created', 'matched', 'refused'}
    assert {'not_empty', 'not_runner_owned', 'record_invalid', 'staging_failed', 'publish_failed',
            'publish_sync_failed', 'record_write_failed', 'workspace_unavailable'} <= reasons
    assert any(parse(x).get('staging_left') for x in lines) and any(parse(x).get('replaced') for x in lines)
    assert any(parse(x).get('when') == 'before_replace' for x in lines)
    for x in lines:
        value = parse(x)
        assert isinstance(value, dict) and set(value) <= d.LOG_KEYS and value['event'] in (
            'absent', 'created', 'matched', 'refused')
        assert all(isinstance(v, (str, int, bool)) for v in value.values())


def test_reentry_in_the_same_root_and_from_a_fresh_process(dirs):
    work, state = dirs
    ino = d.ensure(work, state)['ino']
    assert d.ensure(work, state) == {'event': 'matched', 'ino': ino}  # a second Native in the same Pod
    code = 'import json,sys; from runner import dot_codex as d; print(json.dumps(d.ensure(sys.argv[1], sys.argv[2])))'
    out = subprocess.run([sys.executable, '-B', '-c', code, work, state], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, PYTHONPATH=str(RUNNER.parent)))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {'event': 'matched', 'ino': ino}  # a "new Pod" process on the same root


def test_restore_procedure_of_readme_section_6(tmp_path):
    """r1 (review code r0 blocker): a full-state copy refuses with identity_mismatch and changes nothing; the README
    section 6 restore steps (rename `.codex` aside, leave the record) then start again with a new placeholder. This
    runs the same helper the integration restore tests use, with the real first step of Native.__init__
    (ensure_placeholder) as the start; the full Native restore runs in review's integration tier."""
    import shutil
    sys.path.insert(0, str(RUNNER.parent))
    from tests.test_runner_protocol import start_after_restore
    root = tmp_path / 'owner'
    (root / 'workspace').mkdir(parents=True)
    root.chmod(0o700)
    old = d.ensure_placeholder(root, root / 'workspace')['ino']
    (root / 'history.json').write_text('{"turns": 3}')
    restored = tmp_path / 'restored'
    shutil.copytree(root, restored)
    started = start_after_restore(restored, lambda: d.ensure_placeholder(restored, restored / 'workspace'))
    assert started['event'] == 'created' and started['replaced'] == old
    assert (restored / 'history.json').read_text() == '{"turns": 3}'
    assert d.check(str(root / 'workspace'), str(root)) == {'event': 'matched', 'ino': old}  # the source untouched


# === app.py wiring (design g2 §2) =================================================================================
def native_function(name):
    tree = ast.parse((RUNNER / 'app.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Native')
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def call_lines(node, name):
    return sorted(c.lineno for c in ast.walk(node) if isinstance(c, ast.Call) and (
        (isinstance(c.func, ast.Attribute) and c.func.attr == name) or (isinstance(c.func, ast.Name) and c.func.id == name)))


def test_init_runs_ensure_after_the_dirs_before_check_local_auth_and_launch():
    init = native_function('__init__')
    ensure, = call_lines(init, 'ensure_placeholder')
    mkdir = max(call_lines(init, 'mkdir'))
    assert mkdir < ensure < call_lines(init, 'check_local')[0] < call_lines(init, 'attach')[0] \
        < call_lines(init, 'launch_process')[0]
    assert 'dot_codex.ensure_placeholder(root, work)' in ast.unparse(init)


def test_check_local_uses_the_placeholder_check_with_its_phase():
    local = ast.unparse(native_function('check_local'))
    assert 'dot_codex.check_placeholder(self.root, self.work, phase)' in local
    assert "self.work / '.codex'" not in local and 'local.parent_dot_codex' in local
    assert 'self.check_local(phase)' in ast.unparse(native_function('check_policy'))
    assert "self.check_policy('turn_pre')" in ast.unparse(native_function('run_turn'))
    assert 'self.check_policy()' in ast.unparse(native_function('launch_process'))  # phase 'launch' by default
