"""Synthetic operation transitions; no cluster writes, auth or provider."""
import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import common_chatbot_prepare as prepare


@pytest.fixture
def bundle(monkeypatch):
    objects = {}
    for role, (namespace, name, uid, _) in prepare.TARGETS.items():
        c = {"name": "runner" if role == "runner" else role, "image": "old-" + role,
             "env": [{"name": "CODEX_PERSONAL_ENABLE", "value": "true"}] if role == "api" else [],
             "volumeMounts": [{"name": "private", "mountPath": "/unchanged", "readOnly": True}]}
        if role == "runner":
            c["env"] = [{"name": key, "value": value} for key, value in {"RUNNER_ACCOUNT_ID": "synthetic-owner", "RUNNER_STATE_ID": "synthetic-state", "RUNNER_STATE_DIR": "/native/access-web", "CODEX_ACCESS_CONTROL_DIR": "/control/access-web"}.items()]
        spec = {"replicas": 1, "template": {"spec": {"containers": [c], "volumes": [{"name": "private", "secret": {"secretName": "unchanged"}}]}},
                "strategy": {"type": "Recreate"} if role == "runner" else {"type": "RollingUpdate"}}
        objects[role] = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"namespace": namespace, "name": name, "uid": uid, "resourceVersion": "17"}, "spec": spec}
    # Stand-in hashes only for these synthetic fixtures; production uses the
    # measured compiled baseline digests, separately exercised by seal CLI.
    monkeypatch.setattr(prepare, "TARGETS", {role: (*values[:3], prepare.digest(objects[role]["spec"])) for role, values in prepare.TARGETS.items()})
    monkeypatch.setattr(prepare, "CONFIG_DIGEST", prepare.digest([]))
    baseline = {"objects": objects, "configs": [], "storage": {"node": "anxious-lookout", "claims": {"native": "trial-native", "control": "trial-control"}}}
    source = "a" * 40
    release = {"sourceSha": source, **{role: "docker.io/library/anxious-s13-chatbot-" + role + "-aaaaaaa@sha256:" + str(index) * 64 for index, role in enumerate(sorted(prepare.ROLES), 1)}}
    return baseline, release, source


def evidence(current):
    return {"currentDigest": prepare.identity_digest(current), "checkedAt": datetime.now(timezone.utc).isoformat(),
            "protectedStateVerified": True, "protectedState": {name: "d" * 64 for name in prepare.PROTECTED_FILES}, "lifetimeReservations": 1,
            "apiHoldObserved": True, "referenceMetadataVerified": True, "schemaReady": True, "noAmbiguousTurns": True, "apiRunning": 0, "nativeRunning": 0,
            "dbBackupSha256": "b" * 64, "nativeControlBackupSha256": "c" * 64, "dbRestoreVerified": True, "nativeControlArchiveVerified": True,
            "runnerPodCount": 0, "backupPodCount": 0, "apiReady": True, "webReady": True, "runnerReady": True,
            "podImagesVerified": True, "ownerStateVerified": True, "publicReadVerified": True,
            "dbNativeLinksVerified": True, "authCheckVerified": True, "runnerPodDigest": "e"*64,
            "nativeSessions": 1, "latestRecordedPairsVerified": 1, "latestRequestsWithoutPlatformMetadata": 0,
            "latestRequestsWithoutRecordedTurn": 0, "historicalRecordedPairsPreserved": 0,
            "historicalPairVerification": prepare.HISTORICAL_PAIR_SCOPE,
            "authReason": "not_configured", "authAvailable": False}


def step(bundle, current, action):
    baseline, release, source = bundle
    packet = prepare.patch(baseline, release, source, current, action, evidence(current))
    role = next(role for role, obj in current.items() if obj["metadata"]["uid"] == packet["target"]["uid"])
    edits = packet["patch"]
    assert edits[:3] == [{"op": "test", "path": "/metadata/uid", "value": current[role]["metadata"]["uid"]},
                         {"op": "test", "path": "/metadata/resourceVersion", "value": current[role]["metadata"]["resourceVersion"]},
                         {"op": "test", "path": "/spec", "value": current[role]["spec"]}]
    current[role]["spec"] = copy.deepcopy(edits[-1]["value"])
    current[role]["metadata"]["resourceVersion"] = str(int(current[role]["metadata"]["resourceVersion"]) + 1)
    return packet


def test_forward_and_rollback_restore_exact_baseline_without_settings_changes(bundle):
    baseline, release, _ = bundle
    original = copy.deepcopy(baseline)
    current = copy.deepcopy(baseline["objects"])
    for action in ("hold-api", "stop-runner", "upgrade-runner", "upgrade-api", "upgrade-web", "start-runner", "resume-api"):
        step(bundle, current, action)
    for role in prepare.ROLES:
        spec = copy.deepcopy(current[role]["spec"])
        assert prepare.container(spec)["image"] == release[role]
        prepare.container(spec)["image"] = prepare.container(baseline["objects"][role]["spec"])["image"]
        assert spec == baseline["objects"][role]["spec"]
    for action in ("hold-api", "stop-runner", "rollback-web", "rollback-api", "rollback-runner", "start-runner", "resume-old-api"):
        step(bundle, current, action)
    assert {role: obj["spec"] for role, obj in current.items()} == {role: obj["spec"] for role, obj in baseline["objects"].items()}
    assert baseline == original


@pytest.mark.parametrize("damage", ["uid", "namespace", "name", "env", "image", "replicas", "volume", "strategy"])
def test_unrelated_drift_is_refused_before_any_patch_including_recovery(bundle, damage):
    baseline, release, source = bundle
    current = copy.deepcopy(baseline["objects"])
    step(bundle, current, "hold-api")
    if damage in {"uid", "namespace", "name"}: current["runner"]["metadata"][damage] = "foreign"
    elif damage == "env": prepare.container(current["runner"]["spec"])["env"].append({"name": "CODEX_MODEL", "value": "another-model"})
    elif damage == "image": prepare.container(current["web"]["spec"])["image"] = "fixture"
    elif damage == "replicas": current["runner"]["spec"]["replicas"] = False
    elif damage == "volume": current["runner"]["spec"]["template"]["spec"]["volumes"] = []
    else: current["runner"]["spec"]["strategy"] = {"type": "RollingUpdate"}
    with pytest.raises(ValueError): prepare.patch(baseline, release, source, current, "stop-runner", evidence(current))


@pytest.mark.parametrize("change", [{"apiRunning": 1}, {"nativeRunning": 1}, {"nativeRunning": False}, {"noAmbiguousTurns": False},
    {"apiHoldObserved": False}, {"protectedStateVerified": False}, {"protectedState": {}}, {"lifetimeReservations": False},
    {"currentDigest": "different"}, {"checkedAt": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()},
    {"dbBackupSha256": ""}, {"dbRestoreVerified": False}, {"nativeControlArchiveVerified": False}, {"backupPodCount": 1}, {"runnerPodCount": False}])
def test_unverified_stale_or_busy_stage_refuses_upgrade(bundle, change):
    baseline, release, source = bundle
    current = copy.deepcopy(baseline["objects"])
    step(bundle, current, "hold-api"); step(bundle, current, "stop-runner")
    proof = {**evidence(current), **change}
    with pytest.raises(ValueError): prepare.patch(baseline, release, source, current, "upgrade-api", proof)


def test_admission_hold_and_all_three_versions_are_required(bundle):
    baseline, release, source = bundle
    current = copy.deepcopy(baseline["objects"])
    with pytest.raises(ValueError): prepare.patch(baseline, release, source, current, "stop-runner", evidence(current))
    step(bundle, current, "hold-api"); step(bundle, current, "stop-runner"); step(bundle, current, "upgrade-api")
    with pytest.raises(ValueError): prepare.patch(baseline, release, source, current, "resume-api", evidence(current))
    with pytest.raises(ValueError): prepare.patch(baseline, release, "f" * 40, current, "upgrade-web", evidence(current))


def test_old_baseline_config_node_or_spec_cannot_be_adopted(bundle):
    baseline, _, _ = bundle
    for key in ("configs", "node", "spec"):
        changed = copy.deepcopy(baseline)
        if key == "configs": changed["configs"] = [{"data": {"CODEX_PERSONAL_ACCOUNT_ID": "other"}}]
        elif key == "node": changed["storage"]["node"] = "foreign-node"
        else: changed["objects"]["api"]["spec"]["replicas"] = 0
        with pytest.raises(ValueError): prepare.validate_baseline(changed)


def test_backup_reader_is_same_node_readonly_no_secret_vendor_or_network(bundle):
    manifest = prepare.backup_reader(bundle[0])
    policy, pod = manifest["items"]
    assert policy["spec"]["egressDeny"] == [{"toEntities": ["all"]}]
    assert policy["spec"]["ingressDeny"] == [{"fromEntities": ["all"]}]
    spec = pod["spec"]
    assert spec["automountServiceAccountToken"] is False and "fsGroup" not in spec["securityContext"]
    assert spec["nodeSelector"] == {"kubernetes.io/hostname": "anxious-lookout"}
    assert all(volume["persistentVolumeClaim"]["readOnly"] is True for volume in spec["volumes"])
    assert all(mount["readOnly"] is True for mount in spec["containers"][0]["volumeMounts"])
    assert {entry["name"] for entry in spec["containers"][0]["env"]} == {"RUNNER_ACCOUNT_ID", "RUNNER_STATE_ID", "RUNNER_STATE_DIR", "CODEX_ACCESS_CONTROL_DIR"}
    assert "sleep" in spec["containers"][0]["command"][-1]


def test_receipts_bind_fresh_read_checks_to_current_metadata_and_refuse_busy_or_extra_inputs(bundle):
    current = copy.deepcopy(bundle[0]["objects"])
    step(bundle, current, "hold-api")
    now = datetime.now(timezone.utc).isoformat()
    api = {"checkedAt": now, "admissionHeld": True, "schemaReady": True, "apiRunning": 0, "ambiguous": 0}
    preservation = {"protectedState": {name: "d" * 64 for name in prepare.PROTECTED_FILES}, "lifetimeReservations": 1}
    runner = {"checkedAt": now, "ownerStateVerified": True, "nativeRunning": 0, "ambiguous": 0, **preservation}
    observed = {"checkedAt": now, "apiHoldObserved": True, "referenceMetadataVerified": True, "runnerPodCount": 0, "backupPodCount": 0}
    result = prepare.receipt(current, api, runner, observed, preservation=preservation)
    assert result["currentDigest"] == prepare.identity_digest(current) and result["noAmbiguousTurns"] is True
    with pytest.raises(ValueError): prepare.receipt(current, {**api, "apiRunning": 1}, runner, observed, preservation=preservation)
    with pytest.raises(ValueError): prepare.receipt(current, api, {**runner, "ambiguous": 1}, observed, preservation=preservation)
    with pytest.raises(ValueError): prepare.receipt(current, api, runner, {**observed, "credential": "must-not-copy"}, preservation=preservation)
    with pytest.raises(ValueError): prepare.receipt(current, api, {**runner, "protectedState": {name: "e" * 64 for name in prepare.PROTECTED_FILES}}, observed, preservation=preservation)
    with pytest.raises(ValueError): prepare.receipt(current, api, runner, observed)
    for selected in range(3):
        for offset in (timedelta(days=365), -timedelta(minutes=5)):
            inputs = [copy.deepcopy(value) for value in (api, runner, observed)]
            inputs[selected]["checkedAt"] = (datetime.now(timezone.utc) + offset).isoformat()
            with pytest.raises(ValueError): prepare.receipt(current, *inputs, preservation=preservation)
    backups = {**{key: evidence(current)[key] for key in ("dbBackupSha256", "nativeControlBackupSha256", "dbRestoreVerified", "nativeControlArchiveVerified")}, **preservation}
    assert prepare.receipt(current, api, runner, observed, backups, preservation)["protectedStateVerified"] is True
    with pytest.raises(ValueError): prepare.receipt(current, api, runner, observed, {**backups, "lifetimeReservations": 2}, preservation)


@pytest.mark.parametrize("kind", ["Pod", "CiliumNetworkPolicy"])
def test_cleanup_uses_created_uid_and_fresh_rv_and_refuses_replacement_or_drift(bundle, kind):
    baseline = bundle[0]
    created = next(value for value in prepare.backup_reader(baseline)["items"] if value["kind"] == kind)
    created["metadata"].update(uid=str(uuid.uuid4()), resourceVersion="21")
    current = copy.deepcopy(created)
    current["metadata"]["resourceVersion"] = "25"
    if kind == "Pod": current["spec"]["nodeName"] = "anxious-lookout"
    packet = prepare.cleanup(baseline, created, current)
    assert packet["body"]["preconditions"] == {"uid": created["metadata"]["uid"], "resourceVersion": "25"}
    changed = copy.deepcopy(current); changed["metadata"]["uid"] = str(uuid.uuid4())
    with pytest.raises(ValueError): prepare.cleanup(baseline, created, changed)
    changed = copy.deepcopy(current); changed["spec"]["foreign"] = True
    with pytest.raises(ValueError): prepare.cleanup(baseline, created, changed)
