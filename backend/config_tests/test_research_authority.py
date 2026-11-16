"""Pure Linux IPC/permit tests; no Codex/model/API/DB/network requests.

These establish local state/transport boundaries. They do not prove native MCP
startup, approval, actual metadata or the deployed FS/proc/FD sandbox boundary.
"""
import json
import os
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from runner.research_authority import (
    AuthorityError, Bootstrap, Call, Epochs, PeerConnection, ProcessIdentity,
    canonical, strict_json,
)


CHILD = '''
import json, os, socket, sys
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(sys.argv[1])
for line in sys.stdin:
    if line == "STOP\\n": break
    if json.loads(line) == {"testFork": True}:
        pid = os.fork()
        if pid == 0:
            connection.sendall(b"{}\\n")
            os._exit(0)
        os.waitpid(pid, 0)
        continue
    connection.sendall(line.encode())
'''


@pytest.fixture
def registration(tmp_path):
    tmp_path.chmod(0o700)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    address = str(tmp_path / 'test.sock')
    listener.bind(address)
    listener.listen(8)
    listener.settimeout(2)
    bootstrap, children, peers = Bootstrap(tmp_path), [], []
    bootstrap.arm(os.getpid(), 'generation-1', address)

    def connect():
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', CHILD, address],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, text=True)
        children.append(child)
        connection, _ = listener.accept()
        connection.settimeout(2)
        peer = PeerConnection(connection)
        peers.append(peer)
        return child, peer

    def send(child, value):
        child.stdin.write(json.dumps(value) + '\n')
        child.stdin.flush()

    yield SimpleNamespace(bootstrap=bootstrap, connect=connect, send=send,
                          message=lambda: {'operation': 'register', 'nonce': bootstrap.nonce})
    bootstrap.close()
    for peer in peers:
        peer.close()
    listener.close()
    for child in children:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)
        child.stdin.close()


def register(fixture):
    child, peer = fixture.connect()
    message = fixture.message()
    fixture.send(child, message)
    fixture.bootstrap.register(peer, peer.receive())
    return child, peer, message


def test_real_child_registration_consumes_nonce_before_model_ready(registration):
    bootstrap = registration.bootstrap
    child, peer, original = register(registration)
    assert peer.credentials[0] == child.pid
    assert peer.identity.parent == os.getpid()
    assert bootstrap.nonce is None and not bootstrap.path.exists()
    assert not peer.socket.get_inheritable()
    assert not os.get_inheritable(peer.identity.fd)
    with pytest.raises(AuthorityError, match='bootstrap_incomplete'):
        bootstrap.activate('generation-1')  # Child assertion is not catalogue proof.
    bootstrap.catalogue_ready('generation-1')
    assert bootstrap.activate('generation-1') is peer
    assert original['nonce'] not in repr(vars(peer))


@pytest.mark.parametrize('bad', [None, {}, {'operation': 'call', 'nonce': 'x' * 64},
                               {'operation': 'register', 'nonce': 'x' * 64},
                               {'operation': 'register', 'nonce': '한글'}])
def test_same_uid_same_argv_first_peer_without_nonce_never_adopted(registration, bad):
    child, peer = registration.connect()
    registration.send(child, bad)
    with pytest.raises(AuthorityError, match='registration_refused'):
        registration.bootstrap.register(peer, peer.receive())
    assert peer.identity is None
    assert registration.bootstrap.phase == 'bootstrap'
    # A failed attacker does not reserve the actual child slot.
    _, valid_peer, _ = register(registration)
    assert registration.bootstrap.peer is valid_peer


def test_correct_nonce_still_requires_exact_parent_not_just_uid(registration, monkeypatch):
    child, peer = registration.connect()
    registration.send(child, registration.message())
    # Simulate a verified process with a different immediate parent. No relaxation
    # to a descendant/argv/UID-only predicate is allowed.
    original_alive = ProcessIdentity.alive
    original_init = ProcessIdentity.__init__
    def changed_init(identity, pid):
        original_init(identity, pid)
        if pid == child.pid:
            identity.parent = os.getppid()
    monkeypatch.setattr(ProcessIdentity, '__init__', changed_init)
    monkeypatch.setattr(ProcessIdentity, 'alive', lambda identity: True if identity.pid == child.pid else original_alive(identity))
    with pytest.raises(AuthorityError, match='registration_refused'):
        registration.bootstrap.register(peer, peer.receive())
    assert peer.identity is None


def test_registration_is_one_use_and_never_reopens_during_active_turn(registration):
    bootstrap = registration.bootstrap
    _, _, original = register(registration)
    bootstrap.catalogue_ready('generation-1')
    bootstrap.activate('generation-1')
    other, other_peer = registration.connect()
    registration.send(other, original)
    with pytest.raises(AuthorityError, match='registration_refused'):
        bootstrap.register(other_peer, other_peer.receive())
    with pytest.raises(AuthorityError, match='bootstrap_phase'):
        bootstrap.arm(os.getpid(), 'generation-2', '/unused')
    assert other_peer.identity is None


def test_simultaneous_nonce_registrations_choose_exactly_one(registration):
    bootstrap = registration.bootstrap
    message = registration.message()
    peers = []
    for _ in range(2):
        child, peer = registration.connect()
        registration.send(child, message)
        peers.append((peer, peer.receive()))
    start, outcomes = threading.Barrier(3), []
    def attempt(peer, received):
        start.wait(timeout=2)
        try:
            bootstrap.register(peer, received)
            outcomes.append('registered')
        except AuthorityError:
            outcomes.append('refused')
    workers = [threading.Thread(target=attempt, args=entry) for entry in peers]
    for worker in workers: worker.start()
    start.wait(timeout=2)
    for worker in workers: worker.join(timeout=2)
    assert sorted(outcomes) == ['refused', 'registered']


def test_expired_nonce_and_wrong_generation_do_not_open_model_admission(registration):
    child, peer = registration.connect()
    registration.send(child, registration.message())
    registration.bootstrap.deadline = 0
    with pytest.raises(AuthorityError, match='registration_refused'):
        registration.bootstrap.register(peer, peer.receive())
    with pytest.raises(AuthorityError, match='bootstrap_incomplete'):
        registration.bootstrap.catalogue_ready('generation-2')


def test_child_exit_invalidates_registered_identity_even_if_pid_number_matches(registration):
    child, peer, _ = register(registration)
    bootstrap = registration.bootstrap
    bootstrap.catalogue_ready('generation-1')
    child.terminate()
    child.wait(timeout=3)
    assert not peer.alive()
    with pytest.raises(AuthorityError, match='bootstrap_incomplete'):
        bootstrap.activate('generation-1')


def test_starttime_change_invalidates_identity(registration, monkeypatch):
    _, peer, _ = register(registration)
    from runner import research_authority
    before = research_authority._process_stat(peer.identity.pid)
    monkeypatch.setattr(research_authority, '_process_stat', lambda _: (before[0], before[1] + 1))
    assert not peer.alive()


def test_process_generation_and_reset_invalidate_ready_peer(registration):
    bootstrap = registration.bootstrap
    _, peer, _ = register(registration)
    bootstrap.catalogue_ready('generation-1')
    with pytest.raises(AuthorityError, match='bootstrap_incomplete'):
        bootstrap.activate('generation-2')
    bootstrap.close()
    assert peer.identity is None
    with pytest.raises(AuthorityError, match='bootstrap_incomplete'):
        bootstrap.activate('generation-1')


def test_descriptor_handoff_is_rejected_using_actual_writer_credentials(registration):
    # SO_PEERCRED still reports the *original* child after another process writes
    # its connected socket. Prove the additional SCM_CREDENTIALS check matters.
    bootstrap = registration.bootstrap
    from_child, peer = registration.connect()
    registration.send(from_child, registration.message())
    bootstrap.register(peer, peer.receive())
    # Malicious fixture child forks and writes through its inherited connection.
    # This tests local kernel IPC, not Codex's deployed proc/fd isolation.
    registration.send(from_child, {'testFork': True})
    with pytest.raises(AuthorityError, match='peer_changed'):
        peer.receive()
    assert peer.credentials[0] == from_child.pid and peer.identity.alive()


@pytest.mark.parametrize('data', [b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}',
                               b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}'])
def test_authorization_json_rejects_ambiguous_keys_and_non_json_numbers(data):
    with pytest.raises(AuthorityError):
        strict_json(data)


def make_epoch(epochs, **overrides):
    fields = dict(generation='generation-1', session='conversation-1', request='request-1',
                  token='original-api-token', scope={'page': {'kind': 'general'}},
                  thread='native-thread', peer=SimpleNamespace(alive=lambda: True))
    fields.update(overrides)
    return epochs.begin(**fields)


def test_registered_child_exit_before_claim_never_issues_work(registration):
    child, peer, _ = register(registration)
    registration.bootstrap.catalogue_ready('generation-1')
    registration.bootstrap.activate('generation-1')
    epochs = Epochs()
    epoch = make_epoch(epochs, peer=peer)
    epochs.bind(epoch, 'native-turn')
    message = observe(epochs, epoch)
    child.terminate()
    child.wait(timeout=3)
    assert not peer.alive()
    with pytest.raises(AuthorityError, match='peer_exited'):
        epochs.claim(epoch, message)
    assert epoch.revoked and epoch.inflight == 0


def test_disconnected_peer_before_claim_never_issues_work(registration):
    _, peer, _ = register(registration)
    epochs = Epochs()
    epoch = make_epoch(epochs, peer=peer)
    epochs.bind(epoch, 'native-turn')
    message = observe(epochs, epoch)
    peer.socket.shutdown(socket.SHUT_RDWR)
    assert peer.identity.alive() and not peer.alive()
    with pytest.raises(AuthorityError, match='peer_exited'):
        epochs.claim(epoch, message)
    assert epoch.revoked and epoch.inflight == 0


def test_parent_identity_invalidation_before_claim_never_issues_work(registration):
    _, peer, _ = register(registration)
    epochs = Epochs()
    epoch = make_epoch(epochs, peer=peer)
    epochs.bind(epoch, 'native-turn')
    message = observe(epochs, epoch)
    registration.bootstrap.parent.close()
    assert peer.identity.alive() and not peer.alive()
    with pytest.raises(AuthorityError, match='peer_exited'):
        epochs.claim(epoch, message)
    assert epoch.revoked and epoch.inflight == 0


def call(**overrides):
    fields = dict(thread='native-thread', turn='native-turn', item='call-1',
                  name='research_list', arguments={'type': 'document'})
    fields.update(overrides)
    return Call.of(**fields)


def observe(epochs, epoch, message=None, order=('event', 'frame')):
    message = message or call()
    for source in order:
        epochs.observe(epoch, source, message, peer=epoch.peer if source == 'frame' else None)
    return message


@pytest.mark.parametrize('order', [('event', 'frame'), ('frame', 'event')])
def test_event_frame_order_inversion_needs_both_and_confirmed_native_turn(order):
    epochs = Epochs()
    epoch = make_epoch(epochs)
    message = observe(epochs, epoch, order=order)
    epochs.bind(epoch, 'native-turn')
    work = epochs.claim(epoch, message)
    assert (work.session, work.request, work.token) == ('conversation-1', 'request-1', 'original-api-token')
    assert work.scope == canonical({'page': {'kind': 'general'}})
    assert epoch.inflight == 1
    epochs.finish(epoch, work, confirmed=True)
    assert epoch.inflight == 0
    with pytest.raises(AuthorityError): epochs.claim(epoch, message)


@pytest.mark.parametrize('only', ['event', 'frame'])
def test_started_only_or_child_only_never_grants_a_permit(only):
    epochs = Epochs()
    epoch = make_epoch(epochs)
    epochs.bind(epoch, 'native-turn')
    epochs.observe(epoch, only, call(), peer=epoch.peer)
    with pytest.raises(AuthorityError, match='permit_unmatched'):
        epochs.claim(epoch, call())
    # A timeout tombstone prevents a late counterpart from reviving the call.
    with pytest.raises(AuthorityError, match='duplicate_or_conflicting_call'):
        epochs.observe(epoch, 'frame' if only == 'event' else 'event', call(), peer=epoch.peer)


def test_pending_or_declined_started_cannot_be_claimed_by_other_peer():
    epochs = Epochs()
    epoch = make_epoch(epochs)
    epochs.bind(epoch, 'native-turn')
    epochs.observe(epoch, 'event', call())
    with pytest.raises(AuthorityError, match='call_refused'):
        epochs.observe(epoch, 'frame', call(), peer=object())
    with pytest.raises(AuthorityError, match='permit_unmatched'):
        epochs.claim(epoch, call())


@pytest.mark.parametrize('source', ['event', 'frame'])
def test_duplicate_before_claim_poisoned_and_after_claim_never_reissued(source):
    epochs = Epochs()
    epoch = make_epoch(epochs)
    epochs.bind(epoch, 'native-turn')
    message = observe(epochs, epoch)
    with pytest.raises(AuthorityError, match='duplicate_or_conflicting_call'):
        epochs.observe(epoch, source, message, peer=epoch.peer)
    with pytest.raises(AuthorityError): epochs.claim(epoch, message)
    message2 = observe(epochs, epoch, call(item='second-call'))
    work = epochs.claim(epoch, message2)
    with pytest.raises(AuthorityError): epochs.observe(epoch, source, message2, peer=epoch.peer)
    epochs.finish(epoch, work, confirmed=True)
    with pytest.raises(AuthorityError): epochs.observe(epoch, source, message2, peer=epoch.peer)
    with pytest.raises(AuthorityError): epochs.claim(epoch, message2)


def test_argument_mismatch_keeps_tombstone_and_arguments_cannot_mutate_after_capture():
    epochs = Epochs()
    epoch = make_epoch(epochs)
    epochs.bind(epoch, 'native-turn')
    arguments = {'nested': {'count': 1}}
    original = call(arguments=arguments)
    epochs.observe(epoch, 'event', original)
    arguments['nested']['count'] = 2
    assert json.loads(original.arguments) == {'nested': {'count': 1}}
    with pytest.raises(AuthorityError):
        epochs.observe(epoch, 'frame', call(arguments=arguments), peer=epoch.peer)
    with pytest.raises(AuthorityError): epochs.claim(epoch, original)


def test_prestart_frame_from_wrong_turn_never_relabels_to_confirmed_turn():
    epochs = Epochs()
    epoch = make_epoch(epochs)
    message = observe(epochs, epoch, call(turn='old-turn'))
    epochs.bind(epoch, 'new-turn')
    with pytest.raises(AuthorityError): epochs.claim(epoch, message)


def test_old_queue_and_work_never_borrow_next_request_token_or_peer():
    epochs = Epochs()
    old = make_epoch(epochs)
    epochs.bind(old, 'native-turn')
    message = observe(epochs, old)
    work = epochs.claim(old, message)
    epochs.revoke(old)
    with pytest.raises(AuthorityError, match='previous_epoch_not_drained'):
        make_epoch(epochs, request='request-2', token='new-token')
    epochs.finish(old, work, confirmed=True)
    new = make_epoch(epochs, request='request-2', token='new-token')
    epochs.bind(new, 'new-turn')
    with pytest.raises(AuthorityError): epochs.observe(old, 'frame', message, peer=old.peer)
    with pytest.raises(AuthorityError): epochs.observe(new, 'frame', message, peer=old.peer)
    assert work.token == 'original-api-token' and work.request == 'request-1'
    assert old.id != new.id


def test_revoke_before_claim_prevents_dispatch_and_claim_before_revoke_keeps_original_work():
    epochs = Epochs()
    first = make_epoch(epochs)
    epochs.bind(first, 'native-turn')
    message = observe(epochs, first)
    epochs.revoke(first)
    with pytest.raises(AuthorityError): epochs.claim(first, message)
    second = make_epoch(epochs)
    epochs.bind(second, 'native-turn')
    message = observe(epochs, second)
    work = epochs.claim(second, message)
    epochs.revoke(second)
    assert second.inflight == 1 and work.epoch == second.id


def test_blocked_callback_does_not_block_native_reader_or_revoke_lock():
    epochs = Epochs()
    epoch = make_epoch(epochs)
    epochs.bind(epoch, 'native-turn')
    message = observe(epochs, epoch)
    dispatched, release, revoked = threading.Event(), threading.Event(), threading.Event()
    def callback():
        work = epochs.claim(epoch, message)
        dispatched.set()
        assert release.wait(3)
        epochs.finish(epoch, work, confirmed=True)
    worker = threading.Thread(target=callback)
    worker.start()
    assert dispatched.wait(2)
    def reader():
        epochs.revoke(epoch)
        revoked.set()
    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        assert revoked.wait(1), 'Reader/revoke blocked behind simulated HTTP'
    finally:
        release.set()
        worker.join(timeout=3)
        reader_thread.join(timeout=3)
    assert epoch.revoked and epoch.inflight == 0


def test_unknown_remote_completion_never_counts_as_drain():
    epochs = Epochs()
    epoch = make_epoch(epochs)
    epochs.bind(epoch, 'native-turn')
    work = epochs.claim(epoch, observe(epochs, epoch))
    epochs.revoke(epoch)
    epochs.finish(epoch, work, confirmed=False)
    assert epoch.inflight == 0 and epoch.unsettled
    with pytest.raises(AuthorityError, match='previous_epoch_not_drained'):
        make_epoch(epochs)


def test_bounded_pending_calls_never_evict_consumed_tombstones():
    epochs = Epochs(limit=1)
    epoch = make_epoch(epochs)
    epochs.bind(epoch, 'native-turn')
    work = epochs.claim(epoch, observe(epochs, epoch))
    epochs.finish(epoch, work, confirmed=True)
    with pytest.raises(AuthorityError, match='permit_limit'):
        epochs.observe(epoch, 'event', call(item='second-call'))
    assert epoch.revoked
    with pytest.raises(AuthorityError): epochs.claim(epoch, call())
