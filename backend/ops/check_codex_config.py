"""Check a private Compose JSON render, without secrets/network/service execution.

This checks the declared topology, not actual target policies or source authority.
Only generic outcomes are printed; rendered identifiers/paths stay private.
"""
import argparse
import json
import math
import sys
import uuid
from pathlib import Path, PurePosixPath


def check(config, enabled=False):
    services, networks = config["services"], config["networks"]
    expected = {"db", "api", "web", "gateway", "migrate", "admin"}
    if enabled:
        expected.add("personal-runner")
    if set(services) != expected or not config.get("name"):
        raise ValueError()
    physical_names = [network["name"] for network in networks.values()]
    if len(physical_names) != len(set(physical_names)):
        raise ValueError()  # External egress must not alias another declared network.
    api = services["api"]
    env = api["environment"]
    ports = services["gateway"].get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or env["AUTH_ORIGIN"] != "http://127.0.0.1:" + str(ports[0]["published"]):
        raise ValueError()
    for name, service in services.items():
        if service.get("build") or service.get("container_name") or service.get("privileged") or service.get("network_mode"):
            raise ValueError()
        if name != "gateway" and service.get("ports"):
            raise ValueError()
        cpus = float(service.get("cpus", 0))
        if not math.isfinite(cpus) or cpus <= 0 or int(service.get("mem_limit", 0)) <= 0 or "default" in service["networks"]:
            raise ValueError()
        for mount in service.get("volumes", []):
            if mount.get("source", "").endswith("docker.sock") or mount["target"] in {"/root", "/home"}:
                raise ValueError()
    if env["APP_ENV"] != "personal-test" or env["OAUTH_MODE"] != "github":
        raise ValueError()
    for name in ("DATABASE_URL", "GITHUB_CLIENT_SECRET", "AUTH_TRANSACTION_KEY"):
        if env.get(name) or not env.get(name + "_FILE"):
            raise ValueError()
    if env.get("ADMIN_DATABASE_URL") or env.get("ADMIN_DATABASE_URL_FILE"):
        raise ValueError()
    if set(services["db"]["networks"]) != {"data"} or not networks["data"].get("internal") or not networks["web-api"].get("internal"):
        raise ValueError()
    if not networks["github-egress"].get("external") or "github-egress" not in api["networks"]:
        raise ValueError()
    expected_api_networks = {"data", "web-api", "github-egress"}
    if enabled:
        expected_api_networks.add("api-runner")
    if set(api["networks"]) != expected_api_networks:
        raise ValueError()
    for name, service in services.items():
        if name != "api" and "github-egress" in service["networks"]:
            raise ValueError()
    for name in ("migrate", "admin"):
        operator = services[name]
        if operator["image"] != api["image"] or set(operator["networks"]) != {"data"} or operator.get("profiles") != ["ops"]:
            raise ValueError()
        if not operator["environment"].get("ADMIN_DATABASE_URL_FILE") or not operator["environment"].get("API_DATABASE_PASSWORD_FILE"):
            raise ValueError()
    if not enabled:
        if env.get("CODEX_PERSONAL_ENABLE") != "false" or env.get("CODEX_RUNNERS_FILE") or env.get("CODEX_PERSONAL_ACCOUNT_ID"):
            raise ValueError()
        return
    native = services["personal-runner"]
    native_env = native["environment"]
    owner = env["CODEX_PERSONAL_ACCOUNT_ID"]
    if str(uuid.UUID(owner)) != owner or native_env["RUNNER_ACCOUNT_ID"] != owner or env["CODEX_PERSONAL_ENABLE"] != "true":
        raise ValueError()
    if not env.get("CODEX_RUNNERS_FILE") or native_env.get("CODEX_MODEL") != "gpt-6.1-sol" or native_env.get("CODEX_AUTH_MODE") != "personal-cache" or native_env.get("CODEX_EXECUTION_SCOPE") != "personal-private":
        raise ValueError()
    if set(native["networks"]) != {"api-runner", "provider-egress"} or not networks["api-runner"].get("internal") or not networks["provider-egress"].get("external"):
        raise ValueError()
    if "/run/personal-control" in {mount["target"] for mount in api.get("volumes", [])}:
        raise ValueError()
    control = next(mount for mount in native["volumes"] if mount["target"] == "/run/personal-control")
    control_path = PurePosixPath(control["source"])
    for name, service in services.items():
        if name != "personal-runner" and "provider-egress" in service["networks"]:
            raise ValueError()
        if name == "personal-runner":
            continue
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                other = PurePosixPath(mount["source"])
                if control_path.is_relative_to(other) or other.is_relative_to(control_path):
                    raise ValueError()  # Provider credentials must never enter API/ops/frontend mounts.


def main():
    parser = argparse.ArgumentParser(description="Structure only; no target-policy/credential/model approval")
    parser.add_argument("--mode", choices=["bootstrap", "enabled"], required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        check(json.loads(Path(args.config).read_text()), enabled=args.mode == "enabled")
    except Exception:
        print("Personal configuration rejected", file=sys.stderr)
        return 1
    print("Personal configuration structure passed; target/source review still required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
