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


IMPORT_PINS = ("SOURCE", "RUNNER_TAG_REF", "RUNNER_FILE", "RUNNER_ARCHIVE_SHA", "RUNNER_HOST_ID",
               "RUNTIME_TAG_REF", "RUNTIME_FILE", "RUNTIME_ARCHIVE_SHA", "RUNTIME_HOST_ID")


def import_pins(script):
    """The committed pin values: each pin is exactly one `KEY=value` line."""
    found = {}
    for key in IMPORT_PINS:
        lines = [line for line in script.splitlines() if line.startswith(key + "=")]
        assert len(lines) == 1, key
        found[key] = lines[0][len(key) + 1:]
    return found


def with_pins(script, pins):
    """The script with each named pin line replaced (the committed value, whatever it is, is never matched)."""
    committed = import_pins(script)
    for key, value in pins.items():
        old = "\n%s=%s\n" % (key, committed[key])
        assert script.count(old) == 1, key
        script = script.replace(old, "\n%s=%s\n" % (key, value))
    return script


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
    script = with_pins(script, pins)
    script = (script.replace("K3S=/usr/local/bin/k3s", "K3S=" + str(fake))
              .replace("EVIDENCE=/root/issue90/evidence", "EVIDENCE=" + str(evidence))
              .replace("IMAGES=/home/opc/issue90-images", "IMAGES=" + str(images))
              .replace('[ "$(id -u)" = 0 ] || refuse', 'true || refuse')
              .replace('"$K3S"', 'python3 "$K3S"'))  # the test tmpfs is noexec: run the stub through python3
    return script, tmp_path / "state.json", evidence


def run_import(script, state, stamp="20261114T000000Z"):
    env = {**__import__("os").environ, "FAKE_STATE": str(state)}
    # Fixed evidence timestamps (review b8caf3e minor): the evidence name is per second.
    script = script.replace("stamp=$(date -u +%Y%m%dT%H%M%SZ)", "stamp=" + stamp)
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    return result.returncode, result.stdout


def test_import_refuses_while_a_pin_is_the_placeholder(tmp_path):
    """The guard stays: any one pin put back to PIN refuses before the evidence file or k3s is touched."""
    script, state, evidence = import_setup(tmp_path)
    before = state.read_text()
    for key in IMPORT_PINS:
        code, out = run_import(with_pins(script, {key: "PIN"}), state)
        assert code == 3 and "a pin is still the placeholder" in out, key
        assert state.read_text() == before and list(evidence.iterdir()) == [], key


def test_import_committed_pins_are_one_stage_a_build():
    """Stage B pins (filled from the stage A images.txt / archives.sha256): no placeholder left, the formats hold,
    and they agree with i90-switch.py's tags and host ids. The values themselves are checked by review against the
    build output, by hash."""
    pins = import_pins((VM / "i90-import.sh").read_text())
    assert "PIN" not in pins.values()
    hexdigits = set("0123456789abcdef")
    source = pins["SOURCE"]
    assert len(source) == 40 and set(source) <= hexdigits
    module = switch()
    for name, tag, ident in (("runner", module.NEW_TAG, module.NEW_ID), ("runtime", module.API_TAG, module.API_ID)):
        key = name.upper()
        assert pins[key + "_TAG_REF"] == tag == "docker.io/library/anxious-dev-%s:90-%s" % (name, source[:7])
        assert pins[key + "_FILE"] == "anxious-dev-%s-90-%s.tar" % (name, source[:7])
        sha = pins[key + "_ARCHIVE_SHA"]
        assert len(sha) == 64 and set(sha) <= hexdigits
        assert pins[key + "_HOST_ID"] == ident
        assert ident.startswith("sha256:") and len(ident) == 71 and set(ident[7:]) <= hexdigits
    assert pins["RUNNER_HOST_ID"] != pins["RUNTIME_HOST_ID"]
    assert pins["RUNNER_ARCHIVE_SHA"] != pins["RUNTIME_ARCHIVE_SHA"]


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
    code, out = run_import(script, state, "20261114T000001Z")  # a second run: present with the host IDs, no import
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
           "newApiImage": module.API_PREFIX + "b" * 64, "apiTemplateSha256": "d" * 64,
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


@pytest.mark.parametrize("name", ["OLD_REF", "NEW_ID", "API_ID", "LEDGERS"])
def test_switch_a_placeholder_pin_blocks_every_mode_but_survey(monkeypatch, name):
    module = switch()
    if name == "LEDGERS":
        first = sorted(module.LEDGERS)[0]
        monkeypatch.setattr(module, "LEDGERS", dict(module.LEDGERS, **{first: module.PIN}))
    else:
        monkeypatch.setattr(module, name, module.PIN)
    assert module.pins_missing()
    with pytest.raises(module.Stop) as stopped:
        module.load_expected("0" * 64)
    assert stopped.value.code == 3 and "pins are still placeholders" in stopped.value.message
    assert module.main(["i90-switch.py", "precheck", "0" * 64]) == 3
    assert module.main(["i90-switch.py", "run", "0" * 64]) == 3


def test_switch_pins_are_filled_from_the_survey():
    """Stage C pins: OLD_REF and LEDGERS from the survey output (NEW_ID / API_ID from stage A); none is left."""
    module = switch()
    assert module.pins_missing() == []
    prefix = module.NEW_PREFIX
    assert module.OLD_REF.startswith(prefix) and module.HEX64.fullmatch(module.OLD_REF[len(prefix):])
    assert sorted(module.LEDGERS) == ["/root/native68/evidence/ledger-isolation-42.json",
                                      "/root/native68/evidence/ledger-resources-6.json"]
    assert all(module.HEX64.fullmatch(v) for v in module.LEDGERS.values())


def test_switch_committed_expected_file_is_the_survey_candidate_shape():
    """vm/i90-expected.json (the survey's `expected candidate:` line plus a newline, copied, never typed): accepted by
    expected_problem with the filled pins, canonical bytes, and consistent with the pins. Its values are checked by
    review against the survey text, by hash."""
    module = switch()
    data = (VM / "i90-expected.json").read_bytes()
    doc = json.loads(data)
    assert data == (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert module.expected_problem(doc) is None
    assert doc["version"] == module.VERSION
    assert doc["newImage"] != module.OLD_REF and doc["oldImageId"] != module.NEW_ID
    assert doc["deployments"]["api"]["images"]["api"] != doc["newApiImage"]
    assert doc["deployments"]["api"]["uid"] == module.API[2]
    assert doc["deployments"]["runner-controller"]["uid"] == module.CTRL[2]
    report = module.derive(doc)
    assert report["forward"]["report"]["oldImage"] == module.OLD_REF
    assert report["forward"]["report"]["newImage"] == doc["newImage"]
    assert report["reverse"]["report"]["newImage"] == module.OLD_REF


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
    forward = module.api_patch(doc, "forward", "10")
    assert forward == [{"op": "test", "path": "/metadata/uid", "value": module.API[2]},
                       {"op": "test", "path": "/metadata/resourceVersion", "value": "10"},
                       {"op": "test", "path": "/spec/replicas", "value": 0},
                       {"op": "test", "path": "/spec/template/spec/containers/0/name", "value": "api"},
                       {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": old},
                       {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": new}]
    reverse = module.api_patch(doc, "reverse", "11")
    assert reverse[4]["value"] == new and reverse[5]["value"] == old
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
               "precheck", "prepare", "tool", "start", "undo", "main", "final", "reopen_checks"}
    added = {"pins_missing", "undo_actions", "wanted", "api_state", "api_patch", "api_image", "api_template_sha",
             "api_state_of"}
    assert set(new) == set(old) | added
    for name in sorted(set(old) - changed):
        assert new[name] == old[name], name


def test_import_same_second_evidence_name_refuses(tmp_path):
    script, state, _ = import_setup(tmp_path)
    assert run_import(script, state, "20261114T000000Z")[0] == 0
    code, out = run_import(script, state, "20261114T000000Z")  # the evidence file exists: never overwritten
    assert code == 3 and "cannot create" in out


def api_deployment(module, doc, image=None, replicas=0, version="10"):
    return {"metadata": {"uid": module.API[2], "resourceVersion": version},
            "spec": {"replicas": replicas, "template": {"metadata": {"labels": {"app": "hosting-api"}}, "spec": {
                "containers": [{"name": "api", "image": image or doc["deployments"]["api"]["images"]["api"],
                                "env": [{"name": module.ALLOW_ENV, "value": module.OWNER},
                                        {"name": "DATABASE_URL", "value": "postgresql://secret@db/x"}]}],
                "initContainers": [{"name": "runner-pool-input",
                                    "image": doc["deployments"]["api"]["images"]["runner-pool-input"]}]}}},
            "status": {"replicas": replicas}}


def test_api_template_hash_fixes_everything_but_the_api_image():
    module = switch()
    doc = expected_doc(module)
    old = api_deployment(module, doc)
    new = api_deployment(module, doc, image=doc["newApiImage"])
    doc["apiTemplateSha256"] = module.api_template_sha(old)
    assert module.api_template_sha(new) == doc["apiTemplateSha256"]
    assert module.api_state_of(old, doc, 8) == "old" and module.api_state_of(new, doc, 8) == "new"
    for damage in ("flag", "init-command", "volume", "annotation", "env-value"):
        drifted = api_deployment(module, doc)
        pod = drifted["spec"]["template"]
        if damage == "flag":
            pod["spec"]["containers"][0]["env"].append({"name": "RESEARCH_STREAM_ENABLE", "value": "true"})
        elif damage == "init-command":
            pod["spec"]["initContainers"][0]["command"] = ["unexpected"]
        elif damage == "volume":
            pod["spec"]["volumes"] = [{"name": "unexpected", "emptyDir": {}}]
        elif damage == "annotation":
            pod["metadata"]["annotations"] = {"kubectl.kubernetes.io/restartedAt": "now"}
        else:
            pod["spec"]["containers"][0]["env"][1]["value"] = "postgresql://other@db/x"
        assert module.api_state_of(drifted, doc, 8) == "other", damage
    assert "secret" not in doc["apiTemplateSha256"]  # a hash, never the env value


def test_api_patch_tests_replicas_and_resource_version():
    module = switch()
    ops = module.api_patch(expected_doc(module), "forward", "10")
    assert {"op": "test", "path": "/spec/replicas", "value": 0} in ops
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": "10"} in ops
    assert [op["op"] for op in ops] == ["test"] * 5 + ["replace"]


def apply_json_patch(document, ops):
    """A minimal RFC 6902 test/replace applier (what the API server does for these ops)."""
    import copy
    candidate = copy.deepcopy(document)
    for op in ops:
        parts = op["path"].strip("/").split("/")
        node = candidate
        for name in parts[:-1]:
            node = node[int(name)] if isinstance(node, list) else node[name]
        key = int(parts[-1]) if isinstance(node, list) else parts[-1]
        if op["op"] == "test" and node[key] != op["value"]:
            return None
        if op["op"] == "replace":
            node[key] = op["value"]
    return candidate


@pytest.mark.parametrize("race", ["none", "scaled-up", "edited"])
def test_api_image_refuses_a_change_between_read_and_patch(monkeypatch, race):
    import copy
    module = switch()
    doc = expected_doc(module)
    live = api_deployment(module, doc)
    doc["apiTemplateSha256"] = module.api_template_sha(live)  # the pre-read passes: the race is after it
    monkeypatch.setattr(module, "deployment", lambda *a: (module.API[2], live["spec"]["replicas"], 0,
                                                         live["status"]["replicas"], copy.deepcopy(live)))
    monkeypatch.setattr(module, "change", lambda *a: None)
    patched = []

    def request(cmd, *args):
        if race == "scaled-up":
            live["spec"]["replicas"] = 1
            live["metadata"]["resourceVersion"] = "11"
        elif race == "edited":  # another writer, replicas still 0
            live["metadata"]["resourceVersion"] = "11"
        result = apply_json_patch(live, json.loads(cmd[-1]))
        if result is None:
            return 1, ""
        live.clear()
        live.update(result)
        patched.append(True)
        return 0, ""
    monkeypatch.setattr(module, "run", request)
    if race == "none":
        module.api_image(doc, "forward", 6)
        assert patched and live["spec"]["template"]["spec"]["containers"][0]["image"] == doc["newApiImage"]
    else:
        with pytest.raises(module.Stop):
            module.api_image(doc, "forward", 6)
        assert not patched


# --- stage C: i90-install.sh (fake /root/issue90, run as the test user) -------------------------------------------

def install_run(tmp_path, name, data, sha=None, prepare=None):
    import hashlib
    import os
    d = tmp_path / "issue90"
    if not d.exists():
        d.mkdir(mode=0o700)
        d.chmod(0o700)
    if prepare:
        prepare(d)
    uid, gid = os.getuid(), os.getgid()
    script = (VM / "i90-install.sh").read_text()
    for old, new in (("D=/root/issue90", "D=" + str(d)),
                     ('[ "$(id -u)" = 0 ] ||', 'true ||'),
                     ("= '0:0:700' ]", "= '%d:%d:700' ]" % (uid, gid)),
                     ('chown root:root "$T" && ', ''),
                     ("= 'regular file|0|0|600|1' ]", "= 'regular file|%d|%d|600|1' ]" % (uid, gid))):
        assert script.count(old) == 1, old
        script = script.replace(old, new)
    sha = sha or hashlib.sha256(data).hexdigest()
    result = subprocess.run(["sh", "-c", script, "sh", name, sha], input=data, capture_output=True, timeout=60)
    return result.returncode, result.stdout.decode(), d


def test_install_new_file_root_0600_and_verified(tmp_path):
    code, out, d = install_run(tmp_path, "i90-expected.json", b'{"a":1}\n')
    assert code == 0 and out.endswith("install rc=0\n"), out
    target = d / "i90-expected.json"
    assert target.read_bytes() == b'{"a":1}\n' and oct(target.stat().st_mode & 0o777) == "0o600"
    assert sorted(p.name for p in d.iterdir()) == ["i90-expected.json"]


def test_install_keeps_the_previous_file_as_rn(tmp_path):
    install_run(tmp_path, "i90-switch.py", b"survey version\n")
    code, out, d = install_run(tmp_path, "i90-switch.py", b"pinned version\n")
    assert code == 0 and "as i90-switch.py.r0" in out, out
    code, out, d = install_run(tmp_path, "i90-switch.py", b"third\n")
    assert code == 0 and "as i90-switch.py.r1" in out, out
    assert (d / "i90-switch.py.r0").read_bytes() == b"survey version\n"
    assert (d / "i90-switch.py.r1").read_bytes() == b"pinned version\n"
    assert (d / "i90-switch.py").read_bytes() == b"third\n"


def test_install_same_file_changes_nothing(tmp_path):
    install_run(tmp_path, "i90-expected.json", b"x\n")
    code, out, d = install_run(tmp_path, "i90-expected.json", b"x\n")
    assert code == 0 and "already the reviewed file" in out
    assert sorted(p.name for p in d.iterdir()) == ["i90-expected.json"]


def test_install_wrong_bytes_change_nothing(tmp_path):
    install_run(tmp_path, "i90-switch.py", b"old\n")
    code, out, d = install_run(tmp_path, "i90-switch.py", b"tampered\n", sha="0" * 64)
    assert code == 5 and "not the reviewed file" in out
    assert sorted(p.name for p in d.iterdir()) == ["i90-switch.py"] and (d / "i90-switch.py").read_bytes() == b"old\n"


@pytest.mark.parametrize("args", [("other.py", "0" * 64), ("i90-switch.py", "0" * 63), ("i90-switch.py", "A" * 64),
                                  ("i90-switch.py",)])
def test_install_usage(tmp_path, args):
    script = (VM / "i90-install.sh").read_text()
    result = subprocess.run(["sh", "-c", script, "sh", *args], input=b"", capture_output=True, timeout=60)
    assert result.returncode == 2 and b"nothing changed" in result.stdout


def test_install_refuses_a_symlink_or_directory_at_the_name(tmp_path):
    code, out, d = install_run(tmp_path, "i90-expected.json", b"x\n",
                               prepare=lambda d: (d / "i90-expected.json").symlink_to(d / "elsewhere"))
    assert code == 3 and "not a regular file" in out
    code, out, d = install_run(tmp_path, "i90-switch.py", b"x\n", prepare=lambda d: (d / "i90-switch.py").mkdir())
    assert code == 3 and "not a regular file" in out
    assert not [p for p in d.iterdir() if p.name.startswith(".i90-install-new.")]


def test_install_refuses_a_parent_that_is_not_0700(tmp_path):
    code, out, d = install_run(tmp_path, "i90-expected.json", b"x\n", prepare=lambda d: d.chmod(0o755))
    assert code == 3 and "is not root:root 700" in out and not (d / "i90-expected.json").exists()
