"""Structural checks for boundaries that are meaningful before actual apply."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from render import NS, BASE, ENCODING, render
from render_approval import approval


@pytest.fixture
def inputs():
    return json.loads(Path(__file__).with_name("input.example.json").read_text())


def keyed(bundle):
    return {(item["kind"], item["metadata"]["name"]): item for item in bundle["items"]}


def test_bootstrap_has_only_new_trial_resources_and_disabled_provider(inputs):
    items = keyed(render(inputs))
    assert all(item["metadata"].get("namespace") == NS for item in items.values()
               if item["kind"] not in {"Namespace", "StorageClass"})
    assert set(kind for kind, _ in items) <= {"Namespace", "StorageClass", "ServiceAccount", "CiliumNetworkPolicy",
        "PersistentVolumeClaim", "StatefulSet", "Service", "Job", "ConfigMap", "Deployment", "Middleware", "IngressRoute"}
    cfg = items["ConfigMap", "trial-config"]["data"]
    assert cfg["APP_BASE_PATH"] == BASE and cfg["CODEX_PERSONAL_ENABLE"] == "false" and cfg["CODEX_RUNNERS_FILE"] == ""
    assert not any(key in cfg for key in ("ALLOW_PUBLIC_IP_HTTP", "ALLOW_INSECURE_LOOPBACK", "CODEX_PERSONAL_ACCOUNT_ID"))
    assert ("Deployment", "runner") not in items
    assert items["StorageClass", "codex-trial-retain"]["reclaimPolicy"] == "Retain"
    assert items["PersistentVolumeClaim", "trial-db"]["spec"]["storageClassName"] == "codex-trial-retain"
    assert items["StatefulSet", "postgres"]["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] == "codex_trial"


def test_trial_route_local_fields_and_upstream_path_contract(inputs):
    items = keyed(render(inputs))
    spec = items["IngressRoute", "trial"]["spec"]
    assert spec["entryPoints"] == ["websecure"] and spec["tls"] == {"secretName": inputs["tlsSecret"]}
    assert items["Middleware", "trial-encoding"]["spec"] == {"encodedCharacters": ENCODING}
    assert items["Middleware", "trial-access"]["spec"] == {"ipAllowList": {"sourceRange": inputs["clientCidrs"]}}
    for route in spec["routes"]:
        assert route["middlewares"] == [{"name": "trial-encoding"}, {"name": "trial-access"}]
        assert "Path(`/codex-trial/api`) || PathPrefix(`/codex-trial/api/`)" in route["match"]
    assert "Path(`/codex-trial/api/internal`)" in spec["routes"][0]["match"]
    assert "Path(`/codex-trial/_fixture`)" in spec["routes"][1]["match"]
    assert "Path(`/codex-trial`) || PathPrefix(`/codex-trial/`)" in spec["routes"][1]["match"]


def test_secret_roles_pod_privileges_and_prefix_probe(inputs):
    items = keyed(render(inputs))
    for kind, name in (("Deployment", "api"), ("Deployment", "web"), ("StatefulSet", "postgres"), ("Job", "trial-migrate-0004")):
        pod = items[kind, name]["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["dnsConfig"]["options"] == [{"name": "ndots", "value": "1"}]
        for container in pod["containers"]:
            assert container["securityContext"]["readOnlyRootFilesystem"] is True
            assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
            assert "cpu" in container["resources"]["limits"] and "memory" in container["resources"]["limits"]
        assert not any("hostPath" in volume for volume in pod["volumes"])
    api = items["Deployment", "api"]["spec"]["template"]["spec"]
    assert {v["secret"]["secretName"] for v in api["volumes"] if "secret" in v} == {"trial-api", "trial-github"}
    assert "--no-proxy-headers" in api["containers"][0]["command"]
    assert "/codex-trial/version.json" in items["Deployment", "web"]["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["exec"]["command"][-1]
    migrate = items["Job", "trial-migrate-0004"]["spec"]["template"]["spec"]["containers"][0]
    assert migrate["image"] == inputs["apiImage"] and migrate["command"][-1] == "0004_publication"


def test_policies_keep_trial_selectors_and_no_default_external_allow(inputs):
    specs = keyed(render(inputs))["CiliumNetworkPolicy", "trial-boundary"]["specs"]
    assert {s["endpointSelector"]["matchLabels"]["app"] for s in specs} == {
        "trial-api", "trial-web", "trial-postgres", "trial-ops", "trial-state-loader", "trial-runner"}
    for spec in specs:
        assert spec["enableDefaultDeny"] == {"ingress": True, "egress": True}
    api = specs[0]
    assert api["egress"][2]["toFQDNs"] == [{"matchName": "github.com"}, {"matchName": "api.github.com"}]
    assert specs[-1]["egress"] == [] and specs[1]["egress"] == []
    for spec in specs:
        for rule in spec["ingress"] + spec["egress"]:
            assert not any(key in rule for key in ("toEntities", "fromEntities", "toCIDR", "toCIDRSet"))
            for key in ("toEndpoints", "fromEndpoints"):
                for target in rule.get(key, []):
                    namespace = target["matchLabels"]["k8s:io.kubernetes.pod.namespace"]
                    assert namespace in {NS, "kube-system"}
    assert render(inputs, "foundation")["items"][-1]["kind"] == "CiliumNetworkPolicy"
    assert all(i["kind"] != "IngressRoute" for i in render(inputs, "app")["items"])


def test_initial_access_probe_exposes_only_nonsensitive_version_and_no_api(inputs):
    items = keyed(render(inputs, "access-probe"))
    route = items["IngressRoute", "trial-access-probe"]["spec"]["routes"][0]
    assert route["match"] == "Host(`trial.example.invalid`) && Path(`/codex-trial/version.json`)"
    assert route["services"] == [{"name": "web", "port": 8080}]
    assert route["middlewares"] == [{"name": "trial-encoding"}, {"name": "trial-access"}]
    web = keyed(render(inputs, "web"))
    assert set(web) == {("Deployment", "web"), ("Service", "web")}


@pytest.mark.parametrize("field,value", [("origin", "http://trial.example.invalid"), ("origin", "https://trial.example.invalid:443"),
    ("origin", "https://trial.example.invalid/codex-trial"), ("origin", "https://user@trial.example.invalid"),
    ("apiImage", "api:latest"), ("webBasePath", ""), ("tlsSecret", "connection-ip-tls"), ("clientId", ""),
    ("clientCidrs", []), ("clientCidrs", ["0.0.0.0/0"]), ("clientCidrs", ["10.0.0.0/8"]),
    ("clientCidrs", ["93.184.216.0/24"]), ("trustedProxyCidrs", []), ("trustedProxyCidrs", ["0.0.0.0/0"]),
    ("trustedProxyCidrs", ["127.0.0.1/32"]), ("unknown", "sentinel")])
def test_missing_unsafe_or_wrong_boundary_refused(inputs, field, value):
    inputs[field] = value
    with pytest.raises(ValueError):
        render(inputs)


def test_missing_input_refused(inputs):
    for field in tuple(inputs):
        missing = deepcopy(inputs)
        del missing[field]
        with pytest.raises(ValueError):
            render(missing)


def test_enable_requires_single_owner_and_exact_endpoint_inventory(inputs):
    inputs["runner"] = {"owner": "00000000-0000-4000-8000-000000000007", "image": inputs["apiImage"],
                        "providerHosts": ["native.example.invalid"], "procedureRef": "private-review/reference"}
    items = keyed(render(inputs))
    assert items["ConfigMap", "trial-config"]["data"]["CODEX_PERSONAL_REMOTE_ORIGIN"] == inputs["origin"]
    runner = items["Deployment", "runner"]["spec"]["template"]["spec"]
    env = {item["name"]: item["value"] for item in runner["containers"][0]["env"]}
    assert env["RUNNER_STATE_DIR"] == "/native/private" and env["CODEX_PERSONAL_CONTROL_DIR"] == "/control/private"
    assert env["RESEARCH_TOOL_CALLBACK_URL"] == "http://api.codex-trial.svc.cluster.local:8080/codex-trial/api/internal/research/tools"
    assert env["CODEX_MODEL"] == "gpt-6.1-sol"
    assert runner["securityContext"]["runAsUser"] == 10001
    for bad in ([], ["*.example.invalid"], ["93.184.216.34"], ["native.example.invalid/path"]):
        inputs["runner"]["providerHosts"] = bad
        with pytest.raises(ValueError):
            render(inputs)


def test_operator_approval_is_explicit_trial_only_and_uses_reviewed_image(inputs):
    decision = {"githubId": "700007", "approved": True, "role": "editor", "actor": "operator:synthetic", "reason": "synthetic verified identity"}
    job = approval(inputs, decision)
    assert job["metadata"] == {"namespace": NS, "generateName": "trial-approval-"}
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == inputs["apiImage"] and container["command"][2] == "app.admin"
    assert container["env"][0] == {"name": "ADMIN_DATABASE_URL_FILE", "value": "/run/ops/admin-url"}
    for change in ({"approved": 1}, {"githubId": "login"}, {"role": "unknown"}, {"actor": ""}):
        with pytest.raises(ValueError):
            approval(inputs, {**decision, **change})
