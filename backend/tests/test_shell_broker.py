import json
import uuid
import pytest
from app.page_scope import shell_allowed
from runner.shell_broker import ShellBroker, arguments_valid
from runner.app import Native, Turn
from tests.test_runner_protocol import wait_idle


@pytest.mark.parametrize('arguments', [None, {}, {'command': ''}, {'command': 'id', 'cwd': '/control'},
    {'command': 'id', 'cwd': '../codex'}, {'command': 'id', 'token': 'no'}, {'command': '\x00'}])
def test_invalid_shell_input(arguments):
    assert not arguments_valid(arguments)


def test_public_and_missing_scope_have_no_shell():
    assert not shell_allowed(None)
    assert not shell_allowed({'page': {'kind': 'public_document'}})
    assert shell_allowed({'page': {'kind': 'general'}})


def test_revoked_lease_never_reaches_authorization_or_executor(tmp_path):
    calls = []
    broker = ShellBroker(tmp_path / 'broker.sock', lambda turn, args: calls.append(turn), lambda value: value)
    try:
        first = broker.activate({'session': 'first'})
        current = broker.activate({'session': 'second'})
        assert broker.execute({'lease': first, 'arguments': {'command': 'id'}})['error']['code'] == 'tool_not_allowed'
        assert not calls
        broker.revoke()
        assert broker.execute({'lease': current, 'arguments': {'command': 'id'}})['error']['code'] == 'tool_not_allowed'
        assert not calls
    finally: broker.close()


def test_v2_native_keeps_thread_but_isolates_config_and_resets_registry(tmp_path, monkeypatch):
    monkeypatch.setenv('SHELL_EXECUTOR_ENABLED', 'true')
    native = Native(tmp_path / 'state', 'gpt-6.1-sol', '/usr/local/bin/codex-fixture', 'https://fixture.invalid/tools')
    session = str(uuid.uuid4())
    try:
        assert native.work == tmp_path / 'state/native-work'
        threads = []
        processes = []
        for kind in ('general', 'public_document', 'general'):
            scope = {'page': {'kind': kind}}
            if kind == 'public_document': scope['page']['resourceId'] = str(uuid.uuid4())
            native.accept(session, Turn(requestId=uuid.uuid4(), text='hello', tools=[], toolToken='t'*40, scope=scope))
            result = wait_idle(native, session)
            threads.append(result['threadId'])
            processes.append(native.process.pid)
            assert native.shell_broker.lease is None
        assert len(set(threads)) == 1
        assert len(set(processes)) == 3
    finally: native.close()


def test_prepare_shell_keeps_root_private_and_refuses_symlink(tmp_path):
    import os
    import stat
    from runner.prepare_shell import prepare
    assert os.getuid() == 10001 and os.getgid() == 10001
    root = tmp_path / 'state'
    root.mkdir(mode=0o700)
    prepare(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root/'workspace').stat().st_mode) == 0o2770
    assert stat.S_IMODE((root/'shell-ipc').stat().st_mode) == 0o2770
    (root/'workspace').rmdir()
    (root/'workspace').symlink_to(tmp_path)
    with pytest.raises(RuntimeError): prepare(root)
