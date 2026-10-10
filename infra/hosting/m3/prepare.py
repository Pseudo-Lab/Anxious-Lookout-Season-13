"""Emit-only existing m2-hosting M3 jobs and UID/RV-bound API JSON patches."""
import argparse
import hashlib
import json
from pathlib import Path

API = "docker.io/library/anxious-s13-personal-api@sha256:41403b6a5ab16bee5cfb936879761f8a31420085236794de347eb410944defca"
OLD_API = "docker.io/library/anxious-hosting-api@sha256:843880452e3b53c62c46fc932356263461b43aeed23b0b3f46896f36b1767e6f"
DISABLED = {"CODEX_PERSONAL_ENABLE": "false", "CODEX_PERSONAL_ACCOUNT_ID": "", "CODEX_RUNNERS_FILE": ""}
CM = "hosting-m3-verifier-v1"


def require(value):
    if not value:
        raise ValueError("Existing rollout baseline mismatch")


def jobs():
    code = Path(__file__).with_name("verify_database.py").read_text()
    cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": CM, "namespace": "m2-hosting",
          "annotations": {"issue7/checker-sha256": hashlib.sha256(code.encode()).hexdigest()}},
          "immutable": True, "data": {"verify_database.py": code}}
    values = [cm]
    for stage in ("before", "migrate", "after"):
        container = {"name": "ops", "image": API,
            "command": ["python", "-m", "app.migrate", "--revision", "0004_publication"] if stage == "migrate"
                else ["python", "/checks/verify_database.py", stage],
            "env": [{"name": key, "value": value} for key, value in DISABLED.items()] + [
                {"name": "ADMIN_DATABASE_URL_FILE", "value": "/run/ops/database-url"},
                {"name": "API_DATABASE_PASSWORD_FILE", "value": "/run/ops/api-password"},
                {"name": "DATABASE_URL_FILE", "value": "/run/api/database-url"},
                {"name": "PYTHONPATH", "value": "/app"},
                {"name": "PGOPTIONS", "value": "-c statement_timeout=120000 -c lock_timeout=10000"}],
            "resources": {"requests": {"cpu": "25m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "192Mi"}},
            "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
            "volumeMounts": [{"name": name, "mountPath": path, "readOnly": True} for name, path in
                (("ops", "/run/ops"), ("api", "/run/api"), ("checks", "/checks"))] + [{"name": "tmp", "mountPath": "/tmp"}]}
        values.append({"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "hosting-m3-"+stage+"-v1", "namespace": "m2-hosting"},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 180, "template": {"metadata": {"labels": {"app": "hosting-ops"}}, "spec": {
                "serviceAccountName": "hosting", "automountServiceAccountToken": False, "restartPolicy": "Never",
                "terminationGracePeriodSeconds": 30, "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                    "runAsGroup": 10001, "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [container], "volumes": [
                    {"name": "ops", "secret": {"secretName": "hosting-ops", "defaultMode": 288}},
                    {"name": "api", "secret": {"secretName": "hosting-api", "defaultMode": 288,
                        "items": [{"key": "database-url", "path": "database-url"}]}},
                    {"name": "checks", "configMap": {"name": CM}}, {"name": "tmp", "emptyDir": {"sizeLimit": "32Mi"}}]}}}})
    return values


def patch(original, current, rollback=False):
    for value in (original, current):
        require(value["kind"] == "Deployment" and value["metadata"]["name"] == "api" and value["metadata"]["namespace"] == "m2-hosting"
            and value["spec"]["replicas"] == 1 and value["spec"]["strategy"] == {"type":"RollingUpdate", "rollingUpdate":{"maxSurge":1,"maxUnavailable":0}})
        require(len(value["spec"]["template"]["spec"]["containers"]) == 1)
        require(value["spec"]["template"]["spec"]["containers"][0]["name"] == "api")
    require(original["metadata"]["uid"] == current["metadata"]["uid"])
    old = original["spec"]["template"]["spec"]["containers"][0]
    now = current["spec"]["template"]["spec"]["containers"][0]
    require(old["image"] == OLD_API and now["image"] == (API if rollback else OLD_API))
    env = old.get("env", [])
    require(len({item["name"] for item in env}) == len(env))
    require(all(item.get("value", "") in {"", "false"} and "valueFrom" not in item for item in env if item["name"] in DISABLED))
    wanted = [item for item in env if item["name"] not in DISABLED] + [{"name":key,"value":value} for key,value in DISABLED.items()]
    # Everything outside image and the explicit disable env must match the approved baseline.
    expected = json.loads(json.dumps(original["spec"]))
    expected["template"]["spec"]["containers"][0].update(image=now["image"], env=wanted if rollback else env)
    require(current["spec"] == expected)
    return [{"op":"test","path":"/metadata/uid","value":current["metadata"]["uid"]},
            {"op":"test","path":"/metadata/resourceVersion","value":current["metadata"]["resourceVersion"]},
            {"op":"replace","path":"/spec/template/spec/containers/0/image","value":OLD_API if rollback else API},
            {"op":"add","path":"/spec/template/spec/containers/0/env","value":env if rollback else wanted}]


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part",choices=("before","migrate","after","checker","api","rollback-api"))
    parser.add_argument("--original");parser.add_argument("--current")
    args=parser.parse_args()
    try:
        if args.part in {"api","rollback-api"}:
            value=patch(json.loads(Path(args.original).read_text()),json.loads(Path(args.current).read_text()),args.part=="rollback-api")
        else:
            value=next(item for item in jobs() if item["metadata"]["name"] == (CM if args.part=="checker" else "hosting-m3-"+args.part+"-v1"))
        print(json.dumps(value,indent=2))
    except Exception:
        parser.exit(1,"Existing rollout emit refused; no private input values printed\n")
