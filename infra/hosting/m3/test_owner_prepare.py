"""Existing-root preservation and staged owner-only runtime contracts."""
import copy
import uuid

import pytest

from owner_prepare import API, RUNNER, OLD_API, NAME, LABEL, CLAIMS, emit, patch, storage
from test_prepare import apply
from test_https_prepare import snapshot as old_snapshot, TLS
from https_prepare import desired


@pytest.fixture
def snapshot():
    old = old_snapshot.__wrapped__()
    result = desired(old, TLS)
    api = copy.deepcopy(old["api"])
    api["spec"] = result["api"]
    config = result["config"]
    api["metadata"]["resourceVersion"] = "actual-https-rv"
    pvcs, pvs = [], []
    for name in ("trial-native", "trial-control"):
        identity, volume_uid = CLAIMS[name]
        pvcs.append({"kind": "PersistentVolumeClaim", "metadata": {"namespace": "codex-trial", "name": name, "uid": identity},
            "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "codex-trial-retain", "volumeName": "pvc-" + identity}, "status": {"phase": "Bound"}})
        pvs.append({"kind": "PersistentVolume", "metadata": {"name": "pvc-" + identity, "uid": volume_uid}, "spec": {
            "persistentVolumeReclaimPolicy": "Retain", "claimRef": {"namespace": "codex-trial", "name": name, "uid": identity},
            "nodeAffinity": {"required": {"nodeSelectorTerms": [{"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": ["synthetic-node"]}]}]}}}})
    return {"api": api, "config": config, "pvcs": {"items": pvcs}, "pvs": {"items": pvs}}


@pytest.fixture
def inputs():
    return {"ownerId": str(uuid.uuid4()), "stateId": str(uuid.uuid4()),
            "providerHosts": ["auth.example.invalid", "model.example.invalid"], "procedureRef": "synthetic-reviewed-only"}


def test_upgrade_independent_of_owner_credentials_storage_preserves_https(snapshot):
    current = copy.deepcopy(snapshot["api"])
    result = apply(current, patch(snapshot, {}, current, "upgrade"))
    c = result["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == API
    c["image"] = OLD_API
    assert result["spec"] == current["spec"]
    upgraded = copy.deepcopy(current)
    upgraded["spec"] = emit(snapshot, {}, "upgrade-api")
    assert apply(upgraded, patch(snapshot, {}, upgraded, "rollback-upgrade"))["spec"] == current["spec"]


def test_enable_disable_roundtrip_omission_and_actual_uid_rv_guards(snapshot, inputs):
    current = copy.deepcopy(snapshot["api"])
    current["spec"] = emit(snapshot, {}, "upgrade-api")
    active = apply(current, patch(snapshot, inputs, current, "enable"))
    for e in active["spec"]["template"]["spec"]["containers"][0]["env"]:
        if e.get("value") == "": e.pop("value")
    operations = patch(snapshot, inputs, active, "disable")
    assert operations[2]["value"] == active["spec"]
    assert apply(active, operations)["spec"] == current["spec"]
    wrong = copy.deepcopy(active); wrong["metadata"]["uid"] = "replacement"
    with pytest.raises(ValueError): patch(snapshot, inputs, wrong, "disable")
    wrong = copy.deepcopy(active); wrong["metadata"]["resourceVersion"] = "later"
    with pytest.raises(AssertionError): apply(wrong, operations)


@pytest.mark.parametrize("action", ["upgrade", "enable", "disable", "rollback-upgrade"])
def test_no_hidden_spec_drift_allowed(snapshot, inputs, action):
    current = copy.deepcopy(snapshot["api"])
    if action in {"enable", "rollback-upgrade"}: current["spec"] = emit(snapshot, {}, "upgrade-api")
    if action == "disable": current["spec"] = emit(snapshot, inputs, "enable-api")
    current["spec"]["template"]["spec"]["volumes"][0]["secret"]["secretName"] = "unreviewed-secret"
    with pytest.raises(ValueError): patch(snapshot, inputs, current, action)


def test_owner_init_uses_existing_volumes_no_auth_or_native(snapshot, inputs):
    job = emit(snapshot, inputs, "init")
    spec = job["spec"]["template"]["spec"]
    assert job["spec"]["backoffLimit"] == 0 and job["spec"]["activeDeadlineSeconds"] == 90
    assert spec["nodeSelector"] == {"kubernetes.io/hostname": "synthetic-node"}
    assert spec["automountServiceAccountToken"] is False and spec["securityContext"]["runAsUser"] == 10001
    assert {v["name"] for v in spec["volumes"]} == {"native", "tmp"}
    assert "initialize_binding" in spec["containers"][0]["command"][2]
    assert spec["containers"][0]["image"] == API
    deny = emit(snapshot, inputs, "init-policy")["spec"]
    assert "ingressDeny" in deny and "egressDeny" in deny


def test_runner_is_single_bound_private_pod_with_downward_uid(snapshot, inputs):
    runner = emit(snapshot, inputs, "runner")
    assert runner["spec"]["replicas"] == 1 and runner["spec"]["strategy"] == {"type": "Recreate"}
    spec = runner["spec"]["template"]["spec"]
    c = spec["containers"][0]
    env = {e["name"]: e for e in c["env"]}
    assert c["image"] == RUNNER and env["CODEX_MODEL"]["value"] == "gpt-6.1-sol"
    assert env["RUNNER_POD_UID"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.uid"
    assert env["RUNNER_STATE_ID"]["value"] == inputs["stateId"]
    assert env["RUNNER_ACCOUNT_ID"]["value"] == inputs["ownerId"]
    assert "/api/internal/research/tools" in env["RESEARCH_TOOL_CALLBACK_URL"]["value"]
    assert spec["terminationGracePeriodSeconds"] == 55 and not spec["automountServiceAccountToken"]
    assert "readinessProbe" in c and "livenessProbe" not in c
    assert emit(snapshot, inputs, "service")["spec"]["type"] == "ClusterIP"
    mapped = emit(snapshot, inputs, "api-input")[inputs["ownerId"]]
    assert mapped["stateId"] == inputs["stateId"] and NAME in mapped["url"]


def test_policy_cross_namespace_only_callback_and_exact_provider_dns(snapshot, inputs):
    values = emit(snapshot, inputs, "policy")["items"]
    runner, api = values[0]["spec"], values[1]["spec"]
    assert runner["endpointSelector"]["matchLabels"]["app"] == LABEL
    assert runner["ingress"][0]["fromEndpoints"][0]["matchLabels"]["k8s:io.kubernetes.pod.namespace"] == "m2-hosting"
    assert [v["matchName"] for v in runner["egress"][-1]["toFQDNs"]] == inputs["providerHosts"]
    assert api["ingress"][0]["fromEndpoints"][0]["matchLabels"]["k8s:io.kubernetes.pod.namespace"] == "codex-trial"


@pytest.mark.parametrize("damage", ["owner", "state", "nil", "wildcard", "ip", "procedure", "claim", "node", "bound"])
def test_incomplete_or_unsafe_runtime_inputs_refuse(snapshot, inputs, damage):
    if damage in {"owner", "state"}: inputs[damage + "Id"] = "not-a-uuid"
    elif damage == "nil": inputs["ownerId"] = "00000000-0000-0000-0000-000000000000"
    elif damage == "wildcard": inputs["providerHosts"] = ["*.example.invalid"]
    elif damage == "ip": inputs["providerHosts"] = ["127.0.0.1"]
    elif damage == "procedure": inputs.pop("procedureRef")
    elif damage == "claim": snapshot["pvs"]["items"][0]["spec"]["claimRef"]["uid"] = "replacement"
    elif damage == "node": snapshot["pvs"]["items"][0]["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] = ["other-node"]
    else: snapshot["pvcs"]["items"][0]["status"]["phase"] = "Pending"
    with pytest.raises(ValueError): emit(snapshot, inputs, "runner")


def test_runner_stop_start_guarded_without_rollout_surge(snapshot, inputs):
    current = emit(snapshot, inputs, 'runner')
    current['metadata'].update(uid='owned-runner-uid', resourceVersion='current-runner-rv')
    current['spec']['template']['spec']['containers'][0]['terminationMessagePath'] = '/dev/termination-log'
    snapshot['runner'] = copy.deepcopy(current)
    snapshot['runnerDryRun'] = copy.deepcopy(current)
    stopped = apply(current, patch(snapshot, inputs, current, 'runner-stop'))
    assert stopped['spec']['replicas'] == 0
    restarted = apply(stopped, patch(snapshot, inputs, stopped, 'runner-start'))
    assert restarted == current
    operations = patch(snapshot, inputs, current, 'runner-stop')
    wrong = copy.deepcopy(current); wrong['metadata']['uid'] = 'replacement'
    with pytest.raises(AssertionError): apply(wrong, operations)


@pytest.mark.parametrize('action', ['runner-stop', 'runner-start'])
def test_runner_lifecycle_rejects_other_image_or_volume(snapshot, inputs, action):
    current = emit(snapshot, inputs, 'runner')
    current['metadata'].update(uid='owned', resourceVersion='rv')
    snapshot['runner'] = copy.deepcopy(current)
    snapshot['runnerDryRun'] = copy.deepcopy(current)
    if action == 'runner-start': current['spec']['replicas'] = 0
    current['spec']['template']['spec']['containers'][0]['image'] = OLD_API
    with pytest.raises(ValueError): patch(snapshot, inputs, current, action)


def test_runner_defaulted_baseline_cannot_change_emitted_binding(snapshot, inputs):
    current = emit(snapshot, inputs, 'runner')
    current['metadata'].update(uid='owned', resourceVersion='rv')
    current['spec']['template']['spec']['containers'][0]['image'] = OLD_API
    snapshot['runner'] = copy.deepcopy(current)
    snapshot['runnerDryRun'] = copy.deepcopy(current)
    with pytest.raises(ValueError): patch(snapshot, inputs, current, 'runner-stop')
