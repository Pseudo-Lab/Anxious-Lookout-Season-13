"""Emit-only existing HTTPS owner-runner stages. No credentials/default owners."""
import argparse
import copy
import ipaddress
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from prepare import deployment_spec_for_comparison
from https_prepare import ROOT_API as OLD_API, DISABLED

API = "docker.io/library/anxious-s13-uid-api-70fbad4@sha256:5a2a95d93810bcfc035bb883d1096376d9f2cf6f48fc99f88f1f8c308c308566"
RUNNER = "docker.io/library/anxious-s13-uid-runner-70fbad4@sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87"
NS, RUN_NS = "m2-hosting", "codex-trial"
NAME, LABEL = "owner-runner-v1", "hosting-owner-runner"
API_INPUT, TRANSPORT = "hosting-owner-runner-input-v1", "owner-runner-transport-v1"
CONFIG = "hosting-owner-runner-v1"
CLAIMS = {
    "trial-native": ("12afb84f-8c3a-4282-9861-dca96457f54e", "716ac492-1f1f-46cc-9044-78b56042c067"),
    "trial-control": ("cb2f742f-6499-438c-b95c-ec11724eeb9b", "1e6f0f1b-f51e-4a0b-aecf-2115f5917b9d"),
}


def require(value):
    if not value: raise ValueError("Owner runner preparation refused")


def require_emitted(expected, actual):
    """All emitted fields must survive defaulting, including exact ordered lists."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(expected) <= set(actual))
        for key, value in expected.items(): require_emitted(value, actual[key])
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(expected) == len(actual))
        for a, b in zip(expected, actual): require_emitted(a, b)
    else: require(type(actual) is type(expected) and actual == expected)


def obj(kind, name, spec, namespace=RUN_NS, version="v1"):
    return {"apiVersion": version, "kind": kind, "metadata": {"namespace": namespace, "name": name}, "spec": spec}


def validate_api(snapshot):
    api, config = snapshot["api"], snapshot["config"]
    require(api["kind"] == "Deployment" and api["metadata"]["name"] == "api" and api["metadata"]["namespace"] == NS)
    require(api["metadata"].get("uid") and api["metadata"].get("resourceVersion"))
    require(api["spec"]["replicas"] == 1 and api["spec"]["strategy"] == {
        "type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}})
    containers = api["spec"]["template"]["spec"]["containers"]
    require(len(containers) == 1 and containers[0]["name"] == "api" and containers[0]["image"] == OLD_API)
    require(containers[0]["envFrom"] == [{"configMapRef": {"name": "hosting-root-https-v1"}}])
    require(config["kind"] == "ConfigMap" and config["metadata"]["namespace"] == NS
        and config["metadata"]["name"] == "hosting-root-https-v1" and config.get("immutable") is True)
    data = config["data"]
    origin = urlsplit(data["AUTH_ORIGIN"])
    require(origin.scheme == "https" and origin.hostname and not any((origin.path, origin.query, origin.fragment, origin.username, origin.password))
        and origin.port is None and data.get("APP_BASE_PATH", "") == "" and data["OAUTH_MODE"] == "github")
    require(data.get("AUTH_TRUSTED_PROXY_CIDRS"))
    for cidr in data["AUTH_TRUSTED_PROXY_CIDRS"].split(","): ipaddress.ip_network(cidr, strict=True)
    env = containers[0].get("env", [])
    require(len({v["name"] for v in env}) == len(env))
    for k, wanted in DISABLED.items():
        require(data.get(k, wanted) == wanted)
        for item in env:
            if item["name"] == k: require("valueFrom" not in item and item.get("value", "") == wanted)
    require(not any(v["name"] in {"AUTH_ORIGIN", "APP_BASE_PATH", "APP_ENV", "CODEX_EXECUTION_SCOPE", "CODEX_PERSONAL_REMOTE_ORIGIN"} for v in env))
    return api, config


def identifiers(inputs):
    require(set(inputs) <= {"ownerId", "stateId", "providerHosts", "procedureRef"})
    for key in ("ownerId", "stateId"):
        require(isinstance(inputs.get(key), str) and str(uuid.UUID(inputs[key])) == inputs[key] and uuid.UUID(inputs[key]).int != 0)
    return inputs["ownerId"], inputs["stateId"]


def storage(snapshot):
    nodes = []
    claims = {v["metadata"]["name"]: v for v in snapshot["pvcs"]["items"]}
    volumes = {v["metadata"]["name"]: v for v in snapshot["pvs"]["items"]}
    for name in ("trial-native", "trial-control"):
        c = claims[name]
        claim_uid, volume_uid = CLAIMS[name]
        require(c["kind"] == "PersistentVolumeClaim" and c["metadata"]["namespace"] == RUN_NS
            and c["metadata"]["uid"] == claim_uid and c["status"]["phase"] == "Bound"
            and c["spec"]["accessModes"] == ["ReadWriteOnce"]
            and c["spec"].get("volumeMode", "Filesystem") == "Filesystem"
            and c["spec"]["storageClassName"] == "codex-trial-retain")
        p = volumes[c["spec"]["volumeName"]]
        require(p["kind"] == "PersistentVolume" and p["metadata"]["uid"] == volume_uid
            and p["metadata"]["name"] == "pvc-" + claim_uid
            and p["spec"]["persistentVolumeReclaimPolicy"] == "Retain")
        ref = p["spec"]["claimRef"]
        require(ref["namespace"] == RUN_NS and ref["name"] == name and ref["uid"] == c["metadata"]["uid"])
        terms = p["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"]
        require(len(terms) == 1 and len(terms[0]["matchExpressions"]) == 1)
        expression = terms[0]["matchExpressions"][0]
        require(expression["key"] == "kubernetes.io/hostname" and expression["operator"] == "In" and len(expression["values"]) == 1)
        nodes.append(expression["values"][0])
    require(nodes[0] == nodes[1])
    return nodes[0]


def selection(label, namespace):
    return {"matchLabels": {"k8s:io.kubernetes.pod.namespace": namespace, "k8s:app": label}}


def ports(port):
    return [{"ports": [{"port": str(port), "protocol": "TCP"}]}]


def policies(inputs):
    identifiers(inputs)
    hosts = inputs.get("providerHosts")
    require(isinstance(hosts, list) and 0 < len(hosts) <= 16 and len(set(hosts)) == len(hosts))
    for host in hosts:
        require(isinstance(host, str) and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) and "." in host and ".." not in host)
        try: ipaddress.ip_address(host)
        except ValueError: pass
        else: raise ValueError("Exact DNS names required")
    require(isinstance(inputs.get("procedureRef"), str) and re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", inputs["procedureRef"]))
    api, runner = selection("hosting-api", NS), selection(LABEL, RUN_NS)
    dns_names = hosts + ["api.m2-hosting.svc.cluster.local"]
    policy = obj("CiliumNetworkPolicy", "owner-runner-boundary-v1", {
        "endpointSelector": {"matchLabels": {"app": LABEL}}, "enableDefaultDeny": {"ingress": True, "egress": True},
        "ingress": [{"fromEndpoints": [api], "toPorts": ports(8080)}],
        "egress": [{"toEndpoints": [api], "toPorts": ports(8080)},
            {"toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:k8s-app": "kube-dns"}}],
             "toPorts": [{"ports": [{"port": "53", "protocol": p} for p in ("TCP", "UDP")],
                          "rules": {"dns": [{"matchName": h} for h in dns_names]}}]},
            {"toFQDNs": [{"matchName": h} for h in hosts], "toPorts": ports(443)}]}, version="cilium.io/v2")
    bridge = obj("CiliumNetworkPolicy", "hosting-owner-runner-link-v1", {
        "endpointSelector": {"matchLabels": {"app": "hosting-api"}}, "enableDefaultDeny": {"ingress": True, "egress": True},
        "ingress": [{"fromEndpoints": [runner], "toPorts": ports(8080)}],
        "egress": [{"toEndpoints": [runner], "toPorts": ports(8080)},
                   {"toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:k8s-app": "kube-dns"}}],
                    "toPorts": [{"ports": [{"port": "53", "protocol": p} for p in ("TCP", "UDP")],
                                 "rules": {"dns": [{"matchName": NAME + ".codex-trial.svc.cluster.local"}]}}]}]}, namespace=NS, version="cilium.io/v2")
    return {"apiVersion": "v1", "kind": "List", "items": [policy, bridge]}


def pod(snapshot, container, label):
    container.update(securityContext={"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                     "capabilities": {"drop": ["ALL"]}})
    return {"metadata": {"labels": {"app": label}}, "spec": {
        "serviceAccountName": "trial", "automountServiceAccountToken": False,
        "nodeSelector": {"kubernetes.io/hostname": storage(snapshot)}, "terminationGracePeriodSeconds": 55,
        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
                            "seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [container], "volumes": [{"name": n, "persistentVolumeClaim": {"claimName": "trial-" + n}}
            for n in ("native", "control")] + [{"name": "tmp", "emptyDir": {"sizeLimit": "32Mi"}}]}}


def emit(snapshot, inputs, part):
    api, config = validate_api(snapshot)
    if part == "upgrade-api":
        spec = copy.deepcopy(api["spec"])
        spec["template"]["spec"]["containers"][0]["image"] = API
        return spec
    owner, state = identifiers(inputs)
    if part == "api-input":
        return {owner: {"url": "http://" + NAME + ".codex-trial.svc.cluster.local:8080",
                        "tokenFile": "/run/owner-input/transport-token", "stateId": state}}
    if part == "config":
        data = dict(config["data"])
        data.update(APP_ENV="personal-test", CODEX_PERSONAL_ENABLE="true", CODEX_PERSONAL_ROOT_ENABLE="true",
            CODEX_PERSONAL_ACCOUNT_ID=owner, CODEX_RUNNERS_FILE="/run/owner-input/runners.json",
            CODEX_PERSONAL_REMOTE_ORIGIN=data["AUTH_ORIGIN"], CODEX_EXECUTION_SCOPE="personal-private")
        return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": CONFIG, "namespace": NS},
                "immutable": True, "data": data}
    if part == "enable-api":
        spec = emit(snapshot, inputs, "upgrade-api")
        container = spec["template"]["spec"]["containers"][0]
        container["envFrom"] = [{"configMapRef": {"name": CONFIG}}]
        values = {k: emit(snapshot, inputs, "config")["data"][k] for k in DISABLED}
        container["env"] = [v for v in container.get("env", []) if v["name"] not in DISABLED] + [{"name": k, "value": v} for k, v in values.items()]
        require(not any(v["name"] == "owner-input" for v in spec["template"]["spec"]["volumes"]))
        spec["template"]["spec"]["volumes"].append({"name": "owner-input", "secret": {"secretName": API_INPUT,
            "defaultMode": 0o440, "items": [{"key": k, "path": k} for k in ("runners.json", "transport-token")]}})
        container["volumeMounts"].append({"name": "owner-input", "mountPath": "/run/owner-input", "readOnly": True})
        return spec
    if part == "policy": return policies(inputs)
    if part == "init-policy":
        return obj("CiliumNetworkPolicy", "owner-state-init-deny-v1", {
            "endpointSelector": {"matchLabels": {"app": "owner-state-init"}},
            "enableDefaultDeny": {"ingress": True, "egress": True},
            "ingressDeny": [{"fromEntities": ["all"]}], "egressDeny": [{"toEntities": ["all"]}]}, version="cilium.io/v2")
    if part == "init":
        template = pod(snapshot, {"name": "init", "image": API,
            "command": ["python", "-c", "import os; from runner.binding import initialize_binding; initialize_binding('/native/private',os.environ['RUNNER_ACCOUNT_ID'],os.environ['RUNNER_STATE_ID'])"],
            "env": [{"name": "RUNNER_ACCOUNT_ID", "value": owner}, {"name": "RUNNER_STATE_ID", "value": state}],
            "resources": {"requests": {"cpu": "25m", "memory": "64Mi"}, "limits": {"cpu": "100m", "memory": "128Mi"}},
            "volumeMounts": [{"name": "native", "mountPath": "/native"}, {"name": "tmp", "mountPath": "/tmp"}]}, "owner-state-init")
        template["spec"]["volumes"] = [v for v in template["spec"]["volumes"] if v["name"] != "control"]
        template["spec"].update(restartPolicy="Never", activeDeadlineSeconds=90)
        return obj("Job", "owner-state-init-v1", {"backoffLimit": 0, "activeDeadlineSeconds": 90, "template": template}, version="batch/v1")
    if part == "runner":
        policies(inputs)  # No guessed/wildcard endpoint inventory on a runnable part.
        env = {"APP_ENV": "personal-test", "CODEX_AUTH_MODE": "personal-cache", "CODEX_EXECUTION_SCOPE": "personal-private",
            "CODEX_PERSONAL_CONTROL_DIR": "/control/private", "CODEX_MODEL": "gpt-6.1-sol", "CODEX_BIN": "/usr/local/bin/codex",
            "RUNNER_ACCOUNT_ID": owner, "RUNNER_STATE_ID": state, "RUNNER_STATE_DIR": "/native/private",
            "RUNNER_TOKEN_FILE": "/run/transport/transport-token", "RESEARCH_TOOL_CALLBACK_URL": "http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools"}
        template = pod(snapshot, {"name": "runner", "image": RUNNER,
            "env": [{"name": k, "value": v} for k, v in env.items()] + [{"name": "RUNNER_POD_UID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}}}],
            "resources": {"requests": {"cpu": "100m", "memory": "192Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}},
            "volumeMounts": [{"name": n, "mountPath": "/" + n} for n in ("native", "control", "tmp")]
                + [{"name": "transport", "mountPath": "/run/transport", "readOnly": True}],
            "command": ["uvicorn", "runner.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--no-proxy-headers"]}, LABEL)
        template["spec"]["volumes"].append({"name": "transport", "secret": {"secretName": TRANSPORT, "defaultMode": 0o440,
            "items": [{"key": "transport-token", "path": "transport-token"}]}})
        template["spec"]["containers"][0]["readinessProbe"] = {"exec": {"command": ["python", "-c",
            "import json,os,urllib.request; from pathlib import Path; r=urllib.request.Request('http://127.0.0.1:8080/health',headers={'Authorization':'Bearer '+Path(os.environ['RUNNER_TOKEN_FILE']).read_text().strip(),'X-Runner-State-Id':os.environ['RUNNER_STATE_ID']}); v=json.load(urllib.request.urlopen(r,timeout=2)); assert v['available'] is True and v['ownerId']==os.environ['RUNNER_ACCOUNT_ID'] and v['stateId']==os.environ['RUNNER_STATE_ID']"]},
            "initialDelaySeconds": 5, "periodSeconds": 10, "timeoutSeconds": 3, "failureThreshold": 1}
        return obj("Deployment", NAME, {"replicas": 1, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": LABEL}}, "template": template}, version="apps/v1")
    if part == "service":
        return obj("Service", NAME, {"type": "ClusterIP", "selector": {"app": LABEL}, "ports": [{"port": 8080, "targetPort": 8080}]})
    raise ValueError()


def patch(snapshot, inputs, current, action):
    if action in {"runner-stop", "runner-start"}:
        desired = emit(snapshot, inputs, "runner")["spec"]
        baseline, dryrun = snapshot["runner"], snapshot["runnerDryRun"]
        # Kubernetes defaulted spec is retained verbatim, never guessed or adopted
        # from a later GET. PM records/reviews server dry-run against emission and
        # its first applied GET before this baseline can authorize lifecycle work.
        require(baseline["spec"] == dryrun["spec"] and baseline["spec"]["replicas"] == 1)
        require(baseline["kind"] == "Deployment" and baseline["metadata"]["namespace"] == RUN_NS
            and baseline["metadata"]["name"] == NAME and baseline["metadata"].get("uid"))
        require_emitted(desired, baseline["spec"])
        require(baseline["spec"]["strategy"] == desired["strategy"]
            and baseline["spec"]["selector"] == desired["selector"])
        expected = copy.deepcopy(baseline["spec"])
        target = copy.deepcopy(expected)
        if action == "runner-stop": target["replicas"] = 0
        else: expected["replicas"] = 0
        require(current["kind"] == "Deployment" and current["metadata"]["name"] == NAME
            and current["metadata"]["namespace"] == RUN_NS and current["metadata"].get("uid") == baseline["metadata"]["uid"]
            and current["metadata"].get("resourceVersion") and current["spec"] == expected)
        return [{"op": "test", "path": "/metadata/uid", "value": current["metadata"]["uid"]},
                {"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
                {"op": "test", "path": "/spec", "value": current["spec"]},
                {"op": "replace", "path": "/spec/replicas", "value": target["replicas"]}]
    original, _ = validate_api(snapshot)
    upgrade = emit(snapshot, {}, "upgrade-api")
    if action == "upgrade": expected, target = original["spec"], upgrade
    elif action == "rollback-upgrade": expected, target = upgrade, original["spec"]
    elif action == "enable": expected, target = upgrade, emit(snapshot, inputs, "enable-api")
    elif action == "disable": expected, target = emit(snapshot, inputs, "enable-api"), upgrade
    else: raise ValueError()
    require(current["kind"] == "Deployment" and current["metadata"]["name"] == "api" and current["metadata"]["namespace"] == NS
        and current["metadata"]["uid"] == original["metadata"]["uid"])
    require(deployment_spec_for_comparison(current["spec"]) == deployment_spec_for_comparison(expected))
    return [{"op": "test", "path": "/metadata/uid", "value": current["metadata"]["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
            {"op": "test", "path": "/spec", "value": current["spec"]},
            {"op": "replace", "path": "/spec", "value": target}]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", choices=("upgrade", "rollback-upgrade", "enable", "disable", "runner-stop", "runner-start", "api-input", "config", "init-policy", "init", "policy", "runner", "service"))
    parser.add_argument("--snapshot", required=True); parser.add_argument("--inputs"); parser.add_argument("--current")
    args = parser.parse_args()
    try:
        snapshot = json.loads(Path(args.snapshot).read_text())
        inputs = json.loads(Path(args.inputs).read_text()) if args.inputs else {}
        value = patch(snapshot, inputs, json.loads(Path(args.current).read_text()), args.part) if args.part in {"upgrade", "rollback-upgrade", "enable", "disable", "runner-stop", "runner-start"} else emit(snapshot, inputs, args.part)
        print(json.dumps(value, indent=2))
    except Exception:
        parser.exit(1, "Owner-runner emit refused; private values are not printed\n")
