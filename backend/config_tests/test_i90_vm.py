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


# --- stage B: i90-import.sh with a fake k3s ------------------------------------------------------------------------

FAKE_K3S = r'''#!/usr/bin/env python3
import json, os, sys
state_file = os.environ["FAKE_STATE"]
state = json.load(open(state_file))
args = sys.argv[1:]
def save():
    json.dump(state, open(state_file, "w"))
if args[:4] == ["ctr", "-n", "k8s.io", "images"]:
    rest = args[4:]
    if rest == ["ls", "-q"]:
        print("\n".join(state["refs"]))
    elif rest == ["ls"]:
        print("REF TYPE DIGEST SIZE")
        for ref, (digest, _) in state["refs"].items():
            print(ref, "application/vnd.oci.image.index.v1+json", digest, "1MiB")
    elif rest[0] == "import":
        name = os.path.basename(rest[1])
        ref, digest, ident = state["archives"][name]
        if state.get("fail_import") == name:
            sys.exit(1)
        state["refs"][ref] = [digest, ident]
        save()
    elif rest[0] == "tag":
        state["refs"][rest[2]] = state["refs"][rest[1]]
        save()
    sys.exit(0)
if args[:2] == ["crictl", "inspecti"]:
    ref = args[-1]
    if ref not in state["refs"]:
        sys.exit(1)
    print(state.get("cri_override", {}).get(ref, state["refs"][ref][1]))
    sys.exit(0)
sys.exit(2)
'''

RUNNER_ID, RUNTIME_ID = "sha256:" + "1" * 64, "sha256:" + "2" * 64
RUNNER_DIGEST, RUNTIME_DIGEST = "sha256:" + "a" * 64, "sha256:" + "b" * 64
RUNNER_REF = "docker.io/library/anxious-dev-runner:90-159e271"
RUNTIME_REF = "docker.io/library/anxious-dev-runtime:90-159e271"


def import_setup(tmp_path, archive_bytes=(b"runner", b"runtime")):
    import hashlib
    images, evidence = tmp_path / "images", tmp_path / "evidence"
    images.mkdir()
    evidence.mkdir(mode=0o700)
    (images / "anxious-dev-runner-90-159e271.tar").write_bytes(archive_bytes[0])
    (images / "anxious-dev-runtime-90-159e271.tar").write_bytes(archive_bytes[1])
    fake = tmp_path / "k3s"
    fake.write_text(FAKE_K3S)
    fake.chmod(0o755)
    state = {"refs": {}, "archives": {
        "anxious-dev-runner-90-159e271.tar": [RUNNER_REF, RUNNER_DIGEST, RUNNER_ID],
        "anxious-dev-runtime-90-159e271.tar": [RUNTIME_REF, RUNTIME_DIGEST, RUNTIME_ID]}}
    (tmp_path / "state.json").write_text(json.dumps(state))
    script = (VM / "i90-import.sh").read_text()
    pins = {"SOURCE": "159e271c33853e93740c213db331103dd9359622", "RUNNER_TAG_REF": RUNNER_REF,
            "RUNNER_FILE": "anxious-dev-runner-90-159e271.tar",
            "RUNNER_ARCHIVE_SHA": hashlib.sha256(b"runner").hexdigest(), "RUNNER_HOST_ID": RUNNER_ID,
            "RUNTIME_TAG_REF": RUNTIME_REF, "RUNTIME_FILE": "anxious-dev-runtime-90-159e271.tar",
            "RUNTIME_ARCHIVE_SHA": hashlib.sha256(b"runtime").hexdigest(), "RUNTIME_HOST_ID": RUNTIME_ID}
    for key, value in pins.items():
        assert script.count("\n%s=PIN\n" % key) == 1
        script = script.replace("\n%s=PIN\n" % key, "\n%s=%s\n" % (key, value))
    script = (script.replace("K3S=/usr/local/bin/k3s", "K3S=" + str(fake))
              .replace("EVIDENCE=/root/issue90/evidence", "EVIDENCE=" + str(evidence))
              .replace("IMAGES=/home/opc/issue90-images", "IMAGES=" + str(images))
              .replace('[ "$(id -u)" = 0 ] || refuse', 'true || refuse')
              .replace('"$K3S"', 'python3 "$K3S"'))  # the test tmpfs is noexec: run the stub through python3
    return script, tmp_path / "state.json", evidence


def run_import(script, state):
    env = {**__import__("os").environ, "FAKE_STATE": str(state)}
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    return result.returncode, result.stdout


def test_import_refuses_while_a_pin_is_the_placeholder(tmp_path):
    result = subprocess.run(["sh", "-c", (VM / "i90-import.sh").read_text()], capture_output=True, text=True)
    assert result.returncode == 3 and "a pin is still the placeholder" in result.stdout


def test_import_both_chains_and_digest_references(tmp_path):
    script, state, evidence = import_setup(tmp_path)
    code, out = run_import(script, state)
    assert code == 0, out
    assert "i90 runner digest reference: docker.io/library/anxious-dev-runner@" + RUNNER_DIGEST in out
    assert "i90 runtime digest reference: docker.io/library/anxious-dev-runtime@" + RUNTIME_DIGEST in out
    refs = json.loads(state.read_text())["refs"]
    assert set(refs) == {RUNNER_REF, RUNTIME_REF, "docker.io/library/anxious-dev-runner@" + RUNNER_DIGEST,
                         "docker.io/library/anxious-dev-runtime@" + RUNTIME_DIGEST}
    files = list(evidence.iterdir())
    assert len(files) == 1 and files[0].read_text().splitlines()[-1] == "result rc=0"
    code, out = run_import(script, state)  # a second run: already present with the host IDs, no new import
    assert code == 0 and out.count("skip import of") == 2


def test_import_refuses_a_wrong_archive_before_anything(tmp_path):
    script, state, _ = import_setup(tmp_path, archive_bytes=(b"runner", b"tampered"))
    code, out = run_import(script, state)
    assert code == 3 and "is not" in out
    assert json.loads(state.read_text())["refs"] == {}


def test_import_never_retags_an_existing_reference_with_another_id(tmp_path):
    script, state, _ = import_setup(tmp_path)
    value = json.loads(state.read_text())
    value["refs"][RUNTIME_REF] = [RUNTIME_DIGEST, "sha256:" + "9" * 64]
    state.write_text(json.dumps(value))
    code, out = run_import(script, state)
    assert code == 3 and "never retagged" in out
    assert RUNNER_REF not in json.loads(state.read_text())["refs"]  # checked before either import


def test_import_mismatch_after_import_is_rc8(tmp_path):
    script, state, _ = import_setup(tmp_path)
    value = json.loads(state.read_text())
    value["cri_override"] = {RUNNER_REF: "sha256:" + "8" * 64}
    state.write_text(json.dumps(value))
    code, out = run_import(script, state)
    assert code == 8 and "MISMATCH (runner)" in out


# --- stage C: i90-switch.py ----------------------------------------------------------------------------------------

def switch():
    return load("i90-switch")


def expected_doc(module, **changes):
    recorded_api = "docker.io/library/anxious-dev-runtime@sha256:" + "3" * 64
    doc = {"version": module.VERSION, "newImage": module.NEW_PREFIX + "a" * 64, "oldImageId": "sha256:" + "4" * 64,
           "oldPlanSha256": "5" * 64, "newPlanSha256": "6" * 64, "toolImage": "sha256:" + "7" * 64,
           "newApiImage": module.API_PREFIX + "b" * 64,
           "others": {o: {"state": "s", "generation": 1, "revision": 4, "planSha256": "8" * 64,
                          "image": "docker.io/library/anxious-dev-runner@sha256:" + "9" * 64} for o in module.OTHERS},
           "deployments": {
               "api": {"uid": module.API[2], "allow": module.OWNER,
                       "images": {"api": recorded_api,
                                  "runner-pool-input": "docker.io/library/anxious-dev-runtime@sha256:" + "c" * 64}},
               "runner-controller": {"uid": module.CTRL[2], "allow": module.OWNER,
                                     "images": {"controller": recorded_api}}}}
    doc.update(changes)
    return doc


def test_switch_pins_are_placeholders_and_block_every_mode_but_survey():
    module = switch()
    assert module.pins_missing() == ["API_ID", "LEDGERS ledger-isolation-42.json", "LEDGERS ledger-resources-6.json",
                                     "NEW_ID", "OLD_REF"]
    with pytest.raises(module.Stop) as stopped:
        module.load_expected("0" * 64)
    assert stopped.value.code == 3 and "pins are still placeholders" in stopped.value.message
    assert module.main(["i90-switch.py", "precheck", "0" * 64]) == 3
    assert module.main(["i90-switch.py", "run", "0" * 64]) == 3


def test_switch_expected_file_rules_for_the_api_image():
    module = switch()
    assert module.expected_problem(expected_doc(module)) is None
    doc = expected_doc(module)
    del doc["newApiImage"]
    assert module.expected_problem(doc) == "keys or version"
    bad = expected_doc(module, newApiImage="docker.io/library/anxious-dev-runner@sha256:" + "b" * 64)
    assert module.expected_problem(bad) == "newApiImage is not a digest reference of anxious-dev-runtime"
    same = expected_doc(module)
    same["newApiImage"] = same["deployments"]["api"]["images"]["api"]
    assert module.expected_problem(same) == "newApiImage is the recorded api image"


def test_switch_api_patch_moves_only_the_api_container_image():
    module = switch()
    doc = expected_doc(module)
    old, new = doc["deployments"]["api"]["images"]["api"], doc["newApiImage"]
    forward = module.api_patch(doc, "forward")
    assert forward == [{"op": "test", "path": "/metadata/uid", "value": module.API[2]},
                       {"op": "test", "path": "/spec/template/spec/containers/0/name", "value": "api"},
                       {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": old},
                       {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": new}]
    reverse = module.api_patch(doc, "reverse")
    assert reverse[2]["value"] == new and reverse[3]["value"] == old
    assert not any("initContainers" in op["path"] or "env" in op["path"] for op in forward + reverse)


def test_switch_wanted_templates():
    module = switch()
    doc = expected_doc(module)
    assert module.wanted(module.API, doc, "old") == doc["deployments"]["api"]
    new = module.wanted(module.API, doc, "new")
    assert new["images"] == {"api": doc["newApiImage"],
                             "runner-pool-input": doc["deployments"]["api"]["images"]["runner-pool-input"]}
    assert {k: v for k, v in new.items() if k != "images"} == {k: v for k, v in doc["deployments"]["api"].items()
                                                              if k != "images"}
    assert module.wanted(module.CTRL, doc, "new") == doc["deployments"]["runner-controller"]


@pytest.mark.parametrize("branch,api,want", [
    ("reverse", "new", ["binding", "api"]), ("reverse", "old", ["binding"]), ("not needed", "new", ["api"]),
    ("not needed", "old", []), ("freeze", "old", "freeze"), ("freeze", "new", "freeze"),
    ("reverse", "other", "freeze"), ("not needed", None, "freeze")])
def test_switch_undo_actions(branch, api, want):
    assert switch().undo_actions(branch, api) == want


def test_switch_unchanged_functions_are_pa_switch_byte_for_byte():
    """Every top-level function of pa-switch.py not in the listed set is byte-identical in i90-switch.py."""
    import ast

    def functions(path):
        text = path.read_text()
        return {node.name: ast.get_source_segment(text, node) for node in ast.parse(text).body
                if isinstance(node, ast.FunctionDef)}
    old, new = functions(PA / "pa-switch.py"), functions(VM / "i90-switch.py")
    changed = {"rows", "check_owners", "tag_digest", "images_ok", "survey", "expected_problem", "load_expected",
               "precheck", "prepare", "tool", "start", "undo", "main", "final"}
    added = {"pins_missing", "undo_actions", "wanted", "api_state", "api_patch", "api_image"}
    assert set(new) == set(old) | added
    for name in sorted(set(old) - changed):
        assert new[name] == old[name], name
