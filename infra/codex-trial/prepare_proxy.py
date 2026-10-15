"""Synthetic TLS fixture; translate only the provider/service/address seams."""
import ipaddress
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from render import render

root = Path("/fixture")
inputs = json.loads(Path(__file__).with_name("input.example.json").read_text())
bundle = render(inputs)
(root / "manifest.json").write_text(json.dumps(bundle, indent=2))
items = {(item["kind"], item["metadata"]["name"]): item for item in bundle["items"]}
middlewares = {name: item["spec"] for (kind, name), item in items.items() if kind == "Middleware"}
# The production source ACL is not a synthetic loopback. Override only this
# test identity seam, keep the default RemoteAddr strategy (no forwarded header).
middlewares["trial-access"] = {"ipAllowList": {"sourceRange": ["127.0.0.1/32"]}}
routes = items["IngressRoute", "trial"]["spec"]["routes"]
routers = {}
for route in routes:
    app = route["services"][0]["name"]
    routers["trial-" + app] = {"rule": route["match"], "priority": route["priority"],
        "service": "trial-" + app, "middlewares": [m["name"] for m in route["middlewares"]],
        "entryPoints": ["websecure"], "tls": {}}
# Read-only topology control equivalents: old hosting on HTTP and the unrelated
# HTTPS app prefix, neither with new trial middleware. These are synthetic routes.
routers.update({"old-api": {"rule": "Host(`trial.example.invalid`) && (Path(`/api`) || PathPrefix(`/api/`))",
    "service": "control", "entryPoints": ["web"], "priority": 20},
    "old-web": {"rule": "Host(`trial.example.invalid`) && PathPrefix(`/`) && !(Path(`/api`) || PathPrefix(`/api/`))",
    "service": "control", "entryPoints": ["web"], "priority": 10},
    "other-app": {"rule": "Host(`trial.example.invalid`) && PathPrefix(`/android-agent/`)",
    "service": "control", "entryPoints": ["websecure"], "tls": {}, "priority": 20}})
dynamic = {"http": {"routers": routers, "middlewares": middlewares, "services": {
    name: {"loadBalancer": {"servers": [{"url": "http://127.0.0.1:" + str(port)}]}}
    for name, port in (("trial-api", 8081), ("trial-web", 8082), ("control", 8083))}},
    "tls": {"certificates": [{"certFile": "/routing/cert.pem", "keyFile": "/routing/key.pem"}]}}
(root / "routes.yml").write_text(json.dumps(dynamic, indent=2))  # JSON is valid YAML.
probe = items["IngressRoute", "trial-access-probe"]["spec"]["routes"][0]
probe_dynamic = {"http": {**dynamic["http"], "routers": {
    **{name: router for name, router in routers.items() if not name.startswith("trial-")},
    "trial-access-probe": {"rule": probe["match"], "priority": probe["priority"],
        "service": "trial-web", "middlewares": [m["name"] for m in probe["middlewares"]],
        "entryPoints": ["websecure"], "tls": {}}}}, "tls": dynamic["tls"]}
(root / "probe-routes.yml").write_text(json.dumps(probe_dynamic, indent=2))
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-trial-only")])
current = datetime.now(timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(current - timedelta(minutes=1))
        .not_valid_after(current + timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([
            x509.DNSName("trial.example.invalid"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .sign(key, hashes.SHA256()))
(root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(root / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
(root / "key.pem").chmod(0o600)
print("Synthetic exact render and one-hour TLS certificate prepared; no actual inputs")
