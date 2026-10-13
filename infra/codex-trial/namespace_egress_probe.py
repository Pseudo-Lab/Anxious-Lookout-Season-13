"""Emit/run short-lived credential-free k3s probes; never invoke Codex."""
import argparse
import ipaddress
import json
from pathlib import Path
import signal
import socket
import ssl
import sys
import time
from urllib.request import build_opener, ProxyHandler

from namespace_egress import NS, require

PORT = 18080
ACK = b"namespace-egress-probe-v1"
NAMES = ("trial-egress-source-v1", "trial-egress-peer-v1")


def emit(image):
    from render import image as validate_image, pod, limits, resource
    validate_image(image)
    cm = resource("ConfigMap", "trial-egress-probe-v1", immutable=True, data={
        "namespace_egress_probe.py": Path(__file__).read_text(),
        "namespace_egress.py": Path(__file__).with_name("namespace_egress.py").read_text()})
    items = [cm]
    for name in NAMES:
        template = pod("namespace-egress-probe", {"name": "probe", "image": image,
            "command": ["python", "-B", "/probe/namespace_egress_probe.py", "serve"],
            "resources": limits("100m", "64Mi"),
            "volumeMounts": [{"name": "probe", "mountPath": "/probe", "readOnly": True}]},
            extra_volumes=[{"name": "probe", "configMap": {"name": "trial-egress-probe-v1", "defaultMode": 0o444}}])
        template["metadata"]["labels"]["probe-name"] = name
        template["spec"].update(restartPolicy="Never", activeDeadlineSeconds=300)
        template["spec"]["containers"][0]["readinessProbe"] = {
            "tcpSocket": {"port": PORT}, "periodSeconds": 2}
        items.append({"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": name, "namespace": NS, "labels": template["metadata"]["labels"]}, "spec": template["spec"]})
    items.append(resource("Service", "trial-egress-peer-v1", {"selector": {"probe-name": NAMES[1]},
        "ports": [{"name": "probe", "protocol": "TCP", "port": PORT, "targetPort": PORT}]}))
    return {"apiVersion": "v1", "kind": "List", "items": items}


def exchange(address):
    with socket.create_connection((address, PORT), timeout=3) as client:
        client.sendall(b"probe")
        return client.recv(64) == ACK


def server():
    deadline = time.monotonic() + 240
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("0.0.0.0", PORT))
        listener.listen(8)
        listener.settimeout(0.5)
        count = 0
        print(json.dumps({"listening": True, "nativeStarted": False}), flush=True)
        while time.monotonic() < deadline:
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            with client:
                client.settimeout(2)
                try:
                    data = client.recv(64)
                    if data == b"probe":
                        client.sendall(ACK)
                        count += 1
                        print(json.dumps({"served": count}), flush=True)
                except OSError:
                    pass


def check(targets, denied_only=False):
    deadline = time.monotonic() + 40
    def expired(signum, frame):
        raise TimeoutError("Bounded probe deadline")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(40)
    require(set(targets) == {"peerPod", "peerService", "apiPod", "apiService"})
    for address in targets.values():
        value = ipaddress.ip_address(address)
        require(value.version == 4 and value.is_private and not (value.is_loopback or value.is_unspecified or
                                         value.is_multicast or value.is_link_local))
    require(exchange("127.0.0.1"))
    print(json.dumps({"case": "same-pod-loopback", "connected": True}), flush=True)
    for case in ("peerPod", "peerService"):
        connected = False
        try:
            # A TCP handshake alone already violates the requested deny.
            with socket.create_connection((targets[case], PORT), timeout=3):
                connected = True
        except OSError:
            pass
        require(not connected)
        print(json.dumps({"case": case, "connected": False, "policyDropEvidenceRequired": True}), flush=True)
    if denied_only:
        signal.alarm(0)
        return
    opener = build_opener(ProxyHandler({}))
    for case in ("apiPod", "apiService"):
        host = targets[case] if ":" not in targets[case] else "[" + targets[case] + "]"
        with opener.open("http://" + host + ":8080/healthz", timeout=5) as response:
            require(response.status == 200)
        print(json.dumps({"case": case, "httpStatus": 200}), flush=True)
    # These exact names are test destinations, never policy allow-list restrictions.
    for host in ("api.m2-hosting.svc.cluster.local", "chatgpt.com"):
        remaining = int(deadline - time.monotonic())
        require(remaining > 0)
        signal.alarm(min(8, remaining))
        require(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        remaining = int(deadline - time.monotonic())
        require(remaining > 0)
        signal.alarm(remaining)
    print(json.dumps({"case": "dns", "resolved": True}), flush=True)
    context = ssl.create_default_context()
    with socket.create_connection(("chatgpt.com", 443), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname="chatgpt.com"):
            pass
    print(json.dumps({"case": "external-tls", "verifiedHandshake": True,
                      "providerRequest": False, "nativeStarted": False}), flush=True)
    signal.alarm(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("emit", "serve", "check", "deny", "loopback"))
    parser.add_argument("image", nargs="?")
    args = parser.parse_args()
    try:
        if args.mode == "emit":
            print(json.dumps(emit(args.image), indent=2))
        elif args.mode == "serve":
            server()
        elif args.mode == "loopback":
            require(exchange("127.0.0.1"))
            print(json.dumps({"loopback": True}))
        else:
            check(json.load(sys.stdin), denied_only=args.mode == "deny")
    except Exception:
        parser.exit(1, "Namespace egress probe refused/failed; target and payload values omitted\n")
