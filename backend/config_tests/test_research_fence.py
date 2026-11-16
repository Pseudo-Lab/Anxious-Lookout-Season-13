"""Private control file/fault-injection tests; API settlement remains unconnected."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.codex_policy import CodexFailure
from runner.research_authority import AuthorityError, Call
from runner.research_fence import BINDING, MutationFence


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    owner, state = str(uuid4()), str(uuid4())
    fence = MutationFence(tmp_path, owner, state)
    work = SimpleNamespace(session=str(uuid4()), request=str(uuid4()), generation='native-generation',
        call=Call.of('thread', 'turn', 'call', 'research_document_save', {'title': 'private'}),
        token='this-token-is-never-persisted')
    yield fence, work


def intent(fence, work):
    return fence.begin(work, str(uuid4()), 'a' * 64)


def binding(value):
    return {key: value[key] for key in BINDING}


def test_file_and_directory_fsync_complete_before_send(setup, monkeypatch):
    fence, work = setup
    original_fsync, order = os.fsync, []
    def sync(descriptor):
        import stat
        order.append('directory' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file')
        original_fsync(descriptor)
    monkeypatch.setattr(os, 'fsync', sync)
    intent(fence, work)
    order.append('HTTP-send')  # Caller receives no permission to send until begin returned.
    assert order == ['file', 'directory', 'HTTP-send']
    assert os.stat(fence.path).st_mode & 0o777 == 0o600
    contents = fence.path.read_text()
    assert work.token not in contents and 'private' not in contents


@pytest.mark.parametrize('failure', ['file', 'directory', 'rename'])
def test_intent_sync_or_publication_failure_means_dispatch_zero(setup, monkeypatch, failure):
    fence, work = setup
    original_fsync = os.fsync
    def sync(descriptor):
        import stat
        phase = 'directory' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file'
        if phase == failure:
            raise OSError('injected storage failure')
        original_fsync(descriptor)
    monkeypatch.setattr(os, 'fsync', sync)
    if failure == 'rename':
        monkeypatch.setattr(os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('rename failed')))
    sent = []
    with pytest.raises(OSError):
        intent(fence, work)
        sent.append('HTTP')
    assert sent == []
    # A dir fsync failure may have published a fence; it is never discarded.
    if failure == 'directory':
        assert fence.pending() is not None


def test_process_or_pod_restart_preserves_unsettled_intent(setup):
    fence, work = setup
    value = intent(fence, work)
    restarted = MutationFence(fence.control, fence.owner, fence.state)
    assert restarted.pending() == value
    with pytest.raises(AuthorityError, match='mutation_unsettled'):
        intent(restarted, work)


@pytest.mark.parametrize('outcome', ['applied', 'not_applied'])
def test_exact_terminal_ack_is_durable_and_duplicate_safe(setup, outcome):
    fence, work = setup
    value = intent(fence, work)
    terminal = fence.terminal(binding(value), outcome)
    assert terminal['phase'] == 'terminal'
    assert fence.pending() is None
    restarted = MutationFence(fence.control, fence.owner, fence.state)
    assert restarted.terminal(binding(value), outcome) == terminal
    assert restarted.pending() is None
    new = intent(restarted, work)
    assert new['fenceId'] != value['fenceId']
    with pytest.raises(AuthorityError, match='settlement_mismatch'):
        restarted.terminal(binding(value), outcome)
    assert restarted.pending() == new


@pytest.mark.parametrize('field', sorted(BINDING))
def test_foreign_or_stale_binding_cannot_clear_fence(setup, field):
    fence, work = setup
    value = intent(fence, work)
    forged = binding(value)
    forged[field] = {'generation': 'other'} if field == 'native' else 'other'
    with pytest.raises(AuthorityError, match='settlement_mismatch'):
        fence.terminal(forged, 'not_applied')
    assert fence.pending() == value


def test_conflicting_terminal_outcome_is_rejected(setup):
    fence, work = setup
    value = intent(fence, work)
    fence.terminal(binding(value), 'applied')
    with pytest.raises(AuthorityError, match='settlement_mismatch'):
        fence.terminal(binding(value), 'not_applied')


def test_terminal_sync_failure_does_not_acknowledge_success(setup, monkeypatch):
    fence, work = setup
    value = intent(fence, work)
    monkeypatch.setattr(os, 'fsync', lambda _: (_ for _ in ()).throw(OSError('sync failed')))
    confirmed = []
    with pytest.raises(OSError):
        fence.terminal(binding(value), 'applied')
        confirmed.append('ACK')
    assert confirmed == []
    assert fence.pending() == value


def test_terminal_directory_sync_failure_never_bypasses_durable_admission(setup, monkeypatch):
    import stat
    fence, work = setup
    value = intent(fence, work)
    original_sync = os.fsync
    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError('directory sync failed')
        original_sync(descriptor)
    with monkeypatch.context() as context:
        context.setattr(os, 'fsync', fail_directory)
        with pytest.raises(OSError): fence.terminal(binding(value), 'applied')
        with pytest.raises(OSError): fence.pending()
        with pytest.raises(OSError): fence.terminal(binding(value), 'applied')
    assert fence.terminal(binding(value), 'applied')['outcome'] == 'applied'
    assert fence.pending() is None


def test_control_binding_and_corrupt_state_fail_closed(setup):
    fence, work = setup
    intent(fence, work)
    with pytest.raises(AuthorityError, match='fence_binding'):
        MutationFence(fence.control, str(uuid4()), fence.state).pending()
    fence.path.write_text('{"version":1,"version":2}')
    with pytest.raises(AuthorityError): fence.pending()


@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink'])
def test_linked_state_is_not_adopted(setup, link_kind):
    fence, work = setup
    intent(fence, work)
    other = fence.path.with_name('other.json')
    if link_kind == 'symlink':
        fence.path.rename(other)
        fence.path.symlink_to(other)
    else:
        os.link(fence.path, other)
    with pytest.raises((CodexFailure, OSError)):
        fence.pending()
