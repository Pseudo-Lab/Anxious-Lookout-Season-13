"""#90 P5 r6 diagnosis (infra/hosting/m3/chat-stream/vm/i90-p5-hop2-look.py): read only, fixed keys. Pure fakes."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "hosting" / "m3" / "chat-stream" / "vm" / "i90-p5-hop2-look.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="infra/ not mounted")
RUN = "i90-p5-turns-r6-20261114T172503Z"
TRAEFIK = {"k8s:io.kubernetes.pod.namespace": "kube-system", "k8s:app.kubernetes.io/name": "traefik"}
RUNNER = {"k8s:io.kubernetes.pod.namespace": "codex-runner", "k8s:anxious.io/managed": "user-runner"}
API_LABELS = ["k8s:app=hosting-api", "k8s:io.kubernetes.pod.namespace=m2-hosting",
              "k8s:io.cilium.k8s.policy.serviceaccount=default", "k8s:SECRET-label=x"]


@pytest.fixture
def h(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("i90_p5_hop2_look", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "EVIDENCE", str(tmp_path))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    return module


def repo_cnp():
    """hosting-api-ingress as infra/hosting/render.sh renders it (no transition rule)."""
    return {"metadata": {"name": "hosting-api-ingress"}, "spec": {
        "endpointSelector": {"matchLabels": {"app": "hosting-api"}},
        "ingress": [{"fromEndpoints": [{"matchLabels": TRAEFIK}], "toPorts": [{"ports": [{"port": "8080", "protocol": "TCP"}]}]},
                    {"fromEndpoints": [{"matchLabels": RUNNER}], "toPorts": [{"ports": [{"port": "8081", "protocol": "TCP"}]}]}]}}


def cluster(cnps=None, ccnps=(), ceps=None, config=None, fail=()):
    tables = {
        "configmap": {"data": config if config is not None else {"allow-localhost": "policy", "kube-proxy-replacement": "false"}},
        "ciliumnetworkpolicies": {"items": list(cnps if cnps is not None else [repo_cnp()])},
        "ciliumclusterwidenetworkpolicies": {"items": list(ccnps)},
        "ciliumendpoints": {"items": list(ceps if ceps is not None else [
            {"metadata": {"name": "SECRET-pod"}, "status": {"identity": {"id": 12345, "labels": API_LABELS},
                                                             "policy": {"ingress": {"enforcing": True, "state": "enforcing"}}}},
            {"metadata": {"name": "web"}, "status": {"identity": {"id": 7, "labels": ["k8s:app=hosting-web"]}}}])},
    }

    def run(args, **kwargs):
        key = next(k for k in tables if k in args)

        class Done:
            returncode = 1 if key in fail else 0
            stdout = json.dumps(tables[key]).encode()
        return Done()
    return run


def client_file(tmp_path, lines, run=RUN, hop3=False):
    folder = tmp_path / run
    folder.mkdir()
    (folder / "client-S4a-hop2.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    if hop3:
        (folder / "client-S4a-hop3.jsonl").write_text("")
    return folder


def outputs(raw):
    return {x["kind"]: x for x in (json.loads(y) for y in raw.splitlines())}


def test_repo_policy_and_connect_timeout_show_no_host_rule(h, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(h.subprocess, "run", cluster())
    client_file(tmp_path, [{"kind": "end", "reason": "probe_error", "errorType": "TimeoutError", "elapsedMs": 5003.4,
                            "unixNs": 1}])
    assert h.main() == 0
    raw = capsys.readouterr().out
    assert "SECRET" not in raw and "12345" in raw
    out = outputs(raw)
    assert out["client"] == {"kind": "client", "run": RUN, "hop3FileForS4a": False, "hop2": {
        "counts": {"end": 1}, "unparsed": 0, "headers": False,
        "ends": [{"reason": "probe_error", "errorType": "TimeoutError", "elapsedMs": 5003}]}}
    assert out["cilium"] == {"kind": "cilium", "allowLocalhost": "policy", "kubeProxyReplacement": "false"}
    assert out["endpoint"]["count"] == 1
    assert out["endpoint"]["items"] == [{"identity": 12345, "enforcing": True, "state": "enforcing"}]
    assert out["policies"]["items"] == [{"kind": "CNP", "name": "hosting-api-ingress", "selectsApi": True,
                                         "nodeSelector": False, "enableDefaultDeny": "missing", "rules": [
        {"direction": "ingress", "from": ["endpoints:traefik"], "ports": [8080]},
        {"direction": "ingress", "from": ["endpoints:user-runner"], "ports": [8081]}]}]
    assert out["hostTo8080"] == {"kind": "hostTo8080", "admitted": False, "rules": []}


@pytest.mark.parametrize("rule, admitted", [
    ({"fromEntities": ["host"], "toPorts": [{"ports": [{"port": "8080"}]}]}, True),
    ({"fromEntities": ["all"]}, True),
    ({"toPorts": [{"ports": [{"port": "8080"}]}]}, True),            # no peer field: every source
    ({"fromEntities": ["host"], "toPorts": [{"ports": [{"port": "8081"}]}]}, False),
    ({"fromEntities": ["world"], "toPorts": [{"ports": [{"port": "8080"}]}]}, False),
])
def test_host_rules_on_8080_are_found(h, tmp_path, monkeypatch, capsys, rule, admitted):
    cnp = repo_cnp()
    cnp["spec"]["ingress"].append(rule)
    monkeypatch.setattr(h.subprocess, "run", cluster(cnps=[cnp]))
    assert h.main() == 0
    assert outputs(capsys.readouterr().out)["hostTo8080"]["admitted"] is admitted


def test_a_clusterwide_deny_or_unrelated_selector_is_not_an_admission(h, tmp_path, monkeypatch, capsys):
    ccnps = [
        {"metadata": {"name": "anxious-lookout-tenant-boundary"}, "spec": {
            "endpointSelector": {"matchLabels": {"k8s:io.kubernetes.pod.namespace": "tenant-example"}},
            "ingress": [{"fromEntities": ["host"]}]}},
        {"metadata": {"name": "SECRET-ccnp"}, "spec": {
            "endpointSelector": {}, "ingressDeny": [{"fromEntities": ["host"], "toPorts": [{"ports": [{"port": "8080"}]}]}]}},
        {"metadata": {"name": "anxious-lookout-node-protection"}, "spec": {
            "nodeSelector": {}, "ingress": [{"fromEntities": ["host"]}]}},
        {"metadata": {"name": "expr"}, "spec": {
            "endpointSelector": {"matchExpressions": [{"key": "app", "operator": "Exists"}]},
            "ingress": [{"fromEntities": ["cluster"]}]}},
    ]
    monkeypatch.setattr(h.subprocess, "run", cluster(ccnps=ccnps))
    assert h.main() == 0
    raw = capsys.readouterr().out
    assert "SECRET" not in raw
    out = outputs(raw)
    chosen = {(x["kind"], x["name"]): x["selectsApi"] for x in out["policies"]["items"]}
    assert chosen[("CCNP", "anxious-lookout-tenant-boundary")] is False
    assert chosen[("CCNP", "other")] in (True, "unknown")
    assert chosen[("CCNP", "anxious-lookout-node-protection")] is False
    assert out["hostTo8080"]["admitted"] is False
    assert out["hostTo8080"]["rules"] == [{"policy": "other", "direction": "ingressDeny", "selects": True,
                                           "from": ["entities:host"]}]


@pytest.mark.parametrize("selector, want", [
    ({"matchLabels": {"app": "hosting-api"}}, True),
    ({"matchLabels": {"k8s:app": "hosting-api"}}, True),
    ({"matchLabels": {"any:app": "hosting-api"}}, True),
    ({"matchLabels": {"app": "hosting-web"}}, False),
    ({"matchLabels": {"k8s:io.kubernetes.pod.namespace": "m2-hosting"}}, True),
    ({"matchLabels": {"k8s:io.kubernetes.pod.namespace": "other"}}, False),
    ({}, True),
    ({"matchLabels": {"reserved:host": ""}}, "unknown"),
    ({"matchExpressions": [{"key": "app", "operator": "In", "values": ["hosting-api"]}]}, "unknown"),
])
def test_selector_matching_uses_the_endpoint_labels(h, selector, want):
    assert h.selects_api(selector, set(API_LABELS)) == want


def test_selection_is_unknown_without_exactly_one_api_endpoint(h, tmp_path, monkeypatch, capsys):
    two = {"status": {"identity": {"id": 1, "labels": API_LABELS}}}
    monkeypatch.setattr(h.subprocess, "run", cluster(ceps=[two, two]))
    assert h.main() == 0
    out = outputs(capsys.readouterr().out)
    assert out["endpoint"]["count"] == 2
    assert out["endpoint"]["items"][0] == {"identity": 1, "enforcing": "missing", "state": "missing"}
    assert [x["selectsApi"] for x in out["policies"]["items"]] == ["unknown"]


def test_client_record_variants_and_unknown_values_are_bucketed(h, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(h.subprocess, "run", cluster(config={"allow-localhost": "SECRET"}))
    folder = client_file(tmp_path, [{"kind": "headers", "status": 200}, {"kind": "SECRET"},
                                    {"kind": "end", "reason": "SECRET-r", "errorType": "SECRET-e"},
                                    {"kind": "end", "reason": "local_interrupt"}], hop3=True)
    with open(folder / "client-S4a-hop2.jsonl", "a") as stream:
        stream.write("not json SECRET\n[1]\n")
    assert h.main() == 0
    raw = capsys.readouterr().out
    assert "SECRET" not in raw
    out = outputs(raw)
    assert out["client"]["hop3FileForS4a"] is True
    assert out["client"]["hop2"] == {"counts": {"headers": 1, "other": 1, "end": 2}, "unparsed": 2, "headers": True,
                                     "ends": [{"reason": "other", "errorType": "other", "elapsedMs": None},
                                              {"reason": "local_interrupt", "errorType": "missing", "elapsedMs": None}]}
    assert out["cilium"] == {"kind": "cilium", "allowLocalhost": "other", "kubeProxyReplacement": "missing"}


def test_client_file_missing_symlink_or_several_runs(h, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(h.subprocess, "run", cluster())
    (tmp_path / RUN).mkdir()
    assert h.main() == 0
    assert outputs(capsys.readouterr().out)["client"]["hop2"] == "missing"
    target = tmp_path / "elsewhere.jsonl"
    target.write_text(json.dumps({"kind": "headers"}) + "\n")
    os.symlink(target, tmp_path / RUN / "client-S4a-hop2.jsonl")
    assert h.main() == 0
    assert outputs(capsys.readouterr().out)["client"]["hop2"] == "unreadable"
    (tmp_path / "i90-p5-turns-r6-20261115T000000Z").mkdir()
    assert h.main() == 0
    assert outputs(capsys.readouterr().out)["client"] == {"kind": "client", "runs": 2}


def test_fifo_client_file_does_not_block(h, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(h.subprocess, "run", cluster())
    (tmp_path / RUN).mkdir()
    os.mkfifo(tmp_path / RUN / "client-S4a-hop2.jsonl")
    assert h.main() == 0
    assert outputs(capsys.readouterr().out)["client"]["hop2"] == "unreadable"


@pytest.mark.parametrize("failing", ["configmap", "ciliumnetworkpolicies", "ciliumclusterwidenetworkpolicies",
                                     "ciliumendpoints"])
def test_refuses_when_a_lookup_fails_or_not_root(h, monkeypatch, capsys, failing):
    monkeypatch.setattr(h.subprocess, "run", cluster(fail=(failing,)))
    assert h.main() == 3
    assert outputs(capsys.readouterr().out)["refused"]["reason"] == "a cluster lookup failed"
    monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
    assert h.main() == 3
