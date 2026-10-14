"""Emit-only #9 application rollout. No kubectl, import, auth or model dispatch."""
import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

TARGETS = {
    "api": ("m2-hosting", "api", "dbd61092-59f0-4065-b801-43195a76340a", "8db49bee87be1ac0bc795fccc92e74ad8dd416e1c51ae0e0632d03e80f7adcb6"),
    "web": ("m2-hosting", "web", "009ecc53-a943-4bd0-b009-a286fd23c8c7", "5df76758a18f93982be5b6cfdd1ab20144d41eb85ce9a227f1bb33e3e6ecaed6"),
    "runner": ("codex-trial", "access-web-runner-v1", "cc1122e0-04ca-440c-878a-c26fdbe27622", "cd7b52d07826b5392d7b12eec2c09a042ca96304345213bc2e75907f38f95489"),
}
CONFIG_DIGEST = "11def41a5073cac218133eefa0dfa1678fc34665a5bee2f8483fb3f8b6b7839e"
ROLES = {"api", "runner", "web"}
PROTECTED_FILES = {"native/account-id", "native/state-binding.json", "native/broker.json",
                   "control/access-ledger.json", "control/access-projection.json"}
ACTIONS = {"hold-api", "upgrade-api", "upgrade-web", "stop-runner", "upgrade-runner", "start-runner",
           "resume-api", "rollback-api", "rollback-web", "rollback-runner", "resume-old-api"}


def require(value):
    if not value:
        raise ValueError("Chatbot preparation refused")


def digest(value):
    return hashlib.sha256((json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()).hexdigest()


def container(spec):
    require(len(spec["template"]["spec"]["containers"]) == 1)
    return spec["template"]["spec"]["containers"][0]


def held(spec):
    result = copy.deepcopy(spec)
    env = container(result)["env"]
    matches = [entry for entry in env if entry["name"] == "CODEX_PERSONAL_ENABLE"]
    require(len(matches) == 1 and matches[0] == {"name": "CODEX_PERSONAL_ENABLE", "value": "true"})
    matches[0]["value"] = "false"
    return result


def validate_object(role, obj, baseline=False):
    namespace, name, uid, spec_hash = TARGETS[role]
    require(obj["apiVersion"] == "apps/v1" and obj["kind"] == "Deployment")
    meta = obj["metadata"]
    require((meta["namespace"], meta["name"], meta["uid"]) == (namespace, name, uid))
    require(isinstance(meta.get("resourceVersion"), str) and re.fullmatch(r"[0-9]+", meta["resourceVersion"]))
    require(container(obj["spec"])["name"] == ("runner" if role == "runner" else role))
    require(type(obj["spec"]["replicas"]) is int and obj["spec"]["replicas"] in (0, 1))
    if baseline:
        require(digest(obj["spec"]) == spec_hash)


def validate_baseline(baseline):
    require(set(baseline) == {"objects", "configs", "storage"} and set(baseline["objects"]) == ROLES)
    for role, obj in baseline["objects"].items():
        validate_object(role, obj, baseline=True)
    require(digest(baseline["configs"]) == CONFIG_DIGEST)
    require(baseline["storage"]["claims"] == {"native": "trial-native", "control": "trial-control"})
    require(baseline["storage"]["node"] == "anxious-lookout")


def seal(directory):
    root = Path(directory)
    deployments = json.loads((root / "hosting-deployments.json").read_text())["items"]
    objects = {obj["metadata"]["name"]: obj for obj in deployments}
    require(set(objects) == {"api", "web"} and len(deployments) == 2)
    objects["runner"] = json.loads((root / "runner.json").read_text())
    # Keep only the guarded resource identity/spec; status/managedFields aren't authority.
    objects = {role: {"apiVersion": obj["apiVersion"], "kind": obj["kind"], "spec": obj["spec"],
                     "metadata": {key: obj["metadata"][key] for key in ("namespace", "name", "uid", "resourceVersion")}}
               for role, obj in objects.items()}
    configs = sorted([{"kind": obj["kind"], "metadata": {key: obj["metadata"][key] for key in ("name", "namespace", "uid")},
                       "immutable": obj.get("immutable"), "data": obj["data"]}
                      for obj in json.loads((root / "configmaps.json").read_text())["items"]], key=lambda obj: obj["metadata"]["name"])
    # Reuse reviewed exact Retain claim/volume UID, claimRef and same-node checks.
    from owner_prepare import storage
    node = storage({"pvcs": json.loads((root / "pvcs.json").read_text()), "pvs": json.loads((root / "pvs.json").read_text())})
    result = {"objects": objects, "configs": configs, "storage": {"node": node, "claims": {"native": "trial-native", "control": "trial-control"}}}
    validate_baseline(result)
    return result


def identity_digest(objects):
    return digest({role: {"uid": obj["metadata"]["uid"], "spec": obj["spec"]} for role, obj in objects.items()})


def release_specs(baseline, release, approved_source):
    require(set(release) == {"sourceSha", "api", "runner", "web"} and release["sourceSha"] == approved_source)
    require(re.fullmatch(r"[a-f0-9]{40}", approved_source))
    result = {}
    for role in ROLES:
        require(re.fullmatch(r"docker.io/library/anxious-s13-chatbot-" + role + r"-[a-f0-9]{7}@sha256:[a-f0-9]{64}", release[role]))
        require("-" + approved_source[:7] + "@" in release[role])
        spec = copy.deepcopy(baseline["objects"][role]["spec"])
        container(spec)["image"] = release[role]
        require(container(baseline["objects"][role]["spec"])["image"] != release[role])
        result[role] = spec
    return result


def check_evidence(evidence, current, backup=False, ready=False):
    # These are operator-captured private receipts, not claims of cryptographic
    # approval. Review must verify their origin and the fresh read-only checks.
    require(evidence["currentDigest"] == identity_digest(current))
    captured = datetime.fromisoformat(evidence["checkedAt"].replace("Z", "+00:00"))
    require(captured.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - captured).total_seconds() <= 120)
    for key in ("apiHoldObserved", "schemaReady", "noAmbiguousTurns", "referenceMetadataVerified"):
        require(evidence.get(key) is True)
    require(evidence.get("protectedStateVerified") is True)
    validate_protected(evidence["protectedState"])
    require(type(evidence.get("lifetimeReservations")) is int and evidence["lifetimeReservations"] > 0)
    for key in ("apiRunning", "nativeRunning"):
        require(type(evidence.get(key)) is int and evidence[key] == 0)
    if backup:
        for key in ("dbBackupSha256", "nativeControlBackupSha256"):
            require(isinstance(evidence.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", evidence[key]))
        require(evidence.get("dbRestoreVerified") is True and evidence.get("nativeControlArchiveVerified") is True)
    if ready:
        for key in ("apiReady", "webReady", "runnerReady", "podImagesVerified", "ownerStateVerified", "publicReadVerified"):
            require(evidence.get(key) is True)
        require(evidence.get("authReason") in {"not_configured", "auth_expired", "auth_revoked", "budget_exhausted", "provider_rate_limited", "model_unavailable", "policy_refused", "unavailable", None})
        require(type(evidence.get("authAvailable")) is bool)
        require(evidence["authAvailable"] == (evidence["authReason"] is None))


def no_pods(evidence, key):
    require(type(evidence.get(key)) is int and evidence[key] == 0)


def validate_protected(value):
    require(isinstance(value, dict) and set(value) == PROTECTED_FILES)
    require(all(isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item) for item in value.values()))


def receipt(current, api_probe, runner_probe, observations, backups=None, preservation=None):
    require(api_probe["admissionHeld"] is True and api_probe["schemaReady"] is True and runner_probe["ownerStateVerified"] is True)
    times = [datetime.fromisoformat(value["checkedAt"].replace("Z", "+00:00")) for value in (api_probe, runner_probe, observations)]
    now = datetime.now(timezone.utc)
    require(all(value.tzinfo is not None and 0 <= (now - value).total_seconds() <= 120 for value in times))
    require(preservation is not None and set(preservation) == {"protectedState", "lifetimeReservations"})
    validate_protected(preservation["protectedState"])
    require(type(preservation["lifetimeReservations"]) is int and preservation["lifetimeReservations"] > 0)
    require(runner_probe["protectedState"] == preservation["protectedState"])
    require(type(runner_probe["lifetimeReservations"]) is int and runner_probe["lifetimeReservations"] == preservation["lifetimeReservations"])
    allowed = {"checkedAt", "apiHoldObserved", "referenceMetadataVerified", "runnerPodCount", "backupPodCount", "apiReady", "webReady", "runnerReady",
               "podImagesVerified", "publicReadVerified", "authReason", "authAvailable"}
    require(not (set(observations) - allowed))
    result = {**observations, "checkedAt": min(times).isoformat(), "currentDigest": identity_digest(current),
              "schemaReady": True, "ownerStateVerified": True, "apiRunning": api_probe["apiRunning"], "nativeRunning": runner_probe["nativeRunning"],
              "protectedStateVerified": True, **preservation,
              "noAmbiguousTurns": api_probe["ambiguous"] == 0 and runner_probe["ambiguous"] == 0}
    if backups:
        require(set(backups) == {"dbBackupSha256", "nativeControlBackupSha256", "dbRestoreVerified", "nativeControlArchiveVerified", "protectedState", "lifetimeReservations"})
        require(backups["protectedState"] == preservation["protectedState"]
                and type(backups["lifetimeReservations"]) is int and backups["lifetimeReservations"] == preservation["lifetimeReservations"])
        result.update(backups)
    check_evidence(result, current)
    return result


def patch(baseline, release, approved_source, current, action, evidence=None):
    validate_baseline(baseline)
    require(action in ACTIONS and set(current) == ROLES)
    new = release_specs(baseline, release, approved_source)
    old = {role: obj["spec"] for role, obj in baseline["objects"].items()}
    old_hold, new_hold = held(old["api"]), held(new["api"])
    runner_states = []
    for spec in (old["runner"], new["runner"]):
        for count in (0, 1):
            value = copy.deepcopy(spec)
            value["replicas"] = count
            runner_states.append(value)
    for role, obj in current.items():
        validate_object(role, obj)
        possible = [old[role], new[role]]
        if role == "api": possible += [old_hold, new_hold]
        if role == "runner": possible = runner_states
        require(obj["spec"] in possible)  # Reject unrelated drift, including rollback.
    api = current["api"]["spec"]
    role = "runner" if "runner" in action else "web" if "web" in action else "api"
    before = current[role]["spec"]
    after = copy.deepcopy(before)
    if action == "hold-api":
        require(api in (old["api"], new["api"]))
        after = held(api)
    else:
        require(api in (old_hold, new_hold))
        require(evidence is not None)
        check_evidence(evidence, current, backup=action.startswith(("upgrade", "rollback")), ready=action.startswith("resume"))
        if action in {"stop-runner", "start-runner"}:
            require(before["replicas"] == (1 if action == "stop-runner" else 0))
            if action == "start-runner":
                no_pods(evidence, "runnerPodCount"); no_pods(evidence, "backupPodCount")
            after["replicas"] = 0 if action == "stop-runner" else 1
        elif action in {"resume-api", "resume-old-api"}:
            expected = new if action == "resume-api" else old
            require(api == held(expected["api"]))
            require(current["runner"]["spec"] == expected["runner"] and current["web"]["spec"] == expected["web"])
            after = expected["api"]
        else:
            require(current["runner"]["spec"]["replicas"] == 0)
            no_pods(evidence, "runnerPodCount"); no_pods(evidence, "backupPodCount")
            forward = action.startswith("upgrade")
            source, target = (old, new) if forward else (new, old)
            expected, after = copy.deepcopy(source[role]), copy.deepcopy(target[role])
            if role == "api": expected, after = held(expected), held(after)
            if role == "runner":
                expected["replicas"] = after["replicas"] = 0
                require(evidence.get("runnerPodCount") == 0)
            require(before == expected)
    obj = current[role]
    return {"target": {key: obj["metadata"][key] for key in ("namespace", "name", "uid")},
            "patch": [{"op": "test", "path": "/metadata/uid", "value": obj["metadata"]["uid"]},
                      {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
                      {"op": "test", "path": "/spec", "value": before},
                      {"op": "replace", "path": "/spec", "value": after}]}


def backup_reader(baseline):
    validate_baseline(baseline)
    label, namespace = "chatbot-backup-v1", "codex-trial"
    runner_env = {item["name"]: item["value"] for item in container(baseline["objects"]["runner"]["spec"])["env"] if "value" in item}
    reader_env = [{"name": key, "value": runner_env[key]} for key in ("RUNNER_ACCOUNT_ID", "RUNNER_STATE_ID", "RUNNER_STATE_DIR", "CODEX_ACCESS_CONTROL_DIR")]
    policy = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy", "metadata": {"name": label, "namespace": namespace},
              "spec": {"endpointSelector": {"matchLabels": {"app": label}}, "ingressDeny": [{"fromEntities": ["all"]}], "egressDeny": [{"toEntities": ["all"]}]}}
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": label, "namespace": namespace, "labels": {"app": label}},
           "spec": {"restartPolicy": "Never", "activeDeadlineSeconds": 240, "terminationGracePeriodSeconds": 5,
                    "serviceAccountName": "trial", "automountServiceAccountToken": False,
                    "nodeSelector": {"kubernetes.io/hostname": baseline["storage"]["node"]},
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{"name": "reader", "image": container(baseline["objects"]["api"]["spec"])["image"],
                                    "env": reader_env,
                                    "command": ["python", "-B", "-c", "import time; time.sleep(230)"],
                                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"cpu": "200m", "memory": "256Mi"}},
                                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                                    "volumeMounts": [{"name": name, "mountPath": "/" + name, "readOnly": True} for name in ("native", "control")]}],
                    "volumes": [{"name": name, "persistentVolumeClaim": {"claimName": claim, "readOnly": True}} for name, claim in baseline["storage"]["claims"].items()]}}
    return {"apiVersion": "v1", "kind": "List", "items": [policy, pod]}


def cleanup(baseline, created, current):
    expected = next((value for value in backup_reader(baseline)["items"] if value["kind"] == created["kind"]), None)
    require(expected is not None)
    from owner_prepare import require_emitted
    require_emitted(expected, created)
    require(current["kind"] == created["kind"] and current["apiVersion"] == created["apiVersion"])
    for key in ("name", "namespace", "uid"):
        require(current["metadata"][key] == created["metadata"][key])
    require(re.fullmatch(r"[a-f0-9-]{36}", created["metadata"]["uid"]))
    require(isinstance(current["metadata"]["resourceVersion"], str) and current["metadata"]["resourceVersion"])
    first, latest = copy.deepcopy(created["spec"]), copy.deepcopy(current["spec"])
    if created["kind"] == "Pod":
        for value in (first, latest):
            if "nodeName" in value: require(value.pop("nodeName") == baseline["storage"]["node"])
    require(first == latest)
    name = expected["metadata"]["name"]
    path = "/api/v1/namespaces/codex-trial/pods/" + name if created["kind"] == "Pod" else "/apis/cilium.io/v2/namespaces/codex-trial/ciliumnetworkpolicies/" + name
    return {"path": path, "body": {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
        "uid": created["metadata"]["uid"], "resourceVersion": current["metadata"]["resourceVersion"]}, "gracePeriodSeconds": 5}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seal", "receipt", "backup-reader", "cleanup", *sorted(ACTIONS)])
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--release"); parser.add_argument("--approved-source-sha")
    parser.add_argument("--current"); parser.add_argument("--evidence")
    parser.add_argument("--api-probe"); parser.add_argument("--runner-probe"); parser.add_argument("--observations"); parser.add_argument("--backups")
    parser.add_argument("--created")
    parser.add_argument("--preservation")
    args = parser.parse_args()
    try:
        read = lambda path: json.loads(Path(path).read_text())
        if args.action == "seal": result = seal(args.baseline)
        elif args.action == "backup-reader": result = backup_reader(read(args.baseline))
        elif args.action == "cleanup": result = cleanup(read(args.baseline), read(args.created), read(args.current))
        elif args.action == "receipt":
            validate_baseline(read(args.baseline))
            result = receipt(read(args.current), read(args.api_probe), read(args.runner_probe), read(args.observations), read(args.backups) if args.backups else None, read(args.preservation) if args.preservation else None)
        else: result = patch(read(args.baseline), read(args.release), args.approved_source_sha, read(args.current), args.action, read(args.evidence) if args.evidence else None)
        print(json.dumps(result, indent=2))  # Operator redirects full specs to private files.
    except Exception:
        parser.exit(1, "Chatbot preparation refused; private input values are not printed\n")


if __name__ == "__main__":
    main()
