"""Emit JSON Kubernetes Lists only; no cluster/network/Secret reads or writes.

Run inside the reviewed API image with this directory mounted read-only. Inputs
and rendered output are private operator artifacts. See README for staged apply.
"""
import argparse
import ipaddress
import json
import re
import sys
import uuid
from urllib.parse import urlsplit

NS = "codex-trial"
BASE = "/codex-trial"
POSTGRES = "postgres:18.6-bookworm@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c"
ENCODING = {"allowEncoded" + key: False for key in
            ("Slash", "BackSlash", "NullCharacter", "Semicolon", "Percent", "QuestionMark", "Hash")}
PARTS = ("foundation", "policy", "data", "migrate", "web", "access-probe", "app", "ingress", "runner")


def require(condition):
    if not condition:
        raise ValueError("Invalid trial configuration")


def image(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[0-9a-f]{64}", value))
    return value


def config(value):
    required = {"origin", "apiImage", "webImage", "webBasePath", "tlsSecret", "clientId",
                "clientCidrs", "trustedProxyCidrs"}
    require(isinstance(value, dict) and required <= value.keys()
            and not value.keys() - required - {"runner"})
    parsed = urlsplit(value["origin"])
    require(parsed.scheme == "https" and parsed.hostname and not
            any((parsed.path, parsed.query, parsed.fragment, parsed.username, parsed.password))
            and parsed.port is None and value["origin"] == "https://" + parsed.hostname)
    require(re.fullmatch(r"[a-z0-9.-]+", parsed.hostname) and ".." not in parsed.hostname)
    image(value["apiImage"])
    image(value["webImage"])
    require(value["webBasePath"] == BASE)
    require(isinstance(value["tlsSecret"], str) and re.fullmatch(r"trial-tls-[a-z0-9-]{1,50}", value["tlsSecret"]))
    require(isinstance(value["clientId"], str) and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value["clientId"]))
    for key in ("clientCidrs", "trustedProxyCidrs"):
        require(isinstance(value[key], list) and 0 < len(value[key]) <= 8)
        for cidr in value[key]:
            require(isinstance(cidr, str))
            network = ipaddress.ip_network(cidr, strict=True)
            require(cidr == str(network))
            if key == "clientCidrs":
                # Actual RemoteAddr preservation must be proven separately. No
                # header-derived identity or broad NAT/private range exception.
                require(network.prefixlen == network.max_prefixlen and network.network_address.is_global)
            else:
                require(network.network_address.is_private and not network.network_address.is_loopback
                        and not network.network_address.is_link_local and network.prefixlen >= 16)
    runner = value.get("runner")
    if runner is not None:
        require(isinstance(runner, dict) and set(runner) == {"owner", "image", "providerHosts", "procedureRef"})
        require(str(uuid.UUID(runner["owner"])) == runner["owner"])
        image(runner["image"])
        require(isinstance(runner["procedureRef"], str) and re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", runner["procedureRef"]))
        require(isinstance(runner["providerHosts"], list) and 0 < len(runner["providerHosts"]) <= 16)
        for host in runner["providerHosts"]:
            require(isinstance(host, str) and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", host))
            try:
                ipaddress.ip_address(host)
            except ValueError:
                continue
            raise ValueError("Native endpoint inventory requires exact DNS names")
    return value


def resource(kind, name, spec=None, version="v1", **extra):
    value = {"apiVersion": version, "kind": kind, "metadata": {"name": name, "namespace": NS}, **extra}
    if spec is not None:
        value["spec"] = spec
    return value


def selection(app, namespace=NS):
    return {"matchLabels": {"k8s:io.kubernetes.pod.namespace": namespace, "k8s:app": "trial-" + app}}


def ports(port):
    return [{"ports": [{"port": str(port), "protocol": "TCP"}]}]


def endpoint(app, port):
    return {"toEndpoints": [selection(app)], "toPorts": ports(port)}


def dns(names):
    return {"toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:k8s-app": "kube-dns"}}],
            "toPorts": [{"ports": [{"port": "53", "protocol": protocol} for protocol in ("UDP", "TCP")],
                         "rules": {"dns": [{"matchName": name} for name in names]}}]}


def policy(c):
    local_dns = ["postgres." + NS + ".svc.cluster.local"]
    github = ["github.com", "api.github.com"]
    api_egress = [endpoint("postgres", 5432), dns(local_dns + github),
                  {"toFQDNs": [{"matchName": name} for name in github], "toPorts": ports(443)}]
    traefik = {"fromEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system",
                         "k8s:app.kubernetes.io/name": "traefik"}}], "toPorts": ports(8080)}
    specs = []
    def add(app, ingress, egress):
        spec = {"endpointSelector": {"matchLabels": {"app": "trial-" + app}},
                "enableDefaultDeny": {"ingress": True, "egress": True}}
        # Empty allow lists are not valid direction rules in Cilium1.20.2.
        # A wholly closed direction gets an explicit deny, never an empty
        # allow object (which would allow every peer). Denies also take
        # precedence over additive allow policies on these trial endpoints.
        if ingress:
            spec["ingress"] = ingress
        else:
            spec["ingressDeny"] = [{"fromEntities": ["all"]}]
        if egress:
            spec["egress"] = egress
        else:
            spec["egressDeny"] = [{"toEntities": ["all"]}]
        specs.append(spec)
    runner = c.get("runner")
    api_ingress = [traefik]
    if runner:
        api_ingress.append({"fromEndpoints": [selection("runner")], "toPorts": ports(8080)})
        api_egress += [endpoint("runner", 8080)]
        api_egress[1] = dns(local_dns + github + ["runner." + NS + ".svc.cluster.local"])
    add("api", api_ingress, api_egress)
    add("web", [traefik], [])
    add("postgres", [{"fromEndpoints": [selection("api"), selection("ops")], "toPorts": ports(5432)}], [])
    add("ops", [], [endpoint("postgres", 5432), dns(local_dns)])
    # Also deny preparatory state-loader pods, then remove their policy only
    # after their termination. No hostPath or source mounts are generated.
    add("state-loader", [], [])
    runner_egress = []
    if runner:
        runner_egress = [endpoint("api", 8080), dns(["api." + NS + ".svc.cluster.local"] + runner["providerHosts"]),
                         {"toFQDNs": [{"matchName": name} for name in runner["providerHosts"]], "toPorts": ports(443)}]
    add("runner", [{"fromEndpoints": [selection("api")], "toPorts": ports(8080)}], runner_egress)
    return resource("CiliumNetworkPolicy", "trial-boundary", version="cilium.io/v2", specs=specs)


def pvc(name, size):
    return resource("PersistentVolumeClaim", name, {"accessModes": ["ReadWriteOnce"],
        "storageClassName": "codex-trial-retain", "resources": {"requests": {"storage": size}}})


def pod(app, container, uid=10001, extra_volumes=()):
    container.update(securityContext={"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                      "capabilities": {"drop": ["ALL"]}})
    container.setdefault("volumeMounts", []).append({"name": "tmp", "mountPath": "/tmp"})
    return {"metadata": {"labels": {"app": "trial-" + app}}, "spec": {
        "serviceAccountName": "trial", "automountServiceAccountToken": False,
        "dnsPolicy": "ClusterFirst", "dnsConfig": {"options": [{"name": "ndots", "value": "1"}]},
        "securityContext": {"runAsNonRoot": True, "runAsUser": uid, "runAsGroup": uid,
                            "fsGroup": uid, "seccompProfile": {"type": "RuntimeDefault"}},
        "terminationGracePeriodSeconds": 60,
        "containers": [container], "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "32Mi"}}, *extra_volumes]}}


def credential(name, secret, keys):
    return {"name": name, "secret": {"secretName": secret, "defaultMode": 0o440,
                                     "items": [{"key": key, "path": key} for key in keys]}}


def env_files(mapping, directory):
    return [{"name": name + "_FILE", "value": directory + "/" + filename} for name, filename in mapping.items()]


def limits(cpu, memory):
    return {"requests": {"cpu": "25m", "memory": "64Mi"}, "limits": {"cpu": cpu, "memory": memory}}


def service(app, port=8080, headless=False):
    return resource("Service", app, {"selector": {"app": "trial-" + app},
        **({"clusterIP": "None"} if headless else {}),
        "ports": [{"name": "http" if port == 8080 else "postgres", "port": port, "targetPort": port}]})


def deployment(app, template):
    return resource("Deployment", app, {"replicas": 1, "revisionHistoryLimit": 2,
        "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": "trial-" + app}},
        "template": template}, version="apps/v1")


def probe(path):
    return {"exec": {"command": ["python", "-m", "app.probe", path]}, "timeoutSeconds": 10,
            "periodSeconds": 10, "failureThreshold": 3}


def routes(c):
    host = "Host(`" + urlsplit(c["origin"]).hostname + "`)"
    api = "(Path(`/codex-trial/api`) || PathPrefix(`/codex-trial/api/`))"
    internal = "(Path(`/codex-trial/api/internal`) || PathPrefix(`/codex-trial/api/internal/`))"
    fixture = "(Path(`/codex-trial/_fixture`) || PathPrefix(`/codex-trial/_fixture/`))"
    boundary = "(Path(`/codex-trial`) || PathPrefix(`/codex-trial/`))"
    result = []
    for name, rule, priority in (("api", host + " && " + api + " && !" + internal, 120),
            ("web", host + " && " + boundary + " && !" + api + " && !" + fixture, 110)):
        result.append({"kind": "Rule", "match": rule, "priority": priority,
            "middlewares": [{"name": "trial-encoding"}, {"name": "trial-access"}],
            "services": [{"name": name, "port": 8080}]})
    return resource("IngressRoute", "trial", {"entryPoints": ["websecure"], "routes": result,
                    "tls": {"secretName": c["tlsSecret"]}}, version="traefik.io/v1alpha1")


def foundation(c=None):
    """Fixed prerequisite resources, no OAuth/access/credential inputs needed."""
    namespace = resource("Namespace", NS)
    namespace["metadata"] = {"name": NS, "labels": {"pod-security.kubernetes.io/enforce": "restricted",
                                                   "app.kubernetes.io/part-of": "codex-trial"}}
    storage = resource("StorageClass", "codex-trial-retain", version="storage.k8s.io/v1",
        provisioner="rancher.io/local-path", reclaimPolicy="Retain", volumeBindingMode="WaitForFirstConsumer",
        allowVolumeExpansion=False)
    del storage["metadata"]["namespace"]
    return [namespace, storage, resource("ServiceAccount", "trial", automountServiceAccountToken=False), policy(c or {})]


def render(c, part="review"):
    c = config(c)
    if part == "policy":
        return {"apiVersion": "v1", "kind": "List", "items": [policy(c)]}
    prerequisites = foundation(c)
    db = pod("postgres", {"name": "postgres", "image": POSTGRES,
        "args": ["-c", "shared_buffers=128MB", "-c", "max_connections=30"],
        "env": [{"name": "POSTGRES_DB", "value": "codex_trial"},
                {"name": "POSTGRES_PASSWORD_FILE", "value": "/run/secrets/password"}],
        "resources": limits("500m", "512Mi"),
        "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql"},
                         {"name": "credentials", "mountPath": "/run/secrets", "readOnly": True},
                         {"name": "run", "mountPath": "/var/run/postgresql"}],
        "readinessProbe": {"exec": {"command": ["pg_isready", "-U", "postgres", "-d", "codex_trial"]}, "periodSeconds": 5}},
        uid=999, extra_volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "trial-db"}},
        credential("credentials", "trial-db-admin", ["password"]), {"name": "run", "emptyDir": {"sizeLimit": "16Mi"}}])
    data = [pvc("trial-db", "5Gi"), resource("StatefulSet", "postgres", {"serviceName": "postgres", "replicas": 1,
        "selector": {"matchLabels": {"app": "trial-postgres"}}, "template": db}, version="apps/v1"), service("postgres", 5432, True)]
    ops = pod("ops", {"name": "migrate", "image": c["apiImage"],
        "command": ["python", "-m", "app.migrate", "--revision", "0004_publication"],
        "env": env_files({"ADMIN_DATABASE_URL": "admin-url", "API_DATABASE_PASSWORD": "api-password"}, "/run/ops"),
        "volumeMounts": [{"name": "credentials", "mountPath": "/run/ops", "readOnly": True}],
        "resources": limits("200m", "192Mi")}, extra_volumes=[credential("credentials", "trial-ops", ["admin-url", "api-password"])])
    ops["spec"]["restartPolicy"] = "Never"
    migration = resource("Job", "trial-migrate-0004", {"backoffLimit": 0, "activeDeadlineSeconds": 180,
                           "template": ops}, version="batch/v1")
    settings = {"APP_ENV": "personal-test", "APP_BASE_PATH": BASE, "AUTH_ORIGIN": c["origin"],
                "AUTH_TRUSTED_PROXY_CIDRS": ",".join(c["trustedProxyCidrs"]), "OAUTH_MODE": "github",
                "GITHUB_CLIENT_ID": c["clientId"], "RESEARCH_ACCESS_POLICY": "approved",
                "CODEX_PERSONAL_ENABLE": "false", "CODEX_RUNNERS_FILE": ""}
    runner = c.get("runner")
    api_volumes = [credential("api-input", "trial-api", ["database-url"]),
                   credential("github-input", "trial-github", ["client-secret", "transaction-key"])]
    api_mounts = [{"name": "api-input", "mountPath": "/run/api", "readOnly": True},
                  {"name": "github-input", "mountPath": "/run/github", "readOnly": True}]
    if runner:
        settings.update(CODEX_PERSONAL_ENABLE="true", CODEX_PERSONAL_ACCOUNT_ID=runner["owner"],
            CODEX_PERSONAL_REMOTE_ORIGIN=c["origin"], CODEX_EXECUTION_SCOPE="personal-private",
            CODEX_RUNNERS_FILE="/run/runner/runners.json")
        api_volumes.append(credential("runner-input", "trial-transport", ["runners.json", "transport-token"]))
        api_mounts.append({"name": "runner-input", "mountPath": "/run/runner", "readOnly": True})
    api = pod("api", {"name": "api", "image": c["apiImage"], "resources": limits("250m", "256Mi"),
        "envFrom": [{"configMapRef": {"name": "trial-config"}}],
        "env": env_files({"DATABASE_URL": "database-url"}, "/run/api") + env_files(
            {"GITHUB_CLIENT_SECRET": "client-secret", "AUTH_TRANSACTION_KEY": "transaction-key"}, "/run/github"),
        "command": ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080",
                    "--workers", "1", "--no-access-log", "--no-proxy-headers"],
        "volumeMounts": api_mounts, "readinessProbe": probe("/readyz"), "livenessProbe": probe("/healthz"),
        "startupProbe": {**probe("/healthz"), "failureThreshold": 20}}, extra_volumes=api_volumes)
    web_probe = {"exec": {"command": ["node", "-e", "fetch('http://127.0.0.1:8080/codex-trial/version.json',{signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]},
                 "timeoutSeconds": 5, "periodSeconds": 5, "failureThreshold": 3}
    web = pod("web", {"name": "web", "image": c["webImage"], "resources": limits("500m", "512Mi"),
        "env": [{"name": "PORT", "value": "8080"}, {"name": "HOSTNAME", "value": "0.0.0.0"}],
        "readinessProbe": web_probe, "startupProbe": {**web_probe, "failureThreshold": 30}})
    app = [resource("ConfigMap", "trial-config", data=settings), deployment("api", api), service("api"),
           deployment("web", web), service("web")]
    ingress = [resource("Middleware", "trial-encoding", {"encodedCharacters": ENCODING}, version="traefik.io/v1alpha1"),
        resource("Middleware", "trial-access", {"ipAllowList": {"sourceRange": c["clientCidrs"]}}, version="traefik.io/v1alpha1"), routes(c)]
    native = []
    if runner:
        native_pod = pod("runner", {"name": "runner", "image": runner["image"], "resources": limits("500m", "512Mi"),
            "env": [{"name": key, "value": value} for key, value in {
                "APP_ENV": "personal-test", "CODEX_AUTH_MODE": "personal-cache", "CODEX_EXECUTION_SCOPE": "personal-private",
                "CODEX_PERSONAL_CONTROL_DIR": "/control/private", "CODEX_MODEL": "gpt-6.1-sol",
                "RUNNER_ACCOUNT_ID": runner["owner"], "RUNNER_TOKEN_FILE": "/run/secrets/transport-token",
                "CODEX_BIN": "/usr/local/bin/codex", "RUNNER_STATE_DIR": "/native/private",
                "RESEARCH_TOOL_CALLBACK_URL": "http://api.codex-trial.svc.cluster.local:8080/codex-trial/api/internal/research/tools"}.items()],
            "volumeMounts": [{"name": "native", "mountPath": "/native"}, {"name": "control", "mountPath": "/control"},
                             {"name": "transport", "mountPath": "/run/secrets", "readOnly": True}]},
            extra_volumes=[{"name": name, "persistentVolumeClaim": {"claimName": "trial-" + name}} for name in ("native", "control")]
                + [credential("transport", "trial-transport", ["transport-token"])])
        native = [pvc("trial-native", "1Gi"), pvc("trial-control", "1Gi"), deployment("runner", native_pod), service("runner")]
    groups = dict(foundation=prerequisites, data=data, migrate=[migration], app=app, ingress=ingress, runner=native)
    access_probe = resource("IngressRoute", "trial-access-probe", {"entryPoints": ["websecure"],
        "tls": {"secretName": c["tlsSecret"]}, "routes": [{"kind": "Rule", "priority": 130,
            "match": "Host(`" + urlsplit(c["origin"]).hostname + "`) && Path(`/codex-trial/version.json`)",
            "middlewares": [{"name": "trial-encoding"}, {"name": "trial-access"}],
            "services": [{"name": "web", "port": 8080}]}]}, version="traefik.io/v1alpha1")
    require(part == "review" or part in PARTS)
    if part == "web":
        return {"apiVersion": "v1", "kind": "List", "items": app[-2:]}
    if part == "access-probe":
        return {"apiVersion": "v1", "kind": "List", "items": [*ingress[:-1], access_probe]}
    return {"apiVersion": "v1", "kind": "List", "items": [item for group in groups.values() for item in group]
            + [access_probe] if part == "review" else groups[part]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?")
    parser.add_argument("--part", choices=("review", *PARTS), default="review")
    args = parser.parse_args()
    try:
        if args.input is None:
            require(args.part in {"foundation", "policy"})
            output = {"apiVersion": "v1", "kind": "List", "items": foundation() if args.part == "foundation" else [policy({})]}
        else:
            with open(args.input) as source:
                output = render(json.load(source), args.part)
        print(json.dumps(output, indent=2))
    except Exception:
        print("Trial render refused; input values are not printed", file=sys.stderr)
        sys.exit(1)
