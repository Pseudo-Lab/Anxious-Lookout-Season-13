"""Synthetic web image patches preserve connected API/runner; no live operations."""
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import chatbot_web_only as web
import common_chatbot_prepare as common
from common_chatbot_image_audit import audit
from test_common_chatbot_prepare import bundle
from test_common_chatbot_image_audit import images


@pytest.fixture
def web_bundle(bundle, monkeypatch):
    baseline = copy.deepcopy(bundle[0])
    references = []
    for kind, namespace, name in sorted(web.REQUIRED_REFS):
        obj = {"apiVersion":"v1", "kind":kind, "metadata":{"name":name, "uid":str(uuid.uuid4()), "resourceVersion":"18"}}
        if namespace: obj["metadata"]["namespace"] = namespace
        if kind == "ConfigMap": obj.update(immutable=True, data={"MODE":"unchanged"})
        elif kind != "Secret": obj["spec"] = {"unchanged": True}
        if kind == "CiliumNetworkPolicy" and name in {"trial-boundary", "hosting-boundary"}:
            del obj["spec"]
            obj["specs"] = [{"endpointSelector":{"matchLabels":{"fixture":"rule-"+str(index)}}, "egress":[]}
                for index in range(6 if name == "trial-boundary" else 4)]
        if kind == "PersistentVolumeClaim":
            claim_uid, _ = web.CLAIMS[name]
            obj["metadata"]["uid"] = claim_uid
            obj.update(status={"phase":"Bound"}, spec={"accessModes":["ReadWriteOnce"], "storageClassName":"codex-trial-retain", "volumeName":"pvc-"+claim_uid})
        elif kind == "PersistentVolume":
            claim = next(claim for claim, ids in web.CLAIMS.items() if name == "pvc-"+ids[0])
            claim_uid, volume_uid = web.CLAIMS[claim]
            obj["metadata"]["uid"] = volume_uid
            obj["spec"] = {"persistentVolumeReclaimPolicy":"Retain", "claimRef":{"namespace":"codex-trial","name":claim,"uid":claim_uid},
                "nodeAffinity":{"required":{"nodeSelectorTerms":[{"matchExpressions":[{"key":"kubernetes.io/hostname","operator":"In","values":["anxious-lookout"]}]}]}}}
        references.append(obj)
    baseline["configs"] = [{"kind":obj["kind"], "metadata":{key:obj["metadata"][key] for key in ("name","namespace","uid")},
        "data":obj["data"], "immutable":obj["immutable"]} for obj in references if obj["kind"] == "ConfigMap"]
    baseline["configs"].sort(key=lambda obj: obj["metadata"]["name"])
    monkeypatch.setattr(common, "CONFIG_DIGEST", common.digest(baseline["configs"]))
    for role, obj in baseline["objects"].items():
        obj["spec"]["template"]["metadata"] = {"labels":{"app":"access-web-runner" if role == "runner" else role}}
    monkeypatch.setattr(common, "TARGETS", {role:(*values[:3], common.digest(baseline["objects"][role]["spec"])) for role,values in common.TARGETS.items()})
    current = copy.deepcopy(baseline["objects"])
    specs = common.release_specs(baseline, web.BASE_RELEASE, web.BASE_SOURCE)
    pods = {}
    for role, obj in current.items():
        obj["spec"] = copy.deepcopy(specs[role])
        obj["metadata"]["generation"] = 3
        obj["status"] = {"observedGeneration":3,"replicas":1,"readyReplicas":1,"updatedReplicas":1,"availableReplicas":1}
        if role != "web":
            owner = {"apiVersion":"apps/v1","kind":"ReplicaSet","name":obj["metadata"]["name"]+"-synthetic","uid":str(uuid.uuid4()),"controller":True}
            pods[role] = {"items":[{"metadata":{"namespace":obj["metadata"]["namespace"],"name":owner["name"]+"-pod","uid":str(uuid.uuid4()),
                "labels":obj["spec"]["template"]["metadata"]["labels"],"ownerReferences":[owner]},
                "spec":{"nodeName":"anxious-lookout","containers":[copy.deepcopy(common.container(specs[role]))]},
                "status":{"phase":"Running","containerStatuses":[{"name":common.container(specs[role])["name"],"ready":True,
                    "imageID":"containerd://sha256:"+"a"*64,"containerID":"containerd://"+"b"*64,"restartCount":0,
                    "state":{"running":{"startedAt":"2026-10-14T00:00:00Z"}}}]}}]}
    snapshot = {"checkedAt":datetime.now(timezone.utc).isoformat(),"objects":current,"pods":pods,"references":references}
    known_pods = copy.deepcopy(pods)
    preservation = web.seal(baseline, snapshot, known_pods)
    new_sha = "f"*40
    release = {"webSourceSha":new_sha, "web":"docker.io/library/anxious-s13-chatbot-web-fffffff@sha256:"+"c"*64}
    return baseline, snapshot, known_pods, preservation, release, new_sha


def test_only_web_image_changes_and_failed_web_can_rollback_without_native_restart(web_bundle):
    baseline, snapshot, known, preserved, release, source = web_bundle
    original = copy.deepcopy(snapshot)
    packet = web.patch(baseline, preserved, snapshot, release, source, "upgrade")
    assert packet["target"]["name"] == "web" and packet["target"]["namespace"] == "m2-hosting"
    assert [item["path"] for item in packet["patch"]] == ["/metadata/uid","/metadata/resourceVersion","/spec","/spec"]
    changed = packet["patch"][-1]["value"]
    reverted = copy.deepcopy(changed)
    common.container(reverted)["image"] = web.BASE_RELEASE["web"]
    assert reverted == original["objects"]["web"]["spec"]
    assert packet["sources"] == {"api":web.BASE_SOURCE,"runner":web.BASE_SOURCE,"web":source}
    snapshot["objects"]["web"]["spec"] = changed
    snapshot["objects"]["web"]["metadata"]["resourceVersion"] = "19"
    snapshot["objects"]["web"]["status"] = {"unavailableReplicas":1}
    rollback = web.patch(baseline, preserved, snapshot, release, source, "rollback")
    assert rollback["patch"][-1]["value"] == original["objects"]["web"]["spec"]
    assert rollback["sources"]["web"] == web.BASE_SOURCE
    for role in ("api","runner"):
        assert snapshot["objects"][role] == original["objects"][role]
        assert snapshot["pods"][role] == original["pods"][role]
    assert snapshot["references"] == original["references"] and known == original["pods"]


@pytest.mark.parametrize("damage", ["api-spec","runner-spec","web-spec","uid","rv","hold","replicas","pod-uid","restart",
    "container","started","pod-image","pod-not-ready","node","ref-uid","secret-rv","missing-ref","secret-data","config","pvc","policy","stale","future"])
def test_any_unrelated_drift_refuses_web_upgrade_and_rollback(web_bundle, damage):
    baseline, snapshot, known, preserved, release, source = web_bundle
    if damage in {"api-spec","runner-spec","web-spec"}:
        snapshot["objects"][damage.split("-")[0]]["spec"]["strategy"] = {"type":"RecreateUnexpected"}
    elif damage == "uid": snapshot["objects"]["web"]["metadata"]["uid"] = str(uuid.uuid4())
    elif damage == "rv": snapshot["objects"]["web"]["metadata"]["resourceVersion"] = ""
    elif damage == "hold": common.container(snapshot["objects"]["api"]["spec"])["env"][0]["value"] = "false"
    elif damage == "replicas": snapshot["objects"]["runner"]["spec"]["replicas"] = 0
    elif damage in {"stale","future"}:
        snapshot["checkedAt"] = (datetime.now(timezone.utc)+timedelta(minutes=5 if damage=="future" else -5)).isoformat()
    elif damage in {"ref-uid","secret-rv","missing-ref","secret-data","config","pvc","policy"}:
        kind = {"config":"ConfigMap","pvc":"PersistentVolumeClaim","policy":"CiliumNetworkPolicy"}.get(damage,"Secret")
        obj = next(obj for obj in snapshot["references"] if obj["kind"] == kind)
        if damage == "missing-ref": snapshot["references"].remove(obj)
        elif damage == "ref-uid": obj["metadata"]["uid"] = str(uuid.uuid4())
        elif damage == "secret-rv": obj["metadata"]["resourceVersion"] = "20"
        elif damage == "secret-data": obj["data"] = {"never":"accepted"}
        elif damage == "config": obj["data"]["MODE"] = "changed"
        else: obj["spec"]["unexpected"] = True
    else:
        pod = snapshot["pods"]["runner"]["items"][0]
        info = pod["status"]["containerStatuses"][0]
        if damage == "pod-uid": pod["metadata"]["uid"] = str(uuid.uuid4())
        elif damage == "restart": info["restartCount"] = 1
        elif damage == "container": info["containerID"] = "replacement"
        elif damage == "started": info["state"]["running"]["startedAt"] = "2026-10-14T01:00:00Z"
        elif damage == "pod-image": pod["spec"]["containers"][0]["image"] = "replacement"
        elif damage == "node": pod["spec"]["nodeName"] = "other"
        else: info["ready"] = False
    for action in ("upgrade","rollback"):
        with pytest.raises((ValueError, KeyError)): web.patch(baseline, preserved, snapshot, release, source, action)


def test_seal_requires_existing_reviewed_processes_not_an_arbitrary_current_replacement(web_bundle):
    baseline, snapshot, known, _, _, _ = web_bundle
    snapshot["pods"]["runner"]["items"][0]["metadata"]["uid"] = str(uuid.uuid4())
    with pytest.raises(ValueError): web.seal(baseline, snapshot, known)


@pytest.mark.parametrize("change", [{"webSourceSha":"a"*40},{"api":"unrequested"},{"web":"unreviewed:tag"},
    {"web":"dockerXio/library/anxious-s13-chatbot-web-fffffff@sha256:"+"c"*64}])
def test_unapproved_mixed_release_shape_source_or_reference_is_refused(web_bundle, change):
    baseline, snapshot, _, preserved, release, source = web_bundle
    with pytest.raises(ValueError): web.patch(baseline, preserved, snapshot, {**release,**change}, source, "upgrade")


def test_existing_archive_import_auditor_supports_a_distinct_web_source(images):
    source, build, imported, ref, pod = images
    for suffix in ("tar","metadata.json","inspect.json"): (build/("api."+suffix)).rename(build/("web."+suffix))
    ref = ref.replace("chatbot-api","chatbot-web")
    cri = json.loads((imported/"cri.json").read_text()); cri["status"]["repoDigests"] = [ref]
    (imported/"cri.json").write_text(json.dumps(cri)); pod["specImage"] = ref
    result = audit("web", source, build, imported, ref, pod)
    assert result["role"] == "web" and result["sourceSha"] == source and result["podAssociationChecked"] is True


def test_live_shaped_mix_of_five_specs_and_two_rule_arrays_seals_without_conversion(web_bundle):
    baseline, snapshot, known, preservation, release, source = web_bundle
    policies = [obj for obj in snapshot["references"] if obj["kind"] == "CiliumNetworkPolicy"]
    assert sum("spec" in obj for obj in policies) == 5
    assert sorted(len(obj["specs"]) for obj in policies if "specs" in obj) == [4,6]
    original = copy.deepcopy(snapshot)
    assert web.seal(baseline, snapshot, known) == preservation
    assert web.patch(baseline, preservation, snapshot, release, source, "upgrade")["target"]["name"] == "web"
    assert snapshot == original


@pytest.mark.parametrize("fields", [
    {}, {"spec":None}, {"spec":{}}, {"spec":[]}, {"spec":"invalid"},
    {"specs":None}, {"specs":[]}, {"specs":{}}, {"specs":"invalid"},
    {"specs":[None]}, {"specs":[{}]}, {"specs":[[]]}, {"specs":[1]},
    {"spec":{"valid":True},"specs":[]}, {"spec":None,"specs":[{"valid":True}]},
])
def test_missing_empty_or_malformed_policy_representations_fail_closed(web_bundle, fields):
    baseline, snapshot, known, _, _, _ = web_bundle
    policy = next(obj for obj in snapshot["references"] if obj["kind"] == "CiliumNetworkPolicy")
    policy.pop("spec",None); policy.pop("specs",None); policy.update(fields)
    with pytest.raises(ValueError): web.seal(baseline, snapshot, known)


@pytest.mark.parametrize("damage", ["spec-content","specs-content","reorder","add-rule","remove-rule",
    "representation","add-second-field","both-spec","both-specs","remove-second-field"])
def test_each_declared_policy_field_and_array_order_is_preserved_for_recovery(web_bundle, damage):
    baseline, snapshot, known, _, release, source = web_bundle
    one = next(obj for obj in snapshot["references"] if obj["kind"] == "CiliumNetworkPolicy" and "spec" in obj)
    many = next(obj for obj in snapshot["references"] if obj["kind"] == "CiliumNetworkPolicy" and "specs" in obj)
    if damage.startswith("both-") or damage == "remove-second-field":
        one["specs"] = [{"additionalRule":True}]
    preservation = web.seal(baseline, snapshot, known)
    packet = web.patch(baseline, preservation, snapshot, release, source, "upgrade")
    snapshot["objects"]["web"]["spec"] = copy.deepcopy(packet["patch"][-1]["value"])
    assert web.patch(baseline, preservation, snapshot, release, source, "rollback")["target"]["name"] == "web"
    if damage in {"spec-content","both-spec"}: one["spec"]["changed"] = True
    elif damage == "specs-content": many["specs"][0]["changed"] = True
    elif damage == "reorder": many["specs"].reverse()
    elif damage == "add-rule": many["specs"].append({"newRule":True})
    elif damage == "remove-rule": many["specs"].pop()
    elif damage == "representation": one["specs"] = [one.pop("spec")]
    elif damage == "add-second-field": one["specs"] = [{"newRule":True}]
    elif damage == "both-specs": one["specs"][0]["changed"] = True
    else: del one["specs"]
    assert web.references(baseline, snapshot["references"]) != preservation["referenceDigest"]
    with pytest.raises(ValueError): web.patch(baseline, preservation, snapshot, release, source, "rollback")
