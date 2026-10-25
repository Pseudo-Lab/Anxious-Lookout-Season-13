"""#59: executor PID 1 cleans its namespace and exits 0 on SIGTERM instead of waiting for SIGKILL."""
import signal

import pytest
from executor import server


def test_terminate_cleans_descendants_then_exits_zero(monkeypatch):
    calls = []
    monkeypatch.setattr(server, 'clean_namespace', lambda: calls.append('clean'))
    def exit_(code):
        calls.append(('exit', code))
        raise SystemExit(code)
    monkeypatch.setattr(server.os, '_exit', exit_)
    with pytest.raises(SystemExit):
        server.terminate(signal.SIGTERM, None)
    assert calls == ['clean', ('exit', 0)]


def test_serve_installs_the_handler_before_listening(monkeypatch):
    installed = {}
    monkeypatch.setattr(server.os, 'getpid', lambda: 1)
    monkeypatch.setattr(server.os, 'getuid', lambda: server.EXECUTOR_UID)
    monkeypatch.setattr(server, 'WORKSPACE', type('W', (), {'resolve': lambda self, strict: None})())
    monkeypatch.setattr(server.signal, 'signal', lambda signum, handler: installed.setdefault(signum, handler))
    class Stop(Exception): pass
    def no_socket(*args):
        raise Stop()
    monkeypatch.setattr(server.socket, 'socket', no_socket)
    with pytest.raises(Stop):
        server.Executor().serve()
    assert installed == {signal.SIGTERM: server.terminate}
