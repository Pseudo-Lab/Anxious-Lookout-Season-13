"""Emit-only web image upgrade/rollback, preserving the reconnected API/runner."""
import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from common_chatbot_prepare import container, digest, release_specs, require, validate_baseline, validate_object
from owner_prepare import CLAIMS, storage

BASE_SOURCE = "e9f59434997bd9ca376f72e83d12e93b36eb9dbe"
BASE_RELEASE = {"sourceSha": BASE_SOURCE,
    "api": "docker.io/library/anxious-s13-chatbot-api-e9f5943@sha256:b032048fc6aa396d5fef4bab3f8991855bad257c918adced4500d74e36ae3288",
    "runner": "docker.io/library/anxious-s13-chatbot-runner-e9f5943@sha256:0158f92ebeeedcf2c237a7ca5847c46ba0c4d4335913af848456071e099bd76c",
    "web": "docker.io/library/anxious-s13-chatbot-web-e9f5943@sha256:e20cfb5ba9584cf9ef98b090229257ecd29079e7c5a5ce5210016c1f092e5e91"}
REQUIRED_REFS = {
    ("Secret", "m2-hosting", "hosting-api"), ("Secret", "m2-hosting", "hosting-access-web-input-v1"),
    ("Secret", "codex-trial", "access-web-transport-v1"),
    ("ConfigMap", "m2-hosting", "hosting-root-https-v1"), ("ConfigMap", "m2-hosting", "hosting-access-web-v1"),
    *(("PersistentVolumeClaim", "codex-trial", name) for name in CLAIMS),
    *(("PersistentVolume", "", "pvc-" + values[0]) for values in CLAIMS.values()),
    ("Service", "m2-hosting", "api"), ("Service", "m2-hosting", "web"), ("Service", "codex-trial", "access-web-runner-v1"),
    *(("CiliumNetworkPolicy", "codex-trial", name) for name in ("access-state-diagnose-deny-v1", "access-web-boundary-v1",
        "access-web-state-init-deny-v1", "trial-boundary", "trial-namespace-egress-v1")),
    *(("CiliumNetworkPolicy", "m2-hosting", name) for name in ("hosting-access-web-link-v1", "hosting-boundary")),
}


def fresh(snapshot):
    captured = datetime.fromisoformat(snapshot["checkedAt"].replace("Z", "+00:00"))
    require(captured.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - captured).total_seconds() <= 120)


def ready(deployment):
    status = deployment["status"]
    require(type(deployment["metadata"]["generation"]) is int and deployment["metadata"]["generation"] > 0)
    require(type(status["observedGeneration"]) is int)
    require(status["observedGeneration"] == deployment["metadata"]["generation"])
    for field in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas"):
        require(type(status.get(field)) is int and status[field] == 1)
    require(status.get("unavailableReplicas", 0) == 0)


def references(baseline, objects):
    indexed, result = {}, []
    for obj in objects:
        meta = obj["metadata"]
        identity = (obj["kind"], meta.get("namespace", ""), meta["name"])
        require(identity not in indexed)
        require(re.fullmatch(r"[a-f0-9-]{36}", meta["uid"]) and re.fullmatch(r"[0-9]+", meta["resourceVersion"]))
        indexed[identity] = obj
        entry = {"kind": obj["kind"], "metadata": {key: meta[key] for key in ("name", "uid")}, "namespace": meta.get("namespace", "")}
        if obj["kind"] == "Secret":
            # Only metadata projections are accepted. Never include Secret data.
            require(set(obj) <= {"kind", "apiVersion", "metadata"})
            require(set(meta) == {"namespace", "name", "uid", "resourceVersion"})
            entry["resourceVersion"] = meta["resourceVersion"]
        elif obj["kind"] == "ConfigMap":
            entry.update(data=obj["data"], immutable=obj.get("immutable"))
        else:
            require(obj["kind"] in {"PersistentVolumeClaim", "PersistentVolume", "Service", "CiliumNetworkPolicy"})
            entry["spec"] = obj["spec"]
        result.append(entry)
    require(REQUIRED_REFS <= set(indexed))
    configs = sorted([{"kind": obj["kind"], "metadata": {key: obj["metadata"][key] for key in ("name", "namespace", "uid")},
        "data": obj["data"], "immutable": obj.get("immutable")} for key, obj in indexed.items() if key[0] == "ConfigMap"], key=lambda obj: obj["metadata"]["name"])
    require(configs == baseline["configs"])
    require(storage({"pvcs": {"items": [obj for key, obj in indexed.items() if key[0] == "PersistentVolumeClaim"]},
        "pvs": {"items": [obj for key, obj in indexed.items() if key[0] == "PersistentVolume"]}}) == baseline["storage"]["node"])
    return digest(sorted(result, key=lambda entry: (entry["kind"], entry["namespace"], entry["metadata"]["name"])))


def processes(snapshot, baseline, specs):
    result = {}
    for role in ("api", "runner"):
        items = snapshot["pods"][role]["items"]
        require(len(items) == 1)
        pod = items[0]
        meta, status = pod["metadata"], pod["status"]
        require(meta["namespace"] == baseline["objects"][role]["metadata"]["namespace"] and not meta.get("deletionTimestamp"))
        require(meta["labels"]["app"] == specs[role]["template"]["metadata"]["labels"]["app"])
        require(re.fullmatch(r"[a-f0-9-]{36}", meta["uid"]))
        require(pod["spec"]["nodeName"] == baseline["storage"]["node"] and len(pod["spec"]["containers"]) == 1)
        c = pod["spec"]["containers"][0]
        require(c["name"] == container(specs[role])["name"] and c["image"] == container(specs[role])["image"])
        require(status["phase"] == "Running" and len(status["containerStatuses"]) == 1)
        info = status["containerStatuses"][0]
        require(info["name"] == c["name"] and info["ready"] is True and isinstance(info["imageID"], str) and info["imageID"])
        require(isinstance(info["containerID"], str) and info["containerID"] and type(info["restartCount"]) is int and info["restartCount"] >= 0)
        started = info["state"]["running"]["startedAt"]
        require(datetime.fromisoformat(started.replace("Z", "+00:00")).tzinfo is not None)
        owners = [entry for entry in meta["ownerReferences"] if entry.get("controller") is True]
        require(len(owners) == 1 and owners[0]["kind"] == "ReplicaSet" and owners[0]["apiVersion"] == "apps/v1")
        require(owners[0]["name"].startswith(baseline["objects"][role]["metadata"]["name"] + "-"))
        result[role] = {"uid": meta["uid"], "name": meta["name"], "containerID": info["containerID"], "imageID": info["imageID"],
            "restartCount": info["restartCount"], "startedAt": started, "owner": owners[0]}
    return result


def preserved_state(baseline, snapshot):
    validate_baseline(baseline); fresh(snapshot)
    specs = release_specs(baseline, BASE_RELEASE, BASE_SOURCE)
    require(set(snapshot["objects"]) == {"api", "runner", "web"})
    for role, obj in snapshot["objects"].items():
        validate_object(role, obj)
        if role != "web":
            require(obj["spec"] == specs[role]); ready(obj)
    return {"apiRunnerProcesses": processes(snapshot, baseline, specs),
            "referenceDigest": references(baseline, snapshot["references"])}


def seal(baseline, snapshot, known_pods):
    result = preserved_state(baseline, snapshot)
    require(set(known_pods) == {"api", "runner"})
    require(result["apiRunnerProcesses"] == processes({"pods": known_pods}, baseline,
        release_specs(baseline, BASE_RELEASE, BASE_SOURCE)))
    require(snapshot["objects"]["web"]["spec"] == release_specs(baseline, BASE_RELEASE, BASE_SOURCE)["web"])
    ready(snapshot["objects"]["web"])
    return result


def patch(baseline, preserved, snapshot, release, approved_web_sha, action):
    require(action in {"upgrade", "rollback"} and set(preserved) == {"apiRunnerProcesses", "referenceDigest"})
    require(preserved_state(baseline, snapshot) == preserved)
    require(set(release) == {"webSourceSha", "web"} and release["webSourceSha"] == approved_web_sha)
    require(re.fullmatch(r"[a-f0-9]{40}", approved_web_sha) and approved_web_sha != BASE_SOURCE)
    require(re.fullmatch(re.escape("docker.io/library/anxious-s13-chatbot-web-" + approved_web_sha[:7]) + r"@sha256:[a-f0-9]{64}", release["web"]))
    old = release_specs(baseline, BASE_RELEASE, BASE_SOURCE)["web"]
    new = copy.deepcopy(old); container(new)["image"] = release["web"]
    before, after = (old, new) if action == "upgrade" else (new, old)
    obj = snapshot["objects"]["web"]
    require(obj["spec"] == before)
    if action == "upgrade": ready(obj)
    # Only web image changes. No API hold, runner restart, auth/state reads or rearm.
    return {"target": {key: obj["metadata"][key] for key in ("namespace", "name", "uid")},
        "patch": [{"op": "test", "path": "/metadata/uid", "value": obj["metadata"]["uid"]},
                  {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
                  {"op": "test", "path": "/spec", "value": before}, {"op": "replace", "path": "/spec", "value": after}],
        "sources": {"api": BASE_SOURCE, "runner": BASE_SOURCE, "web": approved_web_sha if action == "upgrade" else BASE_SOURCE},
        "preservation": preserved}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seal", "upgrade", "rollback"))
    for name in ("baseline", "snapshot"): parser.add_argument("--" + name, required=True)
    for name in ("preserved", "release", "approved-web-sha", "known-pods"): parser.add_argument("--" + name)
    args = parser.parse_args()
    try:
        read = lambda path: json.loads(Path(path).read_text())
        result = seal(read(args.baseline), read(args.snapshot), read(args.known_pods)) if args.action == "seal" else patch(
            read(args.baseline), read(args.preserved), read(args.snapshot), read(args.release), args.approved_web_sha, args.action)
        print(json.dumps(result, indent=2))
    except Exception:
        parser.exit(1, "Web-only preparation refused; private inputs not printed\n")


if __name__ == "__main__": main()
