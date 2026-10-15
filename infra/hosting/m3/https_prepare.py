"""Emit-only native-disabled root HTTPS; private baseline, UID/RV-bound changes."""
import argparse
import copy
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from prepare import API as BASE_API, deployment_spec_for_comparison

NS = "m2-hosting"
ROOT_API = "docker.io/library/anxious-s13-root-api-8fa7854@sha256:ddd324c1078ffee5c3deef2f3fc21543f94edb25bb9ac9828c68121266fdfde3"
CONFIG = "hosting-root-https-v1"
HTTPS_ROUTE = "hosting-root-https-v1"
ENCODING = {"allowEncodedSlash": False, "allowEncodedBackSlash": False,
            "allowEncodedNullCharacter": False, "allowEncodedSemicolon": False,
            "allowEncodedPercent": False, "allowEncodedQuestionMark": False,
            "allowEncodedHash": False}
DISABLED = {"CODEX_PERSONAL_ENABLE": "false", "CODEX_PERSONAL_ROOT_ENABLE": "false",
            "CODEX_PERSONAL_ACCOUNT_ID": "", "CODEX_RUNNERS_FILE": ""}


def require(value):
    if not value:
        raise ValueError("Root HTTPS baseline mismatch")


def resource(kind, name, spec, version="traefik.io/v1alpha1"):
    return {"apiVersion": version, "kind": kind,
            "metadata": {"name": name, "namespace": NS}, "spec": spec}


def boundary(path):
    return "(Path(`" + path + "`) || PathPrefix(`" + path + "/`))"


def identity(value, kind, name):
    require(value["kind"] == kind and value["metadata"]["name"] == name
            and value["metadata"]["namespace"] == NS
            and value["metadata"].get("uid") and value["metadata"].get("resourceVersion"))


def native_off(env):
    require(len({v["name"] for v in env}) == len(env))
    for item in env:
        if item["name"] in DISABLED:
            require("valueFrom" not in item)
            require(item.get("value", "") == DISABLED[item["name"]])


def validate(snapshot):
    api, config, route = (snapshot[k] for k in ("api", "config", "httpRoute"))
    identity(api, "Deployment", "api")
    identity(config, "ConfigMap", "hosting-config")
    identity(route, "IngressRoute", "hosting")
    spec = api["spec"]
    require(spec["replicas"] == 1 and spec["strategy"] == {
        "type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}})
    pod = spec["template"]["spec"]
    require(len(pod["containers"]) == 1)
    container = pod["containers"][0]
    require(container["name"] == "api" and container["image"] == BASE_API)
    require(container["envFrom"] == [{"configMapRef": {"name": "hosting-config"}}])
    native_off(container.get("env", []))
    require(not any(v["name"] in {"AUTH_ORIGIN", "APP_BASE_PATH", "AUTH_TRUSTED_PROXY_CIDRS",
        "ALLOW_PUBLIC_IP_HTTP", "ALLOW_INSECURE_LOOPBACK"} for v in container.get("env", [])))
    data = config["data"]
    require(data["OAUTH_MODE"] == "github" and data.get("APP_BASE_PATH", "") == ""
            and data.get("AUTH_TRUSTED_PROXY_CIDRS"))
    for cidr in data["AUTH_TRUSTED_PROXY_CIDRS"].split(","):
        ipaddress.ip_network(cidr, strict=True)
    for name, value in DISABLED.items():
        require(data.get(name, value) == value)
    origin = urlsplit(data["AUTH_ORIGIN"])
    require(origin.scheme == "http" and origin.hostname and not any((origin.path,
        origin.query, origin.fragment, origin.username, origin.password)) and origin.port is None)
    # Existing target is an IPv4 literal. No caller-supplied new Host/origin.
    address = ipaddress.ip_address(origin.hostname)
    require(address.version == 4 and address.is_global)
    host = "Host(`" + origin.hostname + "`)"
    api_rule = boundary("/api")
    require(route["spec"] == {"entryPoints": ["web"], "routes": [
        {"kind": "Rule", "match": host + " && " + api_rule, "priority": 20,
            "services": [{"name": "api", "port": 8080}]},
        {"kind": "Rule", "match": host + " && PathPrefix(`/`) && !" + api_rule
            + " && !" + boundary("/android-agent"), "priority": 10,
            "services": [{"name": "web", "port": 8080}]}]})
    # Seal the observed ingress scope: new/unrecognized paths require new review,
    # rather than assuming a broad root router is safe for another application.
    seen = set()
    expected = {
        ("android-agent-download", "apk-download"): {"/android-agent"},
        ("android-agent-download", "connection-acme"): {"/.well-known/acme-challenge/"},
        ("android-agent-download", "connection-https"): {"/android-agent/", "/android-agent/connection-api"},
        ("android-agent-download", "connection-signal"): {"/android-agent/connection-api"},
        ("android-agent-download", "remote-http-redirect"): {"/android-agent/remote/"},
        ("devday-reports", "reports"): {"/"},
    }
    for item in snapshot["ingresses"]["items"]:
        key = (item["metadata"]["namespace"], item["metadata"]["name"])
        require(key in expected and key not in seen)
        seen.add(key)
        rules = item["spec"]["rules"]
        require(len(rules) == 1)
        paths = rules[0]["http"]["paths"]
        require({p["path"] for p in paths} == expected[key])
        require(all(p["pathType"] in {"Exact", "Prefix"} for p in paths))
        if key[0] == "devday-reports":
            require(rules[0].get("host") == "devday." + origin.hostname + ".sslip.io")
        else:
            require(rules[0].get("host", origin.hostname) == origin.hostname)
    require(seen == set(expected))
    return host, "https://" + origin.hostname


def desired(snapshot, tls_name):
    host, origin = validate(snapshot)
    require(re.fullmatch(r"hosting-root-tls-[a-z0-9]{1,32}", tls_name))
    data = dict(snapshot["config"]["data"])
    data.update(AUTH_ORIGIN=origin, APP_BASE_PATH="", ALLOW_PUBLIC_IP_HTTP="false",
                ALLOW_INSECURE_LOOPBACK="false", **DISABLED)
    config = {"apiVersion": "v1", "kind": "ConfigMap",
              "metadata": {"name": CONFIG, "namespace": NS}, "immutable": True, "data": data}
    api = copy.deepcopy(snapshot["api"]["spec"])
    container = api["template"]["spec"]["containers"][0]
    container["image"] = ROOT_API
    container["envFrom"] = [{"configMapRef": {"name": CONFIG}}]
    container["env"] = [v for v in container.get("env", []) if v["name"] not in DISABLED]
    container["env"] += [{"name": k, "value": v} for k, v in DISABLED.items()]
    exclusions = " && !" + boundary("/android-agent") + " && !" + boundary("/.well-known/acme-challenge")
    internal = " && !" + boundary("/api/internal")
    fixture = " && !" + boundary("/_fixture")
    api_rule = boundary("/api")
    https = resource("IngressRoute", HTTPS_ROUTE, {"entryPoints": ["websecure"],
        "tls": {"secretName": tls_name}, "routes": [
            {"kind": "Rule", "match": host + " && " + api_rule + internal + exclusions,
             "priority": 20, "middlewares": [{"name": "hosting-root-encoding-v1"}],
             "services": [{"name": "api", "port": 8080}]},
            {"kind": "Rule", "match": host + " && PathPrefix(`/`) && !" + api_rule + fixture + exclusions,
             "priority": 10, "middlewares": [{"name": "hosting-root-encoding-v1"}],
             "services": [{"name": "web", "port": 8080}]}]})
    http = {"entryPoints": ["web"], "routes": [{"kind": "Rule",
        "match": host + " && (Method(`GET`) || Method(`HEAD`)) && PathPrefix(`/`) && !" + api_rule + fixture + exclusions,
        "priority": 10, "middlewares": [{"name": "hosting-root-encoding-v1"},
                                           {"name": "hosting-root-redirect-v1"}],
        "services": [{"name": "web", "port": 8080}]}]}
    tls_probe = resource("IngressRoute", "hosting-root-tls-probe-v1", {
        "entryPoints": ["websecure"], "tls": {"secretName": tls_name}, "routes": [{
            "kind": "Rule", "match": host + " && Path(`/version.json`) && (Method(`GET`) || Method(`HEAD`))",
            "priority": 21, "middlewares": [{"name": "hosting-root-encoding-v1"}],
            "services": [{"name": "web", "port": 8080}]}]})
    return {"config": config, "api": api, "https-route": https, "http": http,
            "tls-probe": tls_probe,
            "encoding": resource("Middleware", "hosting-root-encoding-v1", {"encodedCharacters": ENCODING}),
            "redirect": resource("Middleware", "hosting-root-redirect-v1", {
                "redirectScheme": {"scheme": "https", "permanent": False}})}


def patch(snapshot, current, tls_name, role, rollback=False):
    values = desired(snapshot, tls_name)
    original = snapshot["api" if role == "api" else "httpRoute"]
    identity(current, original["kind"], original["metadata"]["name"])
    require(current["metadata"]["uid"] == original["metadata"]["uid"])
    expected = values[role] if rollback else original["spec"]
    if role == "api":
        require(deployment_spec_for_comparison(current["spec"]) == deployment_spec_for_comparison(expected))
    else:
        require(current["spec"] == expected)
    target = original["spec"] if rollback else values[role]
    checks = [{"op": "test", "path": "/metadata/uid", "value": current["metadata"]["uid"]},
              {"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
              {"op": "test", "path": "/spec", "value": current["spec"]}]
    if role == "http":
        return checks + [{"op": "replace", "path": "/spec", "value": target}]
    # Preserve all probe/resources/security/auth FILE refs, strategy and annotations.
    prefix = "/spec/template/spec/containers/0/"
    container = target["template"]["spec"]["containers"][0]
    return checks + [{"op": "replace", "path": prefix + k, "value": container[k]}
                     for k in ("image", "envFrom", "env")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", choices=("config", "encoding", "redirect", "https-route", "tls-probe",
        "api", "http", "rollback-api", "rollback-http"))
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--tls-name", required=True)
    parser.add_argument("--current")
    args = parser.parse_args()
    try:
        snapshot = json.loads(Path(args.snapshot).read_text())
        if args.part in {"api", "http", "rollback-api", "rollback-http"}:
            value = patch(snapshot, json.loads(Path(args.current).read_text()), args.tls_name,
                          args.part.removeprefix("rollback-"), args.part.startswith("rollback-"))
        else:
            value = desired(snapshot, args.tls_name)[args.part]
        print(json.dumps(value, indent=2))
    except Exception:
        parser.exit(1, "Root HTTPS emit refused; no private input values printed\n")
