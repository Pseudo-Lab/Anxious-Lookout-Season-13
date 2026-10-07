import hashlib
import os
import subprocess
import time
from pathlib import Path

SCRIPT = "/app/ops/db-backup.sh"


def environment(tmp_path, mode="success"):
    return {**os.environ, "COMPOSE_FILE": "/fixture.yml", "BACKUP_PROJECT_NAME": "fixture-only", "BACKUP_FIXTURE_MODE": mode, "BACKUP_FIXTURE_DIR": str(tmp_path)}


def run_backup(tmp_path, mode="success"):
    return subprocess.run(["bash", SCRIPT, "docker", str(tmp_path / "auth.dump")], env=environment(tmp_path, mode), capture_output=True)


def assert_clean(tmp_path):
    assert not (tmp_path / "auth.dump.lock").exists()
    assert not list(tmp_path.glob(".auth-backup-*"))


def test_existing_dump_preserved(tmp_path):
    archive = tmp_path / "auth.dump"
    archive.write_bytes(b"original dump")
    assert run_backup(tmp_path).returncode != 0
    assert archive.read_bytes() == b"original dump"
    assert_clean(tmp_path)


def test_existing_checksum_preserved(tmp_path):
    checksum = tmp_path / "auth.dump.sha256"
    checksum.write_bytes(b"original checksum")
    assert run_backup(tmp_path).returncode != 0
    assert checksum.read_bytes() == b"original checksum"
    assert not (tmp_path / "auth.dump").exists()
    assert_clean(tmp_path)


def test_symlink_destination_preserved(tmp_path):
    original = tmp_path / "valuable"
    original.write_bytes(b"keep")
    checksum = tmp_path / "auth.dump.sha256"
    checksum.symlink_to(original)
    assert run_backup(tmp_path).returncode != 0
    assert checksum.is_symlink() and original.read_bytes() == b"keep"


def test_failed_producer_removes_only_owned_temporaries(tmp_path):
    marker = tmp_path / "unrelated"
    marker.write_bytes(b"keep")
    assert run_backup(tmp_path, "fail").returncode != 0
    assert not (tmp_path / "auth.dump").exists()
    assert not (tmp_path / "auth.dump.sha256").exists()
    assert marker.read_bytes() == b"keep"
    assert_clean(tmp_path)


def test_success_checksum_and_private_mode(tmp_path):
    assert run_backup(tmp_path).returncode == 0
    archive = tmp_path / "auth.dump"
    assert archive.read_bytes() == b"new fixture dump"
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (tmp_path / "auth.dump.sha256").read_text() == expected + "  " + str(archive) + "\n"
    assert archive.stat().st_mode & 0o777 == 0o600
    assert_clean(tmp_path)


def wait_started(tmp_path, process):
    for _ in range(300):
        if (tmp_path / "started").exists():
            return
        assert process.poll() is None
        time.sleep(0.01)
    raise AssertionError("Fixture did not start")


def test_concurrent_execution_does_not_delete_other_run_lock(tmp_path):
    first = subprocess.Popen(["bash", SCRIPT, "docker", str(tmp_path / "auth.dump")], env=environment(tmp_path, "block"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_started(tmp_path, first)
        assert run_backup(tmp_path).returncode != 0
        assert (tmp_path / "auth.dump.lock").is_dir()
        (tmp_path / "release").touch()
        first.communicate(timeout=10)
        assert first.returncode == 0
        assert (tmp_path / "auth.dump").read_bytes() == b"new fixture dump"
        assert_clean(tmp_path)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()


def test_checksum_created_during_dump_is_preserved(tmp_path):
    first = subprocess.Popen(["bash", SCRIPT, "docker", str(tmp_path / "auth.dump")], env=environment(tmp_path, "block"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_started(tmp_path, first)
        checksum = tmp_path / "auth.dump.sha256"
        checksum.write_bytes(b"external checksum")
        (tmp_path / "release").touch()
        first.communicate(timeout=10)
        assert first.returncode != 0
        assert checksum.read_bytes() == b"external checksum"
        assert not (tmp_path / "auth.dump").exists()
        assert_clean(tmp_path)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()
