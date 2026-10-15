"""Bounded secretless internal TCP canaries; emit only until PM authorization."""
import argparse
import ipaddress
import json
import socket
import sys
import time
from pathlib import Path

RUNTIME_SHA = "b19fafe8187fd37b7c11fcfadde40893c8ab3a9c"
PORT = 18080
ACK = b"secretless-policy-probe-v1"


def control_policy():
    from render import resource, selection, ports
    peers = [selection("policy-control"), selection("state-loader")]
    return resource("CiliumNetworkPolicy", "trial-policy-control", {
        "endpointSelector": {"matchLabels": {"app": "trial-policy-control"}},
        "enableDefaultDeny": {"ingress": True, "egress": True},
        "ingress": [{"fromEndpoints": peers, "toPorts": ports(PORT)}],
        "egress": [{"toEndpoints": peers, "toPorts": ports(PORT)}]}, version="cilium.io/v2")


def emit(image_reference, part="review"):
    from render import image, resource, pod, limits
    image(image_reference)
    config = resource("ConfigMap", "trial-policy-probe-v1", immutable=True,
                      data={"policy_probe.py": Path(__file__).read_text()})
    values = [config, control_policy()]
    for name, role in (("trial-policy-control-a", "policy-control"),
                       ("trial-policy-control-b", "policy-control"), ("trial-policy-denied", "state-loader")):
        template = pod(role, {"name": "probe", "image": image_reference,
            "command": ["python", "/probe/policy_probe.py", "serve"],
            "resources": limits("100m", "64Mi"),
            "volumeMounts": [{"name": "probe", "mountPath": "/probe", "readOnly": True}]},
            extra_volumes=[{"name": "probe", "configMap": {"name": "trial-policy-probe-v1", "defaultMode": 0o444}}])
        template["spec"]["restartPolicy"] = "Never"
        template["spec"]["activeDeadlineSeconds"] = 180
        values.append({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": "codex-trial",
            "labels": template["metadata"]["labels"]}, "spec": template["spec"]})
    if part == "control":
        values = values[:2]
    elif part == "pods":
        values = values[2:]
    elif part != "review":
        raise ValueError("Unknown canary part")
    return {"apiVersion": "v1", "kind": "List", "items": values}


def runtime_check():
    import os
    if os.geteuid() != 10001 or json.loads(Path("/app/release.json").read_text()).get("sha") != RUNTIME_SHA:
        raise ValueError("Approved secretless runtime required")


def exchange(address, expected, timeout=2):
    value = ipaddress.IPv4Address(address)
    if not (value.is_private or value.is_loopback) or value.is_unspecified or value.is_multicast or value.is_link_local:
        raise ValueError("Internal or loopback IPv4 only")
    try:
        with socket.create_connection((str(value), PORT), timeout=timeout) as client:
            client.sendall(b"fixture")
            reply = client.recv(100)
        success = reply == ACK
    except (TimeoutError, ConnectionError, OSError):
        success = False
    if success != expected:
        raise ValueError("Unexpected controlled TCP outcome")
    return {"status": "ok", "expect": "allow" if expected else "deny", "connected": success,
            "datapathAttributionRequired": not success, "nativeStarted": False}


def serve(seconds=150):
    deadline = time.monotonic() + seconds
    count = 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", PORT))
        listener.listen(8)
        listener.settimeout(0.5)
        print(json.dumps({"status": "listening", "port": PORT, "nativeStarted": False}), flush=True)
        while time.monotonic() < deadline:
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            with client:
                client.settimeout(1)
                try:
                    client.recv(100)
                    client.sendall(ACK)
                    count += 1
                    print(json.dumps({"served": count}), flush=True)  # No IP/payload/state values.
                except OSError:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("emit", "serve", "allow", "deny"))
    parser.add_argument("target", nargs="?")
    parser.add_argument("--part", choices=("review", "control", "pods"), default="review")
    args = parser.parse_args()
    try:
        if args.mode == "emit":
            print(json.dumps(emit(args.target, args.part), indent=2))
        else:
            runtime_check()
            if args.mode == "serve":
                serve()
            else:
                print(json.dumps(exchange(args.target, args.mode == "allow")))
    except Exception:
        print("Secretless policy probe refused; no input/credential values printed", file=sys.stderr)
        sys.exit(1)
