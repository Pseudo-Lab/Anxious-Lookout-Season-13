"""Private URL-copy integrity and real PostgreSQL read-only retry decisions."""
from pathlib import Path
import os
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url

from ops.user_runner_db_route import stage, audit
from runner.lifecycle import Refused
from config_tests.test_runner_registry import schema, admin, owner, t0, enter, preservation_snapshot


def sources(tmp_path):
    source = tmp_path/'inputs'; source.mkdir(mode=0o700)
    for filename, username in [('database-url','anxious_api'), ('admin-database-url','postgres')]:
        path = source/filename
        path.write_text(f'postgresql+psycopg://{username}:private%40pass@postgres.m2-hosting.svc.cluster.local/hosting')
        path.chmod(0o600)
    return source


def test_stage_changes_only_host_port_and_preserves_sources(tmp_path):
    source = sources(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    output = tmp_path/'route'
    stage(source, output, 25413)
    for filename, raw in before.items():
        original = make_url(raw.decode())
        routed = make_url((output/filename).read_text())
        assert routed == original.set(host='127.0.0.1', port=25413)
        assert (source/filename).read_bytes() == raw
        assert (output/filename).stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): stage(source, output, 25413)


@pytest.mark.parametrize('damage', ['?hostaddr=10.0.0.1', '?sslmode=verify-full'])
def test_unknown_connection_overrides_are_refused_without_output(tmp_path, damage):
    source = sources(tmp_path)
    path = source/'database-url'; path.write_text(path.read_text()+damage)
    with pytest.raises(Refused): stage(source, tmp_path/'route', 25413)
    assert not (tmp_path/'route').exists()


def test_audit_allows_only_absent_owner_and_never_mutates_registry(admin, owner, t0):
    url = os.environ['ADMIN_DATABASE_URL']
    before = preservation_snapshot(admin)
    result = audit(url, owner)
    assert result == {'runtimeSchemaPresent': True, 'ownerRegistryPresent': False,
                      'ownerEligible': True, 'retryAllowed': True}
    assert preservation_snapshot(admin) == before
    enter(admin, owner, uuid4(), t0)
    before = preservation_snapshot(admin)
    assert audit(url, owner)['retryAllowed'] is False
    assert preservation_snapshot(admin) == before
    assert audit(url, uuid4())['retryAllowed'] is False
