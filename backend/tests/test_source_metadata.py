"""Synthetic selected auth files only; values stay absent from CLI diagnostics."""
import json
import os
import subprocess
import sys

import pytest

from app.codex_policy import CodexFailure
from ops.source_metadata import inspect_source
from runner.auth import write_private


@pytest.fixture
def selected(tmp_path):
    root = tmp_path / "selected-private"
    root.mkdir(mode=0o700)
    source, binding = root / "auth.json", root / "binding.json"
    write_private(source, {"OPENAI_API_KEY": None, "tokens": {"account_id": "private-provider-sentinel",
        "id_token": "private-id-token-sentinel", "access_token": "private-access-token-sentinel",
        "refresh_token": "private-refresh-token-sentinel"}})
    write_private(binding, {"providerAccountId": "private-provider-sentinel", "sourceUid": os.geteuid()})
    return source, binding


def test_readonly_structure_and_binding_do_not_establish_auth_or_owner(selected):
    source, binding = selected
    before = source.read_bytes()
    result = inspect_source(source, binding)
    assert result["structureCompatible"] and result["providerBindingMatches"]
    assert not result["authenticated"] and result["refreshOwnership"] == "owner_evidence_required"
    assert result["sourceAuthoritative"] == "not_established" and result["modelAccess"] == "not_tested"
    assert "sentinel" not in json.dumps(result) and str(source) not in json.dumps(result)
    assert source.read_bytes() == before


def test_format_only_diagnosis_needs_no_user_supplied_provider_id(selected):
    source, _ = selected
    result = inspect_source(source)
    assert result["structureCompatible"] and not result["bindingProvided"]
    assert not result["providerBindingMatches"] and not result["authenticated"]
    assert "sentinel" not in json.dumps(result)


@pytest.mark.parametrize("damage", ["provider", "key", "missing", "mode"])
def test_incompatible_binding_or_format_fails_without_value_disclosure(selected, damage):
    source, binding = selected
    value = json.loads(source.read_text())
    if damage == "provider": value["tokens"]["account_id"] = "wrong-provider-sentinel"
    elif damage == "key": value["OPENAI_API_KEY"] = "private-key-sentinel"
    elif damage == "missing": value["tokens"].pop("refresh_token")
    else: value["auth_mode"] = "external-token"
    write_private(source, value)
    result = subprocess.run([sys.executable, "-m", "ops.source_metadata", "--source", str(source), "--binding", str(binding)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "sentinel" not in result.stdout + result.stderr and str(source) not in result.stdout + result.stderr


@pytest.mark.parametrize("damage", ["permissions", "symlink", "hardlink", "owner", "malformed"])
def test_unsafe_file_never_yields_admission(selected, damage):
    source, binding = selected
    if damage == "permissions": source.chmod(0o644)
    elif damage == "symlink":
        target = source.with_name("linked.json")
        source.rename(target)
        source.symlink_to(target)
    elif damage == "hardlink": os.link(source, source.with_name("other.json"))
    elif damage == "owner": write_private(binding, {"providerAccountId": "private-provider-sentinel", "sourceUid": os.geteuid() + 1})
    else: source.write_text('{"private-token-sentinel":')
    result = subprocess.run([sys.executable, "-m", "ops.source_metadata", "--source", str(source), "--binding", str(binding)], capture_output=True, text=True)
    assert result.returncode == 1 and "sentinel" not in result.stdout + result.stderr
    assert str(source) not in result.stdout + result.stderr
