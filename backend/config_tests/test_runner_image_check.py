"""#68 runner image build gate (pure). The passing case is the runner image build itself (Dockerfile
`RUN python -m runner.image_check`); here only that it fails closed outside that image."""
import pytest

from runner import image_check


def test_fails_closed_outside_the_runner_image():
    # The unit image has no pinned Codex, no bubblewrap, no sandbox link and no /etc/codex.
    found = image_check.problems()
    assert {'bwrap', 'sandbox link', '/etc/codex directory', 'policy files'} <= set(found)
    assert any(name.startswith('pin') for name in found)


def test_main_exits_non_zero_and_names_each_failure(capsys):
    with pytest.raises(SystemExit) as stopped:
        image_check.main()
    assert stopped.value.code == 1
    assert 'runner image check failed: bwrap' in capsys.readouterr().err


def test_git_in_the_image_is_a_failure(monkeypatch):
    real = image_check.shutil.which
    monkeypatch.setattr(image_check.shutil, 'which', lambda name: '/usr/bin/git' if name == 'git' else real(name))
    assert 'git present' in image_check.problems()
