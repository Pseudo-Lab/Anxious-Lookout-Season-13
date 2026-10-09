import threading
import time

import pytest

from policy_probe import emit, control_policy, serve, exchange


def test_canaries_have_no_secret_state_tokens_or_native_startup():
    values = emit("example.invalid/api@sha256:" + "a" * 64)["items"]
    assert [v["kind"] for v in values] == ["ConfigMap", "CiliumNetworkPolicy", "Pod", "Pod", "Pod"]
    assert all(v["metadata"]["namespace"] == "codex-trial" for v in values)
    image = "example.invalid/api@sha256:" + "a" * 64
    assert emit(image, "control")["items"] == values[:2]
    assert emit(image, "pods")["items"] == values[2:]
    assert values[1]["spec"]["endpointSelector"]["matchLabels"] == {"app": "trial-policy-control"}
    for value in values[2:]:
        spec = value["spec"]
        assert spec["securityContext"]["runAsUser"] == 10001 and not spec["automountServiceAccountToken"]
        assert spec["activeDeadlineSeconds"] == 180 and spec["restartPolicy"] == "Never"
        assert all(not any(key in volume for key in ("secret", "hostPath", "persistentVolumeClaim")) for volume in spec["volumes"])
        assert spec["containers"][0]["command"] == ["python", "/probe/policy_probe.py", "serve"]


def test_control_policy_is_only_internal_selected_peers_and_probe_port():
    spec = control_policy()["spec"]
    for direction, key in (("ingress", "fromEndpoints"), ("egress", "toEndpoints")):
        rule = spec[direction][0]
        assert set(rule) == {key, "toPorts"}
        assert rule["toPorts"] == [{"ports": [{"port": "18080", "protocol": "TCP"}]}]
        assert {target["matchLabels"]["k8s:app"] for target in rule[key]} == {"trial-policy-control", "trial-state-loader"}
        assert all(target["matchLabels"]["k8s:io.kubernetes.pod.namespace"] == "codex-trial" for target in rule[key])


def test_tcp_positive_and_failure_do_not_claim_datapath_proof():
    worker = threading.Thread(target=serve, args=(1.5,), daemon=True)
    worker.start()
    time.sleep(0.1)
    assert exchange("127.0.0.1", True)["connected"] is True
    with pytest.raises(ValueError):
        exchange("127.0.0.1", False)
    worker.join(timeout=3)
    result = exchange("127.0.0.1", False)
    assert result["datapathAttributionRequired"] is True and result["connected"] is False


def test_public_targets_and_tag_only_images_refused():
    with pytest.raises(ValueError):
        exchange("93.184.216.34", False)
    with pytest.raises(ValueError):
        emit("api:latest")
