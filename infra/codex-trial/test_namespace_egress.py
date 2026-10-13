from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from namespace_egress import CNP_NAMES, KEY, NODE, NS, TENANT, emit, namespace_policy, patches, plan, rules, verify
from namespace_egress_probe import emit as probe_emit
from namespace_egress_probe import service_guard
from namespace_egress_probe import api_ingress_guard
from namespace_egress_probe import isolation_guard
from render import policy


def snapshot():
    local = []
    for name in sorted(CNP_NAMES):
        obj = policy({}) if name == "trial-boundary" else {
            "kind": "CiliumNetworkPolicy", "metadata": {"namespace": NS},
            "spec": {"endpointSelector": {"matchLabels": {"app": name}},
                     "ingressDeny": [{"fromEntities": ["all"]}], "egressDeny": [{"toEntities": ["all"]}]}}
        obj["metadata"].update(name=name, uid="uid-" + name, resourceVersion="1")
        local.append(obj)
    def cluster(name, spec):
        return {"kind": "CiliumClusterwideNetworkPolicy", "metadata": {
            "name": name, "uid": "uid-" + name, "resourceVersion": "1"}, "spec": spec}
    node = cluster(NODE, {"endpointSelector": {"matchExpressions": [
        {"key": KEY, "operator": "Exists"}, {"key": KEY, "operator": "NotIn", "values": ["kube-system"]}]},
        "enableDefaultDeny": {"ingress": False, "egress": False},
        "egressDeny": [{"toEntities": ["host", "remote-node", "kube-apiserver"]},
                       {"toCIDR": ["192.0.2.1/32"]}]})
    tenant = cluster(TENANT, {"endpointSelector": {"matchLabels": {
        "k8s:io.cilium.k8s.namespace.labels.anxious-lookout.io/tenant": "true"}},
        "ingress": [{"fromEntities": ["cluster"]}], "egressDeny": [{"toEntities": ["host"]}]})
    return {"items": local}, {"items": [node, tenant]}, {
        "kind": "Namespace", "metadata": {"name": NS, "labels": {}}}, {"items": []}


def apply_patch(obj, patch):
    result = deepcopy(obj)
    for step in patch:
        parts = step["path"].strip("/").split("/")
        parent = result
        for part in parts[:-1]:
            parent = parent[part]
        if step["op"] == "test":
            if parent.get(parts[-1]) != step["value"]:
                raise ValueError("test failed")
        elif step["op"] == "replace":
            parent[parts[-1]] = deepcopy(step["value"])
        else:
            raise AssertionError("unexpected operation")
    return result


def test_ingress_and_non_egress_fields_preserved_and_exact_rollback():
    inputs = snapshot()
    baseline = deepcopy(inputs)
    changes = plan(*inputs)
    assert inputs == baseline and len(changes) == 5
    assert all(before["metadata"]["name"] != TENANT for before, _ in changes)
    for before, after in changes:
        forward, rollback = patches(before, after)
        assert apply_patch(before, forward) == after
        assert apply_patch(after, rollback) == before
        assert before["metadata"] == after["metadata"]
        if before["kind"] == "CiliumNetworkPolicy":
            for old, new in zip(rules(before), rules(after)):
                assert {k: v for k, v in old.items() if k not in {"egress", "egressDeny"}} == {
                    k: v for k, v in new.items() if k not in {"egress", "egressDeny"}}
                assert new["egress"] == [{"toEntities": ["all"]}] and "egressDeny" not in new
        else:
            assert before["spec"]["egressDeny"] == after["spec"]["egressDeny"]


@pytest.mark.parametrize("namespace,selected", [(NS, False), ("kube-system", False),
                                                ("m2-hosting", True), ("other", True)])
def test_node_protection_exclusion_only_changes_trial(namespace, selected):
    node = plan(*snapshot())[-1][1]
    expressions = node["spec"]["endpointSelector"]["matchExpressions"]
    assert (namespace not in expressions[1]["values"]) == selected
    assert expressions[0] == {"key": KEY, "operator": "Exists"}


def test_namespace_policy_deny_has_no_role_port_or_dns_exception():
    spec = namespace_policy()["spec"]
    assert spec == {"endpointSelector": {}, "enableDefaultDeny": {"ingress": False, "egress": True},
        "egress": [{"toEntities": ["all"]}],
        "egressDeny": [{"toEndpoints": [{"matchLabels": {KEY: NS}}]}]}


@pytest.mark.parametrize("direction", ["forward", "rollback"])
@pytest.mark.parametrize("drift", ["uid", "spec", "ingress"])
def test_patch_rejects_concurrent_replacement_or_spec_drift(direction, drift):
    before, after = plan(*snapshot())[0]
    forward, backward = patches(before, after)
    obj, patch = (before, forward) if direction == "forward" else (after, backward)
    obj = deepcopy(obj)
    if drift == "uid":
        obj["metadata"]["uid"] = "replacement"
    else:
        obj["spec"]["ingress"] = [{"fromEntities": ["all"]}]
    with pytest.raises(ValueError):
        apply_patch(obj, patch)


@pytest.mark.parametrize("drift", ["cnp-extra", "ccnp-extra", "np", "tenant-label", "tenant-selector", "node-selector", "node-ingress"])
def test_unknown_baseline_refused(drift):
    cnp, ccnp, ns, np = snapshot()
    if drift == "cnp-extra":
        cnp["items"].append(deepcopy(cnp["items"][0]))
    elif drift == "ccnp-extra":
        ccnp["items"].append(deepcopy(ccnp["items"][0]))
    elif drift == "np":
        np["items"] = [{"kind": "NetworkPolicy"}]
    elif drift == "tenant-label":
        ns["metadata"]["labels"]["anxious-lookout.io/tenant"] = "true"
    elif drift == "tenant-selector":
        ccnp["items"][1]["spec"]["endpointSelector"] = {}
    elif drift == "node-selector":
        ccnp["items"][0]["spec"]["endpointSelector"] = {}
    else:
        ccnp["items"][0]["spec"]["ingress"] = [{"fromEntities": ["all"]}]
    with pytest.raises(ValueError):
        plan(cnp, ccnp, ns, np)


def test_private_emit_is_non_overwriting_and_contains_semantic_cases(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for filename, obj in zip(("cnp.json", "ccnp.json", "namespace.json", "np.json"), snapshot()):
        (source / filename).write_text(json.dumps(obj))
    output = tmp_path / "output"
    emit(source, output)
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
    assert len(json.loads((output / "cilium-cases.json").read_text())) == 6
    with pytest.raises(FileExistsError):
        emit(source, output)


@pytest.mark.parametrize("drift", ["none", "status", "uid", "ingress", "namespace"])
def test_fresh_guard_rejects_policy_and_namespace_drift(tmp_path, drift):
    before = snapshot()
    fresh = deepcopy(before)
    if drift == "status":
        fresh[0]["items"][0]["metadata"]["resourceVersion"] = "2"
        fresh[0]["items"][0]["status"] = {"valid": True}
    elif drift == "uid":
        fresh[0]["items"][0]["metadata"]["uid"] = "new-owner"
    elif drift == "ingress":
        fresh[0]["items"][0]["spec"]["ingress"] = [{"fromEntities": ["all"]}]
    elif drift == "namespace":
        fresh[2]["metadata"]["labels"]["unrelated-label"] = "changed"
    for directory, objects in (("before", before), ("fresh", fresh)):
        target = tmp_path / directory
        target.mkdir()
        for name, obj in zip(("cnp.json", "ccnp.json", "namespace.json", "np.json"), objects):
            (target / name).write_text(json.dumps(obj))
    if drift in {"none", "status"}:
        verify(tmp_path / "before", tmp_path / "fresh")
    else:
        with pytest.raises(ValueError):
            verify(tmp_path / "before", tmp_path / "fresh")


def service_snapshot():
    return {"kind": "List", "items": [{"kind": "Service", "metadata": {
        "name": "access-web-runner-v1", "namespace": NS}, "spec": {
        "selector": {"app": "access-web-runner"}}}]}


def hosting_snapshot():
    expected = {"fromEndpoints": [{"matchLabels": {KEY: NS, "k8s:app": "access-web-runner"}}],
        "toPorts": [{"ports": [{"port": "8080", "protocol": "TCP"}]}]}
    def obj(name, spec):
        return {"kind": "CiliumNetworkPolicy", "metadata": {"name": name, "namespace": "m2-hosting"}, "spec": spec}
    return {"kind": "List", "items": [obj("hosting-access-web-link-v1", {
        "endpointSelector": {"matchLabels": {"app": "hosting-api"}}, "ingress": [expected]}),
        obj("hosting-boundary", {"endpointSelector": {"matchLabels": {"app": "hosting-api"}},
            "enableDefaultDeny": {"ingress": True, "egress": True}, "ingress": [{
                "fromEndpoints": [{"matchLabels": {KEY: "kube-system", "k8s:app.kubernetes.io/name": "traefik"}}],
                "toPorts": [{"ports": [{"port": "8080", "protocol": "TCP"}]}]}]})]}


def test_probe_artifacts_have_no_credentials_native_or_controller():
    hosting = hosting_snapshot()
    before = deepcopy(hosting)
    items = probe_emit("example.invalid/api@sha256:" + "a" * 64, service_snapshot(), hosting)["items"]
    assert hosting == before
    assert [i["kind"] for i in items] == ["ConfigMap", "Pod", "Pod", "Service"]
    for obj in items:
        assert obj["metadata"]["namespace"] == NS
        if obj["kind"] == "Pod":
            spec = obj["spec"]
            assert spec["automountServiceAccountToken"] is False
            assert spec["restartPolicy"] == "Never" and spec["activeDeadlineSeconds"] == 300
            assert not any("secret" in v or "persistentVolumeClaim" in v or "hostPath" in v for v in spec["volumes"])
            assert spec["containers"][0]["command"][-1] == "serve"
            if obj["metadata"]["name"] == "trial-egress-source-v1":
                assert obj["metadata"]["labels"]["app"] == "access-web-runner"
                source = {KEY: NS, "k8s:app": obj["metadata"]["labels"]["app"]}
                selector = hosting["items"][0]["spec"]["ingress"][0]["fromEndpoints"][0]["matchLabels"]
                assert all(source.get(key) == value for key, value in selector.items())
                readiness = spec["containers"][0]["readinessProbe"]["exec"]["command"]
                result = subprocess.run(readiness, capture_output=True)
                assert result.returncode == 1
            else:
                assert obj["metadata"]["labels"]["app"] == "trial-namespace-egress-probe"


@pytest.mark.parametrize("drift", ["runner-publishes", "other-publishes", "runner-missing", "selector"])
def test_service_guard_rejects_unready_canary_exposure(drift):
    values = service_snapshot()
    runner = values["items"][0]
    if drift == "runner-publishes":
        runner["spec"]["publishNotReadyAddresses"] = True
    elif drift == "other-publishes":
        other = deepcopy(runner)
        other["metadata"]["name"] = "other"
        other["spec"]["publishNotReadyAddresses"] = True
        values["items"].append(other)
    elif drift == "runner-missing":
        values["items"] = []
    else:
        runner["spec"]["selector"] = {}
    with pytest.raises(ValueError):
        service_guard(values)


def test_service_guard_allows_unrelated_published_peer_service():
    values = service_snapshot()
    values["items"].append({"kind": "Service", "metadata": {"name": "peer", "namespace": NS},
        "spec": {"selector": {"app": "different"}, "publishNotReadyAddresses": True}})
    service_guard(values)


@pytest.mark.parametrize("drift", ["source-label", "port", "deny", "unknown-policy"])
def test_api_ingress_guard_refuses_unusable_runner_path(drift):
    values = hosting_snapshot()
    bridge, boundary = values["items"]
    if drift == "source-label":
        bridge["spec"]["ingress"][0]["fromEndpoints"][0]["matchLabels"]["k8s:app"] = "other"
    elif drift == "port":
        bridge["spec"]["ingress"][0]["toPorts"][0]["ports"][0]["port"] = "18080"
    elif drift == "deny":
        boundary["spec"]["ingressDeny"] = [{"fromEntities": ["all"]}]
    else:
        values["items"].append(deepcopy(boundary))
    with pytest.raises(ValueError):
        api_ingress_guard(values)


@pytest.mark.parametrize("drift", ["none", "not-listed", "pod-ready", "endpoint-ready", "endpoint-unknown",
    "endpoint-serving", "publisher-change", "uid", "missing-reference-ready", "other-reference-ready",
    "missing-reference-unready", "other-reference-unready", "uid-other-ip", "uid-extra-ip",
    "uid-no-address", "pod-ip-mismatch", "pod-ips-missing", "family-mismatch", "unknown-family",
    "other-reference-name", "other-reference-namespace", "other-reference-kind", "dual-stack",
    "ipv6-missing-reference-ready", "unrelated-endpoint"])
def test_live_canary_isolation_guard(drift):
    resources = service_snapshot()
    source = probe_emit("example.invalid/api@sha256:" + "a" * 64, resources, hosting_snapshot())["items"][1]
    source["metadata"]["uid"] = "owned-source"
    source["status"] = {"phase": "Running", "podIP": "10.77.1.123", "podIPs": [{"ip": "10.77.1.123"}],
        "conditions": [{"type": "Ready", "status": "False"}],
        "containerStatuses": [{"ready": False, "state": {"running": {}}}]}
    resources["items"].append(source)
    slices = {"kind": "EndpointSliceList", "items": [{"addressType": "IPv4", "metadata": {
        "namespace": NS, "labels": {"kubernetes.io/service-name": "access-web-runner-v1"}},
        "endpoints": [{"addresses": ["10.77.1.123"], "targetRef": {"uid": "owned-source", "kind": "Pod",
            "namespace": NS, "name": "trial-egress-source-v1"}, "conditions": {"ready": False, "serving": False}}]}]}
    endpoint = slices["items"][0]["endpoints"][0]
    if drift == "not-listed":
        slices["items"] = []
    elif drift == "pod-ready":
        source["status"]["conditions"][0]["status"] = "True"
    elif drift == "endpoint-ready":
        endpoint["conditions"]["ready"] = True
    elif drift == "endpoint-unknown":
        del endpoint["conditions"]["ready"]
    elif drift == "endpoint-serving":
        endpoint["conditions"]["serving"] = True
    elif drift == "publisher-change":
        resources["items"][0]["spec"]["publishNotReadyAddresses"] = True
    elif drift == "uid":
        source["metadata"]["uid"] = "replaced"
    elif drift in {"missing-reference-ready", "other-reference-ready", "missing-reference-unready", "other-reference-unready"}:
        endpoint["targetRef"] = {} if drift.startswith("missing-reference") else {"uid": "stale-other-source"}
        if drift.endswith("-ready"):
            endpoint["conditions"] = {"ready": True, "serving": True}
    elif drift == "uid-other-ip":
        endpoint["addresses"] = ["10.77.1.124"]
    elif drift == "uid-extra-ip":
        endpoint["addresses"].append("10.77.1.124")
    elif drift == "uid-no-address":
        del endpoint["addresses"]
    elif drift == "pod-ip-mismatch":
        source["status"]["podIP"] = "10.77.1.124"
    elif drift == "pod-ips-missing":
        del source["status"]["podIPs"]
    elif drift == "family-mismatch":
        slices["items"][0]["addressType"] = "IPv6"
    elif drift == "unknown-family":
        slices["items"][0]["addressType"] = "FQDN"
    elif drift in {"other-reference-name", "other-reference-namespace", "other-reference-kind"}:
        endpoint["targetRef"][drift.split("-")[-1]] = "other"
    elif drift in {"dual-stack", "ipv6-missing-reference-ready"}:
        source["status"]["podIPs"].append({"ip": "fd00::123"})
        ipv6 = deepcopy(slices["items"][0])
        ipv6["addressType"] = "IPv6"
        ipv6["endpoints"][0]["addresses"] = ["fd00::123"]
        if drift == "ipv6-missing-reference-ready":
            ipv6["endpoints"][0]["targetRef"] = {}
            ipv6["endpoints"][0]["conditions"] = {"ready": True, "serving": True}
        slices["items"].append(ipv6)
    elif drift == "unrelated-endpoint":
        endpoint["addresses"] = ["10.77.1.124"]
        endpoint["targetRef"] = {"uid": "unrelated"}
        endpoint["conditions"] = {"ready": True, "serving": True}
    if drift in {"none", "not-listed", "dual-stack", "unrelated-endpoint"}:
        isolation_guard(resources, slices, "owned-source")
    else:
        with pytest.raises(ValueError):
            isolation_guard(resources, slices, "owned-source")


@pytest.mark.parametrize("kind,name,accepted", [("pods", "trial-egress-source-v1", True),
    ("ciliumnetworkpolicies", "trial-namespace-egress-v1", True),
    ("pods", "access-web-state-init-v1", False), ("persistentvolumeclaims", "trial-native", False),
    ("ciliumclusterwidenetworkpolicies", NODE, False)])
def test_delete_helper_uid_preconditions_and_resource_boundaries(tmp_path, kind, name, accepted):
    stub = tmp_path / "timeout"
    # Stub only the outer timeout/sudo transport inside this offline Docker test.
    stub.write_text('#!/bin/sh\ncat > "$DELETE_TEST_BODY"\nprintf "%s\\n" "$@" > "$DELETE_TEST_ARGV"\n')
    stub.chmod(0o700)
    body, argv = tmp_path / "body", tmp_path / "argv"
    uid = "00000000-0000-4000-8000-000000000007"
    env = {**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"],
           "DELETE_TEST_BODY": str(body), "DELETE_TEST_ARGV": str(argv)}
    result = subprocess.run(["bash", str(Path(__file__).with_name("delete_namespace_egress.sh")),
        kind, name, uid], env=env, capture_output=True)
    assert (result.returncode == 0) == accepted
    if accepted:
        assert json.loads(body.read_text())["preconditions"] == {"uid": uid}
        arguments = argv.read_text().splitlines()
        assert "/usr/local/bin/k3s" in arguments and "--raw" in arguments
        assert any(value.endswith("/namespaces/codex-trial/" + kind + "/" + name) for value in arguments)
    else:
        assert not body.exists()
