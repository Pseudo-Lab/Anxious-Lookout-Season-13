"""Real pinned bundle guard, no auth/native launch when dependency is untrusted."""
import builtins
import io

import pytest
from app.codex_policy import MODEL, CodexFailure
from runner.app import Native


@pytest.mark.parametrize('failure', ['missing-host', 'modified-host', 'nonexecutable-host', 'modified-native'])
def test_real_native_rejects_bad_bundle_before_auth_or_process(tmp_path, monkeypatch, failure):
    host = '/usr/local/bin/codex-code-mode-host'
    original_open = builtins.open
    original_access = __import__('os').access
    calls = []

    def guarded_open(path, *args, **kwargs):
        if path == host and failure == 'missing-host':
            raise FileNotFoundError()
        if (path == host and failure == 'modified-host') or (path == '/usr/local/bin/codex' and failure == 'modified-native'):
            return io.BytesIO(b'untrusted executable')
        return original_open(path, *args, **kwargs)

    class Auth:
        def attach(self, *args):
            calls.append('auth')
            raise AssertionError('Bundle refusal must precede auth effects')

    def launch(*args, **kwargs):
        calls.append('process')
        raise AssertionError('Bundle refusal must precede native effects')

    monkeypatch.setenv('APP_ENV', 'personal-test')
    monkeypatch.setenv('CODEX_EXECUTION_SCOPE', 'personal-private')
    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr('runner.app.os.access', lambda path, mode: False if path == host and failure == 'nonexecutable-host' else original_access(path, mode))
    monkeypatch.setattr('runner.app.subprocess.Popen', launch)
    root = tmp_path/'unstarted'
    with pytest.raises(CodexFailure, match='codex_policy_refused'):
        Native(root, MODEL, '/usr/local/bin/codex', 'https://fixture.invalid/tools', auth=Auth())
    assert calls == [] and not root.exists()
