"""Emit guarded trial-only egress patches from private Kubernetes snapshots.

Offline only. Run in Docker. Never apply the inspection List directly.
"""
import argparse
from copy import deepcopy
import difflib
import json
from pathlib import Path

NS = "codex-trial"
KEY = "k8s:io.kubernetes.pod.namespace"
NAME = "trial-namespace-egress-v1"
CNP_NAMES = {"access-state-diagnose-deny-v1", "access-web-boundary-v1",
             "access-web-state-init-deny-v1", "trial-boundary"}
NODE = "anxious-lookout-node-protection"
TENANT = "anxious-lookout-tenant-boundary"


def require(value):
    if not value:
        raise ValueError("Namespace egress snapshot shape or ownership refused")


def namespace_policy():
    return {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
            "metadata": {"name": NAME, "namespace": NS}, "spec": {
                "endpointSelector": {},
                "enableDefaultDeny": {"ingress": False, "egress": True},
                "egress": [{"toEntities": ["all"]}],
                "egressDeny": [{"toEndpoints": [{"matchLabels": {KEY: NS}}]}]}}


def fields(obj):
    result = {key: deepcopy(obj[key]) for key in ("spec", "specs") if key in obj}
    require(len(result) == 1)
    require(isinstance(result.get("spec", {}), dict))
    require(isinstance(result.get("specs", []), list))
    return result


def rules(obj):
    fields(obj)
    result = obj.get("specs", [obj.get("spec")])
    require(result and all(isinstance(rule, dict) and "endpointSelector" in rule for rule in result))
    return result


def patches(before, after):
    require(before["metadata"].get("uid") and before["metadata"].get("resourceVersion"))
    previous, desired = fields(before), fields(after)
    require(previous.keys() == desired.keys())
    def patch(expected, replacement):
        return [{"op": "test", "path": "/metadata/uid", "value": before["metadata"]["uid"]},
                *[{"op": "test", "path": "/" + key, "value": value} for key, value in expected.items()],
                *[{"op": "replace", "path": "/" + key, "value": value} for key, value in replacement.items()]]
    # Status updates may change RV. UID + entire spec tests reject spec/ownership
    # drift while allowing harmless status changes; rollback is equally guarded.
    return patch(previous, desired), patch(desired, previous)


def plan(cnp, ccnp, namespace, np):
    require(namespace.get("kind") == "Namespace" and namespace["metadata"]["name"] == NS)
    require(namespace["metadata"].get("labels", {}).get("anxious-lookout.io/tenant") != "true")
    require(np.get("items") == [])  # Current reviewed baseline has no KNPs.
    local = cnp.get("items", [])
    require(len(local) == len(CNP_NAMES) and {x["metadata"]["name"] for x in local} == CNP_NAMES)
    require(all(x.get("kind") == "CiliumNetworkPolicy" and
                x["metadata"].get("namespace") == NS for x in local))
    cluster = ccnp.get("items", [])
    require(len(cluster) == 2 and {x["metadata"]["name"] for x in cluster} == {NODE, TENANT})
    require(all(x.get("kind") == "CiliumClusterwideNetworkPolicy" for x in cluster))
    tenant = next(x for x in cluster if x["metadata"]["name"] == TENANT)
    require(fields(tenant).keys() == {"spec"})
    require(tenant["spec"]["endpointSelector"] == {"matchLabels": {
        "k8s:io.cilium.k8s.namespace.labels.anxious-lookout.io/tenant": "true"}})
    changed = []
    for before in sorted(local, key=lambda x: x["metadata"]["name"]):
        after = deepcopy(before)
        for rule in rules(after):
            require("nodeSelector" not in rule)
            rule.pop("egressDeny", None)
            rule["egress"] = [{"toEntities": ["all"]}]
            # Leave enableDefaultDeny and every ingress field untouched.
        changed.append((before, after))
    before = next(x for x in cluster if x["metadata"]["name"] == NODE)
    require(fields(before).keys() == {"spec"})
    require(not any(key in before["spec"] for key in ("ingress", "ingressDeny", "nodeSelector")))
    require(before["spec"].get("enableDefaultDeny") == {"ingress": False, "egress": False})
    require(before["spec"]["endpointSelector"] == {"matchExpressions": [
        {"key": KEY, "operator": "Exists"}, {"key": KEY, "operator": "NotIn", "values": ["kube-system"]}]})
    after = deepcopy(before)
    after["spec"]["endpointSelector"]["matchExpressions"][1]["values"].append(NS)
    changed.append((before, after))
    for original, desired in changed:
        patches(original, desired)  # Validate all before writing any outputs.
    return changed


def emit(source, output):
    read = lambda name: json.loads((source / name).read_text())
    changed = plan(read("cnp.json"), read("ccnp.json"), read("namespace.json"), read("np.json"))
    output.mkdir(mode=0o700, exist_ok=False)
    def write(name, value):
        with (output / name).open("x") as handle:
            (output / name).chmod(0o600)
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    write("namespace-policy.json", namespace_policy())
    cases = [{"name": "namespace-egress-all-except-self", "rules": [namespace_policy()["spec"]], "expectedError": ""}]
    diff = []
    for before, after in changed:
        name = before["metadata"]["name"]
        forward, backward = patches(before, after)
        write(name + ".forward.json", forward)
        write(name + ".rollback.json", backward)
        write(name + ".before.json", before)
        write(name + ".after.json", after)
        cases.append({"name": name, "rules": rules(after), "expectedError": ""})
        diff.extend(difflib.unified_diff(json.dumps(fields(before), indent=2, sort_keys=True).splitlines(True),
            json.dumps(fields(after), indent=2, sort_keys=True).splitlines(True), fromfile=name + ":before", tofile=name + ":trial"))
    write("cilium-cases.json", cases)
    with (output / "policy.diff").open("x") as handle:
        (output / "policy.diff").chmod(0o600)
        handle.writelines(diff)
    print(json.dumps({"status": "emitted", "changedPolicies": len(changed), "newPolicies": 1,
                      "tenantChanged": False, "clusterWrites": False}))


def verify(source, current):
    read = lambda directory, name: json.loads((directory / name).read_text())
    old = [read(source, name) for name in ("cnp.json", "ccnp.json", "namespace.json", "np.json")]
    fresh = [read(current, name) for name in ("cnp.json", "ccnp.json", "namespace.json", "np.json")]
    plan(*old)
    plan(*fresh)
    for previous, actual in zip(old[:2], fresh[:2]):
        by_name = lambda obj: {x["metadata"]["name"]: x for x in obj["items"]}
        for name, obj in by_name(previous).items():
            new = by_name(actual)[name]
            require(obj["metadata"].get("uid") == new["metadata"].get("uid"))
            require(fields(obj) == fields(new))
    require(old[2]["metadata"].get("uid") == fresh[2]["metadata"].get("uid"))
    require(old[2]["metadata"].get("labels") == fresh[2]["metadata"].get("labels"))
    print(json.dumps({"status": "baseline-matches", "clusterWrites": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--verify", action="store_true", help="Compare source with fresh snapshot directory")
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.source, args.output)
        else:
            emit(args.source, args.output)
    except Exception:
        parser.exit(1, "Namespace egress emit refused; private input values omitted\n")
