"""Synthetic-only exact root route translation and generated one-hour TLS."""
import copy
import ipaddress
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from https_prepare import desired
from test_https_prepare import HOST, TLS, snapshot

root = Path("/fixture")
bundle = desired(snapshot.__wrapped__(), TLS)
routers = {}
for stage, routes in (("https", bundle["https-route"]["spec"]["routes"]),
                      ("http", bundle["http"]["routes"])):
    for i, route in enumerate(routes):
        routers[stage + str(i)] = {"rule": route["match"], "priority": route["priority"],
            "service": route["services"][0]["name"],
            "middlewares": [v["name"] for v in route["middlewares"]],
            "entryPoints": ["websecure" if stage == "https" else "web"],
            **({"tls": {}} if stage == "https" else {})}
# Lower-priority control routes deliberately test exclusions, not precedence alone.
for name, path in (("android", "/android-agent"), ("acme", "/.well-known/acme-challenge")):
    for stage in ("http", "https"):
        routers[name + stage] = {"rule": "Path(`" + path + "`) || PathPrefix(`" + path + "/`)",
            "priority": 1, "service": "control", "entryPoints": ["web" if stage == "http" else "websecure"],
            **({"tls": {}} if stage == "https" else {})}
dynamic = {"http": {"routers": routers, "middlewares": {
    bundle[k]["metadata"]["name"]: bundle[k]["spec"] for k in ("encoding", "redirect")},
    "services": {name: {"loadBalancer": {"servers": [{"url": "http://127.0.0.1:" + str(port)}]}}
                 for name, port in (("api", 8081), ("web", 8082), ("control", 8083))}},
    "tls": {"certificates": [{"certFile": "/routing/cert.pem", "keyFile": "/routing/key.pem"}]}}
(root / "routes.yml").write_text(json.dumps(dynamic))
probe = copy.deepcopy(dynamic)
probe["http"]["routers"] = {k: v for k, v in routers.items() if k.startswith(("android", "acme"))}
route = bundle["tls-probe"]["spec"]["routes"][0]
probe["http"]["routers"]["tls-probe"] = {"rule": route["match"], "priority": route["priority"],
    "service": "web", "middlewares": [v["name"] for v in route["middlewares"]],
    "entryPoints": ["websecure"], "tls": {}}
for i, route in enumerate(snapshot.__wrapped__()["httpRoute"]["spec"]["routes"]):
    probe["http"]["routers"]["old-http" + str(i)] = {"rule": route["match"],
        "priority": route["priority"], "service": "control", "entryPoints": ["web"]}
(root / "probe-routes.yml").write_text(json.dumps(probe))
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-root-only")])
now = datetime.now(timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.IPAddress(ipaddress.ip_address(HOST))]), False)
        .sign(key, hashes.SHA256()))
(root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(root / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
(root / "key.pem").chmod(0o600)
print("Synthetic root routes and one-hour TLS prepared; no actual inputs")
