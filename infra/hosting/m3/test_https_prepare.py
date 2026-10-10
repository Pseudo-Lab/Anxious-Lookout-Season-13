"""Preservation, drift refusal and routing boundaries for the existing HTTPS step."""
import copy

import pytest

from https_prepare import BASE_API, CONFIG, DISABLED, ROOT_API, boundary, desired, patch, validate

HOST = "93.184.216.34"
TLS = "hosting-root-tls-v1"


@pytest.fixture
def snapshot():
    host = "Host(`" + HOST + "`)"
    def obj(kind, name, spec=None):
        return {"kind": kind, "metadata": {"name": name, "namespace": "m2-hosting",
                "uid": "owned-" + name, "resourceVersion": "123"}, **({"spec": spec} if spec is not None else {})}
    api = obj("Deployment", "api", {"replicas": 1,
        "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
        "template": {"metadata": {"labels": {"app": "hosting-api"}}, "spec": {"containers": [{
            "name": "api", "image": BASE_API, "envFrom": [{"configMapRef": {"name": "hosting-config"}}],
            "env": [{"name": "DATABASE_URL_FILE", "value": "/run/secrets/database-url"},
                    {"name": "CODEX_PERSONAL_ENABLE", "value": "false"},
                    {"name": "CODEX_RUNNERS_FILE"}, {"name": "CODEX_PERSONAL_ACCOUNT_ID"}],
            "resources": {"requests": {"cpu": "50m", "memory": "96Mi"}},
            "volumeMounts": [{"name": "credentials", "mountPath": "/run/secrets", "readOnly": True}],
            "readinessProbe": {"exec": {"command": ["python", "-m", "app.probe", "/readyz"]}}}],
            "volumes": [{"name": "credentials", "secret": {"secretName": "hosting-api"}}]}}})
    config = obj("ConfigMap", "hosting-config")
    config["data"] = {"OAUTH_MODE": "github", "AUTH_ORIGIN": "http://" + HOST,
        "APP_BASE_PATH": "", "AUTH_TRUSTED_PROXY_CIDRS": "10.42.0.0/16",
        "ALLOW_PUBLIC_IP_HTTP": "true", "GITHUB_CLIENT_ID": "synthetic-existing-id"}
    route = obj("IngressRoute", "hosting", {"entryPoints": ["web"], "routes": [
        {"kind": "Rule", "match": host + " && " + boundary("/api"), "priority": 20,
         "services": [{"name": "api", "port": 8080}]},
        {"kind": "Rule", "match": host + " && PathPrefix(`/`) && !" + boundary("/api")
         + " && !" + boundary("/android-agent"), "priority": 10,
         "services": [{"name": "web", "port": 8080}]}]})
    inputs = [
        ("android-agent-download", "apk-download", ["/android-agent"]),
        ("android-agent-download", "connection-acme", ["/.well-known/acme-challenge/"]),
        ("android-agent-download", "connection-https", ["/android-agent/", "/android-agent/connection-api"]),
        ("android-agent-download", "connection-signal", ["/android-agent/connection-api"]),
        ("android-agent-download", "remote-http-redirect", ["/android-agent/remote/"]),
        ("devday-reports", "reports", ["/"]),
    ]
    ingresses = []
    for ns, name, paths in inputs:
        rule = {"http": {"paths": [{"path": p, "pathType": "Prefix"} for p in paths]}}
        if ns == "devday-reports":
            rule["host"] = "devday." + HOST + ".sslip.io"
        ingresses.append({"metadata": {"namespace": ns, "name": name}, "spec": {"rules": [rule]}})
    return {"api": api, "config": config, "httpRoute": route, "ingresses": {"items": ingresses}}


def test_changes_only_image_config_reference_and_explicit_disable_env(snapshot):
    before = copy.deepcopy(snapshot)
    result = desired(snapshot, TLS)
    container = result["api"]["template"]["spec"]["containers"][0]
    assert container["image"] == ROOT_API
    assert container["envFrom"] == [{"configMapRef": {"name": CONFIG}}]
    assert {e["name"]: e.get("value", "") for e in container["env"] if e["name"] in DISABLED} == DISABLED
    original = snapshot["api"]["spec"]["template"]["spec"]["containers"][0]
    restored = copy.deepcopy(result["api"])
    restored["template"]["spec"]["containers"][0].update({k: original[k] for k in ("image", "env", "envFrom")})
    assert restored == snapshot["api"]["spec"] and snapshot == before
    assert result["config"]["immutable"] is True
    config = result["config"]["data"]
    assert config["AUTH_ORIGIN"] == "https://" + HOST
    assert config["GITHUB_CLIENT_ID"] == before["config"]["data"]["GITHUB_CLIENT_ID"]
    assert config["ALLOW_PUBLIC_IP_HTTP"] == config["ALLOW_INSECURE_LOOPBACK"] == "false"


@pytest.mark.parametrize("role", ["api", "http"])
def test_uid_rv_full_spec_checked_forward_and_exact_rollback(snapshot, role):
    key = "api" if role == "api" else "httpRoute"
    current = copy.deepcopy(snapshot[key])
    current["metadata"]["resourceVersion"] = "456"
    changes = patch(snapshot, current, TLS, role)
    assert changes[:3] == [
        {"op": "test", "path": "/metadata/uid", "value": current["metadata"]["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "456"},
        {"op": "test", "path": "/spec", "value": current["spec"]}]
    current["spec"] = desired(snapshot, TLS)[role]
    undo = patch(snapshot, current, TLS, role, rollback=True)
    if role == "http":
        assert undo[-1]["value"] == snapshot[key]["spec"]
    else:
        original = snapshot[key]["spec"]["template"]["spec"]["containers"][0]
        assert [v["value"] for v in undo[3:]] == [original[k] for k in ("image", "envFrom", "env")]


@pytest.mark.parametrize("role", ["api", "http"])
@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("change", ["uid", "spec"])
def test_changed_resource_or_spec_refuses_even_during_rollback(snapshot, role, rollback, change):
    current = copy.deepcopy(snapshot["api" if role == "api" else "httpRoute"])
    if rollback:
        current["spec"] = desired(snapshot, TLS)[role]
    if change == "uid":
        current["metadata"]["uid"] = "replacement"
    else:
        current["spec"]["unreviewed"] = True
    with pytest.raises(ValueError):
        patch(snapshot, current, TLS, role, rollback)


@pytest.mark.parametrize("key,value", [
    ("OAUTH_MODE", "mock"), ("APP_BASE_PATH", "/codex-trial"),
    ("AUTH_TRUSTED_PROXY_CIDRS", ""), ("AUTH_TRUSTED_PROXY_CIDRS", "not-cidr"),
    ("AUTH_ORIGIN", "https://" + HOST), ("AUTH_ORIGIN", "http://127.0.0.1"),
    ("AUTH_ORIGIN", "http://" + HOST + "/path"), ("CODEX_PERSONAL_ENABLE", "true"),
    ("CODEX_PERSONAL_ROOT_ENABLE", "true"), ("CODEX_PERSONAL_ACCOUNT_ID", "owner"),
    ("CODEX_RUNNERS_FILE", "/map.json"),
])
def test_unsafe_config_baseline_refused(snapshot, key, value):
    snapshot["config"]["data"][key] = value
    with pytest.raises(ValueError):
        validate(snapshot)


@pytest.mark.parametrize("env", [
    [{"name": "CODEX_PERSONAL_ENABLE", "value": "true"}],
    [{"name": "CODEX_PERSONAL_ROOT_ENABLE", "valueFrom": {"secretKeyRef": {"name": "unknown", "key": "x"}}}],
    [{"name": "AUTH_ORIGIN", "value": "https://other.invalid"}],
    [{"name": "CODEX_RUNNERS_FILE", "value": "/enabled.json"}],
    [{"name": "CODEX_PERSONAL_ENABLE", "value": "false"}] * 2,
])
def test_native_auth_overrides_or_duplicate_env_refused(snapshot, env):
    snapshot["api"]["spec"]["template"]["spec"]["containers"][0]["env"] = env
    with pytest.raises(ValueError):
        validate(snapshot)


@pytest.mark.parametrize("change", ["new-ingress", "path", "host", "http-route", "image", "envFrom", "strategy"])
def test_unreviewed_target_routes_or_api_drift_refused(snapshot, change):
    if change == "new-ingress":
        snapshot["ingresses"]["items"].append({"metadata": {"namespace": "other", "name": "app"}})
    elif change in {"path", "host"}:
        rule = snapshot["ingresses"]["items"][0]["spec"]["rules"][0]
        if change == "path":
            rule["http"]["paths"][0]["path"] = "/another-app"
        else:
            rule["host"] = "other.invalid"
    elif change == "http-route":
        snapshot["httpRoute"]["spec"]["routes"][0]["priority"] = 999
    elif change == "strategy":
        snapshot["api"]["spec"]["strategy"] = {"type": "Recreate"}
    else:
        c = snapshot["api"]["spec"]["template"]["spec"]["containers"][0]
        c[change] = "different"
    with pytest.raises(ValueError):
        validate(snapshot)


def test_external_paths_and_http_api_never_fall_through_to_root(snapshot):
    result = desired(snapshot, TLS)
    for route in result["https-route"]["spec"]["routes"] + result["http"]["routes"]:
        assert " && !" + boundary("/android-agent") in route["match"]
        assert " && !" + boundary("/.well-known/acme-challenge") in route["match"]
    https_api = result["https-route"]["spec"]["routes"][0]
    assert " && !" + boundary("/api/internal") in https_api["match"]
    http = result["http"]["routes"]
    assert len(http) == 1 and http[0]["services"][0]["name"] == "web"
    assert "(Method(`GET`) || Method(`HEAD`))" in http[0]["match"]
    assert " && !" + boundary("/api") in http[0]["match"]
    assert result["redirect"]["spec"]["redirectScheme"]["permanent"] is False
    assert len(result["encoding"]["spec"]["encodedCharacters"]) == 7
    assert set(result["encoding"]["spec"]["encodedCharacters"].values()) == {False}


def test_tls_probe_has_no_api_or_full_ui_route_and_no_origin_change(snapshot):
    result = desired(snapshot, TLS)["tls-probe"]
    assert result["metadata"]["name"] == "hosting-root-tls-probe-v1"
    routes = result["spec"]["routes"]
    assert len(routes) == 1 and "Path(`/version.json`)" in routes[0]["match"]
    assert "PathPrefix" not in routes[0]["match"]
    assert "(Method(`GET`) || Method(`HEAD`))" in routes[0]["match"]
    assert routes[0]["services"] == [{"name": "web", "port": 8080}]


@pytest.mark.parametrize("name", ["connection-ip-tls", "../secret", "hosting-root-tls-", "hosting-root-tls-A"])
def test_only_new_target_generation_names_allowed(snapshot, name):
    with pytest.raises(ValueError):
        desired(snapshot, name)
