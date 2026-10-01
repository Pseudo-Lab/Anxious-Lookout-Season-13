#!/usr/bin/env python3
"""Reconcile exact trusted kube-system Pod IPv4s; never grant a whole Pod CIDR.

Run as root by the supplied timer. The operator must create both hash:ip sets
and their host-input rules first. A Kubernetes/API error performs NO firewall
mutation. No tenant labels, caller IPs, or external input can expand the allowlist.
"""
import ipaddress
import json
import subprocess
import sys

API_ACCOUNTS = frozenset({
    "coredns", "metrics-server", "local-path-provisioner-service-account",
    "traefik", "helm-traefik", "helm-traefik-crd",
})
METRICS_ACCOUNTS = frozenset({"metrics-server"})
POD_NETWORK = ipaddress.ip_network("10.42.0.0/16")
SETS = {
    "anxious-k3s-api-clients": API_ACCOUNTS,
    "anxious-k3s-metrics-clients": METRICS_ACCOUNTS,
}


def command(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=20, check=False)
    if result.returncode:
        # Command stderr could include server response details: keep logs minimal.
        raise RuntimeError("command failed: " + " ".join(args[:2]))
    return result.stdout


def firewall(name, permanent, *args):
    flags = ["--permanent"] if permanent else []
    return command(["/usr/bin/firewall-cmd"] + flags + ["--ipset=" + name] + list(args))


def desired_sets(snapshot):
    if snapshot.get("kind") not in ("PodList", "List") or not isinstance(snapshot.get("items"), list):
        raise ValueError("unexpected Kubernetes PodList response")
    desired = {name: set() for name in SETS}
    for pod in snapshot["items"]:
        if pod.get("kind", "Pod" if snapshot["kind"] == "PodList" else None) != "Pod":
            raise ValueError("unexpected resource inside Kubernetes Pod list")
        meta, spec, status = pod["metadata"], pod["spec"], pod.get("status", {})
        if meta.get("namespace") != "kube-system" or spec.get("hostNetwork", False):
            continue
        if meta.get("deletionTimestamp") or status.get("phase") not in ("Pending", "Running"):
            continue
        raw_ip = status.get("podIP")
        if not raw_ip:
            continue
        address = ipaddress.ip_address(raw_ip)
        if address.version != 4 or address not in POD_NETWORK:
            continue
        account = spec.get("serviceAccountName", "default")
        for name, allowed_accounts in SETS.items():
            if account in allowed_accounts:
                desired[name].add(str(address))
    return desired


def reconcile():
    guard = json.loads(command(["/usr/local/bin/k3s", "kubectl", "--request-timeout=15s",
                                "get", "ccnp", "anxious-lookout-node-protection", "-o", "json"]))
    spec = guard.get("spec", {})
    expressions = spec.get("endpointSelector", {}).get("matchExpressions", [])
    namespace_key = "k8s:io.kubernetes.pod.namespace"
    if len(expressions) != 2 or spec.get("endpointSelector", {}).get("matchLabels"):
        raise ValueError("node-protection guard has unexpected narrowing selectors")
    if not any(x.get("key") == namespace_key and x.get("operator") == "Exists" for x in expressions):
        raise ValueError("node-protection guard missing namespace existence selector")
    if not any(x.get("key") == namespace_key and x.get("operator") == "NotIn"
               and x.get("values") == ["kube-system"] for x in expressions):
        raise ValueError("node-protection guard missing namespace exclusion")
    denied = {entity for rule in spec.get("egressDeny", []) if set(rule) == {"toEntities"} for entity in rule.get("toEntities", [])}
    if not {"host", "remote-node", "kube-apiserver"}.issubset(denied):
        raise ValueError("node-protection guard does not deny node/API identities")

    snapshot = json.loads(command([
        "/usr/local/bin/k3s", "kubectl", "--request-timeout=15s",
        "get", "pods", "-n", "kube-system", "-o", "json",
    ]))
    desired = desired_sets(snapshot)
    # Read/validate all four views before changing anything. Entries must be
    # single IPv4s inside the Pod pool; broad CIDRs are treated as misconfiguration.
    current = {}
    for name in SETS:
        for permanent in (False, True):
            entries = set(firewall(name, permanent, "--get-entries").split())
            for entry in entries:
                address = ipaddress.ip_address(entry)
                if address.version != 4 or address not in POD_NETWORK:
                    raise ValueError("unexpected existing ipset entry")
            current[name, permanent] = entries
    changes = 0
    # Remove all stale addresses before adding any new address, in both views.
    for name in SETS:
        for permanent in (False, True):
            for entry in sorted(current[name, permanent] - desired[name]):
                firewall(name, permanent, "--remove-entry=" + entry)
                changes += 1
    for name in SETS:
        for permanent in (True, False):
            for entry in sorted(desired[name] - current[name, permanent]):
                firewall(name, permanent, "--add-entry=" + entry)
                changes += 1
    if changes:
        print("Reconciled trusted system Pod IP sets: {} changes".format(changes))


if __name__ == "__main__":
    try:
        reconcile()
    except Exception as exc:
        print("Firewall synchronization failed: {}".format(type(exc).__name__), file=sys.stderr)
        sys.exit(1)
