from cryptography.fernet import Fernet
import pytest

from check_inputs import check


@pytest.fixture
def inputs(tmp_path):
    tmp_path.chmod(0o700)
    for name in ("db", "ops", "api", "github"):
        (tmp_path / name).mkdir(mode=0o700)
    values = {"db/password": "synthetic-admin-password", "ops/api-password": "synthetic-api-password",
        "ops/admin-url": "postgresql+psycopg://postgres:synthetic-admin-password@postgres.codex-trial.svc.cluster.local/codex_trial",
        "api/database-url": "postgresql+psycopg://anxious_api:synthetic-api-password@postgres.codex-trial.svc.cluster.local/codex_trial",
        "github/client-secret": "synthetic-github-secret", "github/transaction-key": Fernet.generate_key().decode()}
    for filename, value in values.items():
        (tmp_path / filename).write_text(value)
        (tmp_path / filename).chmod(0o600)
    return tmp_path


def test_private_inputs_require_new_db_and_separate_role_passwords(inputs):
    assert check(inputs) == {"status": "ok", "scope": "new-trial-db-only", "credentialsPrinted": False,
                             "callbackRegistrationVerified": False}


@pytest.mark.parametrize("filename,value", [("api/database-url", "postgresql+psycopg://anxious_api:synthetic-api-password@postgres.m2-hosting.svc.cluster.local/hosting"),
    ("ops/admin-url", "postgresql+psycopg://postgres:synthetic-admin-password@postgres.m2-hosting.svc.cluster.local/hosting"),
    ("api/database-url", "postgresql+psycopg://postgres:synthetic-api-password@postgres.codex-trial.svc.cluster.local/codex_trial"),
    ("api/database-url", "postgresql+psycopg://anxious_api:wrong-password@postgres.codex-trial.svc.cluster.local/codex_trial"),
    ("github/transaction-key", "wrong-key"), ("github/client-secret", ""), ("ops/api-password", "synthetic-admin-password")])
def test_input_mismatch_is_refused_before_migration(inputs, filename, value):
    (inputs / filename).write_text(value)
    with pytest.raises(ValueError):
        check(inputs)


@pytest.mark.parametrize("mode", ["directory", "file", "symlink", "hardlink"])
def test_nonprivate_or_linked_inputs_refused(inputs, mode):
    target = inputs / "api/database-url"
    if mode == "directory":
        target.parent.chmod(0o755)
    elif mode == "file":
        target.chmod(0o644)
    elif mode == "symlink":
        target.rename(target.parent / "actual")
        target.symlink_to(target.parent / "actual")
    else:
        (target.parent / "alias").hardlink_to(target)
    with pytest.raises(ValueError):
        check(inputs)
