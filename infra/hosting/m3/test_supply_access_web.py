"""Real bash/jq/stat guard with synthetic wrappers; no Docker/kubectl/auth calls."""
import json
import os
import subprocess
from pathlib import Path

import pytest

APPROVED_RUNNER = "docker.io/library/anxious-s13-chatbot-runner-e9f5943@sha256:0158f92ebeeedcf2c237a7ca5847c46ba0c4d4335913af848456071e099bd76c"
LEGACY_RUNNER = "docker.io/library/anxious-s13-access-runner-9157719@sha256:" + "c" * 64
POD_UID = "12345678-1234-4234-8234-123456789abc"

WRAPPERS = """
set -euo pipefail
ACCESS_SUPPLY_DOCKER() {
  printf 'docker\n' >> "$CASE_CALLS"
  printf '%s\n' "$@" > "$CASE_ARGS"
  [[ "$CASE_DAMAGE" != producer-fail ]] || return 7
  printf 'synthetic-noncredential-payload'
}
ACCESS_SUPPLY_KUBECTL() {
  if [[ "$3" == get ]]; then
    count=$(cat "$CASE_GETS")
    printf '%s' "$((count+1))" > "$CASE_GETS"
    if [[ "$count" == 0 ]]; then cat "$CASE_BEFORE"; else cat "$CASE_AFTER"; fi
  elif [[ "$3" == exec ]]; then
    printf 'exec\n' >> "$CASE_CALLS"
    cat >/dev/null
    [[ "$CASE_DAMAGE" != consumer-fail ]] || return 9
    if [[ "$CASE_DAMAGE" == source-changed ]]; then printf '\n' >> "$ACCESS_SUPPLY_SOURCE"; fi
    printf '{"accepted":true}\n'
  else return 97
  fi
}
source "$CASE_SCRIPT"
"""


def run_case(tmp_path, reference=APPROVED_RUNNER, damage=""):
    source = tmp_path / "source.json"
    source.write_text('{"synthetic":"not-an-auth-envelope"}')
    inputs = tmp_path / "inputs"; inputs.mkdir(mode=0o700)
    (inputs / "inputs.json").write_text(json.dumps({"ownerId":"synthetic-owner", "stateId":"synthetic-state",
        "windowId":"synthetic-window", "newWindow":False}))
    pod = {"metadata":{"uid":POD_UID, "labels":{"app":"access-web-runner"}}, "spec":{"containers":[{
        "name":"runner", "image":reference, "env":[{"name":"RUNNER_ACCOUNT_ID","value":"synthetic-owner"},
        {"name":"RUNNER_STATE_ID","value":"synthetic-state"}]}]}, "status":{"phase":"Running"}}
    if damage == "uid": pod["metadata"]["uid"] = "87654321-1234-4234-8234-123456789abc"
    elif damage == "image": pod["spec"]["containers"][0]["image"] = LEGACY_RUNNER
    elif damage == "owner": pod["spec"]["containers"][0]["env"][0]["value"] = "foreign"
    elif damage == "state": pod["spec"]["containers"][0]["env"][1]["value"] = "foreign"
    elif damage == "label": pod["metadata"]["labels"]["app"] = "foreign"
    elif damage == "phase": pod["status"]["phase"] = "Pending"
    elif damage == "containers": pod["spec"]["containers"].append({"name":"unexpected"})
    elif damage == "extra-input": (inputs / ".hidden").write_text("unexpected")
    elif damage == "input-mode": inputs.chmod(0o755)
    elif damage == "source-symlink":
        target = tmp_path / "source-original.json"; source.rename(target); source.symlink_to(target)
    after = json.loads(json.dumps(pod))
    if damage == "after-uid": after["metadata"]["uid"] = "87654321-1234-4234-8234-123456789abc"
    elif damage == "after-image": after["spec"]["containers"][0]["image"] = LEGACY_RUNNER
    before_path, after_path = tmp_path / "before.json", tmp_path / "after.json"
    before_path.write_text(json.dumps(pod)); after_path.write_text(json.dumps(after))
    calls, args, gets = tmp_path / "calls", tmp_path / "args", tmp_path / "gets"
    gets.write_text("0")
    environment = {**os.environ, "ACCESS_SUPPLY_IMAGE":"sha256:"+"a"*64, "ACCESS_SUPPLY_SOURCE":str(source),
        "ACCESS_SUPPLY_INPUT_DIR":str(inputs), "ACCESS_SUPPLY_POD":"access-web-runner-v1-fixture",
        "ACCESS_SUPPLY_POD_UID":POD_UID, "ACCESS_SUPPLY_RUNNER_REF":reference, "CASE_SCRIPT":str(Path(__file__).with_name("supply_access_web.sh")),
        "CASE_DAMAGE":damage, "CASE_CALLS":str(calls), "CASE_ARGS":str(args), "CASE_GETS":str(gets),
        "CASE_BEFORE":str(before_path), "CASE_AFTER":str(after_path)}
    result = subprocess.run(["bash","-c",WRAPPERS], env=environment, capture_output=True, text=True, timeout=10)
    return result, calls.read_text().splitlines() if calls.exists() else [], args.read_text().splitlines() if args.exists() else [], gets.read_text(), source


@pytest.mark.parametrize("reference", [APPROVED_RUNNER, LEGACY_RUNNER])
def test_approved_exact_ref_and_legacy_keep_nonlogged_private_pipe_and_source_stat(tmp_path, reference):
    result, calls, args, gets, source = run_case(tmp_path, reference)
    assert result.returncode == 0 and json.loads(result.stdout) == {"accepted":True}
    assert sorted(calls) == ["docker","exec"] and gets == "2"
    assert source.read_text() == '{"synthetic":"not-an-auth-envelope"}'
    for flag in ("--rm","--pull=never","--log-driver=none","--read-only","--cap-drop","--security-opt","--entrypoint"):
        assert flag in args
    assert args[args.index("--network")+1] == "none"
    assert args[args.index("--user")+1] == f"{os.getuid()}:{os.getgid()}"
    mounts = [args[index+1] for index, value in enumerate(args) if value == "--mount"]
    assert mounts == [f"type=bind,src={source},dst=/selected/source.json,readonly",
                      f"type=bind,src={source.parent / 'inputs'},dst=/operator,readonly"]
    assert args[-8:] == ["-B","-m","ops.access_snapshot","--source","/selected/source.json","--inputs","/operator/inputs.json","--export"]
    assert "synthetic-noncredential-payload" not in result.stdout + result.stderr


@pytest.mark.parametrize("reference", [
    APPROVED_RUNNER.replace("e9f5943","aaaaaaa"),
    APPROVED_RUNNER.replace("0158f92e","1158f92e"),
    APPROVED_RUNNER.split("@")[0]+":latest",
    APPROVED_RUNNER.replace("chatbot-runner","chatbot-api"),
    APPROVED_RUNNER.replace("docker.io","untrusted.invalid"),
])
def test_unapproved_chatbot_ref_never_reads_pod_or_reaches_supply(tmp_path, reference):
    result, calls, args, gets, _ = run_case(tmp_path, reference)
    assert result.returncode != 0 and calls == args == [] and gets == "0"


@pytest.mark.parametrize("damage", ["uid","image","owner","state","label","phase","containers","extra-input","input-mode","source-symlink"])
def test_existing_pre_supply_binding_and_source_guards_still_block_pipeline(tmp_path, damage):
    result, calls, _, _, _ = run_case(tmp_path, damage=damage)
    assert result.returncode != 0 and calls == []


@pytest.mark.parametrize("damage", ["after-uid","after-image","source-changed","producer-fail","consumer-fail"])
def test_partial_failure_or_post_supply_drift_is_not_success_or_retried(tmp_path, damage):
    result, calls, _, _, _ = run_case(tmp_path, damage=damage)
    assert result.returncode != 0 and sorted(calls) == ["docker","exec"]
