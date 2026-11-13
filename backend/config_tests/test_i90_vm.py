"""#90 dev-VM stage scripts (infra/hosting/m3/chat-stream/vm/). Pure: no VM, no cluster, no Docker build.
Needs infra/ mounted next to backend/ (skipped otherwise, as test_journal_lengths.py)."""
import difflib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VM = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm"
PA = ROOT / "infra" / "hosting" / "m3" / "native-shell" / "vm"
pytestmark = pytest.mark.skipif(not VM.exists(), reason="infra/ not mounted")


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), VM / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_look_prints_only_the_named_env_values():
    look = load("i90-look")
    deployment = {"metadata": {"uid": "u-1"}, "spec": {"replicas": 1, "template": {"spec": {
        "containers": [{"name": "api", "image": "img@sha256:" + "a" * 64, "env": [
            {"name": "DATABASE_URL", "value": "postgresql://secret@db/x"},
            {"name": "RESEARCH_STREAM_ENABLE", "value": "true"},
            {"name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}},
            {"name": "CODEX_USER_RUNNER_IDLE_SECONDS", "value": "1800"}]}],
        "initContainers": [{"name": "runner-pool-input", "image": "init@sha256:" + "b" * 64}]}}},
        "status": {"readyReplicas": 1, "replicas": 1}}
    text = "\n".join(look.deployment_lines("m2-hosting", "api", deployment))
    assert "secret@db" not in text and "postgresql" not in text
    assert '"RESEARCH_STREAM_ENABLE": "true"' in text and '"CODEX_USER_RUNNER_IDLE_SECONDS": "1800"' in text
    assert '"DATABASE_URL"' in text  # names only
    assert "initContainer runner-pool-input image init@sha256:" in text


def test_look_owner_line_digest_is_the_tool_plan_digest():
    look = load("i90-look")
    plan = {"image": "r@sha256:" + "c" * 64, "version": 1, "native": {"name": "n"}}
    line = look.owner_line({"owner": "o", "bindingOwner": "o", "state": "s", "phase": "idle_stopped", "generation": 3,
                            "revision": 9, "pod": None, "active": 0, "ambiguous": False, "bindingState": "s",
                            "plan": plan, "outbox": 0, "entries": 0, "stops": 0, "admissions": 0, "proof": 1})
    import hashlib
    want = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert "oldPlanSha256 " + want in line and "planImage r@sha256:" in line


def test_look_sql_is_select_only_and_wrapped_read_only(monkeypatch):
    look = load("i90-look")
    for query in (look.OWNERS_SQL, look.TOTALS_SQL, look.CLIENTS_SQL):
        assert query.lstrip().upper().startswith("SELECT ") and query.count(";") == 1
        for word in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE", "GRANT", "TRUNCATE", "COPY"):
            assert word not in query.upper().replace("UPDATED", "")
    seen = []
    monkeypatch.setattr(look, "must", lambda cmd, message, timeout=60, stdin=None: seen.append(stdin) or '{"a":1}')
    assert look.rows("t", look.TOTALS_SQL) == [{"a": 1}]
    assert seen[0].decode().startswith("BEGIN READ ONLY;\n/*i90:t*/ SELECT ")


def test_host_build_differs_from_pa_only_in_listed_places():
    """Every changed line of i90-host-build.sh against the reviewed pa-host-build.sh is one of the listed changes."""
    old = (PA / "pa-host-build.sh").read_text().splitlines()
    new = (VM / "i90-host-build.sh").read_text().splitlines()
    removed = [line[1:] for line in difflib.unified_diff(old, new, lineterm="", n=0)
               if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in difflib.unified_diff(old, new, lineterm="", n=0)
             if line.startswith("+") and not line.startswith("+++")]
    # Removed: the header comment (lines 2-22), the usage name, the tag prefix, the start line, the single-archive
    # save/listing lines and the final archive line.
    for line in removed:
        assert (line.startswith("#") or "pa-host-build.sh" in line or line == 'tag="68-$short"'
                or line.startswith('echo "pa host build:') or "anxious-dev-runner-$tag.tar" in line
                or line.startswith("echo \"docker.io/library/$ref|")), line
    for line in added:
        assert (line.startswith("#") or "i90-host-build.sh" in line or line == 'tag="90-$short"'
                or "rt_" in line or "runtime" in line or "release" in line or line.startswith('echo "i90 host build:')
                or "anxious-dev-runner-$tag.tar" in line or line.strip() in {"fi", "b=$?", "echo 'result rc=0'"}
                or line.startswith("if [ \"$b\"") or line.startswith("  if [ \"$b\" = 124 ]")
                or line.startswith("  echo 'result rc=5'") or line.startswith("while read -r h f;")
                or line.startswith("( cd \"$out\" && sha256sum") or line.startswith("printf '%s\\n%s\\n'")
                # the runtime build's first line is the runner build's first line, verbatim
                or line == [l for l in old if l.startswith('timeout "$BUILD_TIMEOUT" docker build')][0]
                or line.strip() == ""), line
    # The runner gate, classification, content checks and binary pins are byte-identical.
    for marker in ("gate_check() {", "classify() {", "CODEX_PIN=", "HOST_PIN=", "grep -qx 'git absent'",
                   "--target runner --build-arg"):
        assert [l for l in old if marker in l] == [l for l in new if marker in l], marker


def test_host_build_syntax_and_usage():
    script = VM / "i90-host-build.sh"
    assert subprocess.run(["sh", "-n", str(script)]).returncode == 0
    result = subprocess.run(["sh", str(script)], capture_output=True, text=True)
    assert result.returncode == 2 and "usage: i90-host-build.sh" in result.stdout
