"""Contract checks on actual Docker-rendered manifests, never real inputs."""
import ast
import json
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

RENDERS = Path(os.environ["PERSONAL_CONFIG_RENDER_DIR"])


def rendered(name):
    return json.loads((RENDERS / (name + ".json")).read_text())


def test_bootstrap_without_any_uuid_or_runner_input():
    document = rendered("bootstrap")
    services = document["services"]
    assert set(services) == {"db", "api", "web", "gateway", "migrate", "admin"}
    assert "personal-native-state" not in document.get("volumes", {})
    env = services["api"]["environment"]
    assert env["CODEX_PERSONAL_ENABLE"] == "false" and env["CODEX_RUNNERS_FILE"] == ""
    assert "CODEX_PERSONAL_ACCOUNT_ID" not in env
    assert not any("PERSONAL_PLATFORM_ACCOUNT_ID=" in line for line in (RENDERS / "bootstrap.env").read_text().splitlines())


def test_enable_preserves_project_db_origin_images_and_limits():
    before, after = rendered("bootstrap"), rendered("enabled")
    assert before["name"] == after["name"]
    assert before["services"]["db"] == after["services"]["db"]
    assert before["volumes"]["personal-db-data"] == after["volumes"]["personal-db-data"]
    for service in ("api", "web", "gateway", "migrate", "admin"):
        assert before["services"][service]["image"] == after["services"][service]["image"]
    for field in ("AUTH_ORIGIN", "DATABASE_URL_FILE", "GITHUB_CLIENT_ID"):
        assert before["services"]["api"]["environment"][field] == after["services"]["api"]["environment"][field]
    api = after["services"]["api"]["environment"]
    native = after["services"]["personal-runner"]
    assert api["CODEX_PERSONAL_ENABLE"] == "true" and api["CODEX_PERSONAL_ACCOUNT_ID"] == native["environment"]["RUNNER_ACCOUNT_ID"]
    assert native["environment"]["CODEX_MODEL"] == "gpt-6.1-sol"
    assert native["environment"]["CODEX_AUTH_MODE"] == "personal-cache"
    assert native["environment"]["CODEX_EXECUTION_SCOPE"] == "personal-private"
    assert native["cpus"] and native["mem_limit"]
    assert not native.get("ports") and not native.get("privileged", False)


@pytest.mark.parametrize("mode", ["bootstrap", "enabled"])
def test_private_file_wiring_and_operator_privilege_separation(mode):
    services = rendered(mode)["services"]
    env = services["api"]["environment"]
    assert env["OAUTH_MODE"] == "github" and env["APP_ENV"] == "personal-test"
    for secret in ("DATABASE_URL", "GITHUB_CLIENT_SECRET", "AUTH_TRANSACTION_KEY"):
        assert secret not in env and env[secret + "_FILE"].startswith("/run/personal-")
    assert "ADMIN_DATABASE_URL_FILE" not in env
    assert services["db"]["environment"]["POSTGRES_PASSWORD_FILE"] == "/run/secrets/postgres-password"
    assert "POSTGRES_PASSWORD" not in services["db"]["environment"]
    for name in ("migrate", "admin"):
        operator = services[name]
        assert operator["image"] == services["api"]["image"]
        assert operator["environment"]["ADMIN_DATABASE_URL_FILE"] == "/run/personal-ops/admin-url"
        assert operator["environment"]["API_DATABASE_PASSWORD_FILE"] == "/run/personal-ops/api-password"
        assert set(operator["networks"]) == {"data"}
        assert operator["profiles"] == ["ops"]
        assert operator["user"] == "10001:10001"
    assert services["migrate"]["command"][-1] == "0004_publication"
    serialized = json.dumps(services)
    assert "disposable-" not in serialized and "mock-github" not in serialized and "fixture-image" not in serialized
    for service in services.values():
        assert not service.get("build") and not service.get("container_name")
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                assert mount.get("bind", {}).get("create_host_path", False) is False


@pytest.mark.parametrize("mode", ["bootstrap", "enabled"])
def test_network_boundary_and_loopback_only(mode):
    document = rendered(mode)
    services, networks = document["services"], document["networks"]
    for service, spec in services.items():
        assert not spec.get("ports") or service == "gateway"
        assert "default" not in spec["networks"]
    ports = services["gateway"]["ports"]
    assert len(ports) == 1 and ports[0]["host_ip"] == "127.0.0.1"
    assert services["api"]["environment"]["AUTH_ORIGIN"] == "http://127.0.0.1:" + str(ports[0]["published"])
    assert set(services["db"]["networks"]) == {"data"} and networks["data"]["internal"]
    assert networks["web-api"]["internal"]
    assert networks["github-egress"]["external"]
    assert "github-egress" in services["api"]["networks"]
    assert all("github-egress" not in spec["networks"] for name, spec in services.items() if name != "api")
    if mode == "enabled":
        native = services["personal-runner"]
        assert set(native["networks"]) == {"api-runner", "provider-egress"}
        assert networks["api-runner"]["internal"] and networks["provider-egress"]["external"]
        assert networks["github-egress"]["name"] != networks["provider-egress"]["name"]
        assert not set(native["networks"]) & set(services["db"]["networks"])
        assert all("provider-egress" not in spec["networks"] for name, spec in services.items() if name != "personal-runner")
        api_targets = {mount["target"] for mount in services["api"]["volumes"]}
        assert "/run/personal-control" not in api_targets and "/state" not in api_targets
        assert all(mount["type"] != "bind" or mount.get("read_only", False) or mount["target"] == "/run/personal-control" for mount in native["volumes"])


def matches(rule, path):
    expression = re.sub(r"(Path|PathPrefix)\(`([^`]*)`\)",
                        lambda found: str(path == found[2] if found[1] == "Path" else path.startswith(found[2])), rule)
    expression = expression.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) is bool:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not evaluate(node.operand)
        if isinstance(node, ast.BoolOp):
            return all(evaluate(child) for child in node.values) if isinstance(node.op, ast.And) else any(evaluate(child) for child in node.values)
        raise AssertionError("Unexpected route expression")
    return evaluate(ast.parse(expression.strip(), mode="eval"))


@pytest.mark.parametrize("path,expected", [("/", "web"), ("/research", "web"), ("/api/auth/github/start", "api"),
    ("/api/auth/github/callback", "api"), ("/api/research/sessions", "api"),
    ("/_fixture/github/authorize", None), ("/api/internal/research/tools", None)])
def test_gateway_route_contract(path, expected):
    routing = rendered("routes")["x-routing"]["http"]
    assert set(routing["services"]) == {"api", "web"}
    routed = [router["service"] for router in routing["routers"].values() if matches(router["rule"], path)]
    assert routed == ([] if expected is None else [expected])


def test_required_private_input_rejections():
    rows = (RENDERS / "rejections.tsv").read_text().splitlines()
    assert len(rows) == 20 and all(row.endswith("\trejected") for row in rows)
    assert any(row.startswith("PERSONAL_PLATFORM_ACCOUNT_ID\t") for row in rows)
    assert any(row.startswith("PERSONAL_PROVIDER_EGRESS_NETWORK\t") for row in rows)


@pytest.mark.parametrize("mode", ["bootstrap", "enabled"])
def test_rendered_api_environment_loads_private_files_without_network(mode, tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from app.settings import Settings
    env = rendered(mode)["services"]["api"]["environment"]
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    values = {"DATABASE_URL": "postgresql+psycopg://synthetic:unused@unstarted/synthetic_personal_db",
              "GITHUB_CLIENT_SECRET": "synthetic-client-secret", "AUTH_TRANSACTION_KEY": Fernet.generate_key().decode()}
    for name, value in values.items():
        file = tmp_path / name.lower()
        file.write_text(value)
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(name + "_FILE", str(file))
    settings = Settings.load()  # Pure configuration; no application/DB/provider starts.
    assert settings.oauth_mode == "github" and settings.client_secret == values["GITHUB_CLIENT_SECRET"]
    assert settings.database_url == values["DATABASE_URL"] and settings.transaction_key == values["AUTH_TRANSACTION_KEY"]
    assert settings.callback_url == env["AUTH_ORIGIN"] + "/api/auth/github/callback"
    Fernet(settings.transaction_key.encode())


def validator():
    spec = importlib.util.spec_from_file_location("config_checker", os.environ["PERSONAL_CONFIG_CHECKER_PATH"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


@pytest.mark.parametrize("mode", ["bootstrap", "enabled"])
def test_operator_structure_checker_accepts_renderer_output(mode):
    validator()(rendered(mode), enabled=mode == "enabled")


@pytest.mark.parametrize("damage", ["network_alias", "public_ingress", "runner_db", "operator_image", "owner", "state", "model", "shared_control", "parent_control", "api_provider_net", "unbounded_resources", "encoded_missing", "encoded_relaxed", "encoded_duplicate"])
def test_operator_checker_rejects_unsafe_configuration_before_any_start(damage):
    config = rendered("enabled")
    if damage == "network_alias":
        config["networks"]["provider-egress"]["name"] = config["networks"]["data"]["name"]
    elif damage == "public_ingress":
        config["services"]["gateway"]["ports"][0]["host_ip"] = "0.0.0.0"
    elif damage == "runner_db":
        config["services"]["personal-runner"]["networks"]["data"] = {}
    elif damage == "operator_image":
        config["services"]["migrate"]["image"] = "synthetic-stale-ops:unstarted"
    elif damage == "owner":
        config["services"]["api"]["environment"]["CODEX_PERSONAL_ACCOUNT_ID"] = "invalid-owner"
    elif damage == "state":
        config["services"]["personal-runner"]["environment"]["RUNNER_STATE_ID"] = "not-a-state-uuid"
    elif damage == "model":
        config["services"]["personal-runner"]["environment"]["CODEX_MODEL"] = "unexpected-model"
    elif damage in {"shared_control", "parent_control"}:
        control = next(mount["source"] for mount in config["services"]["personal-runner"]["volumes"] if mount["target"] == "/run/personal-control")
        config["services"]["api"]["volumes"][0]["source"] = control if damage == "shared_control" else str(Path(control).parent)
    elif damage == "api_provider_net":
        config["services"]["api"]["networks"]["provider-egress"] = {}
    elif damage == "unbounded_resources":
        config["services"]["personal-runner"]["mem_limit"] = -1
    elif damage.startswith("encoded_"):
        command = config["services"]["gateway"]["command"]
        flag = "--entrypoints.web.http.encodedcharacters.allowencodedslash=false"
        if damage == "encoded_missing":
            command.remove(flag)
        elif damage == "encoded_relaxed":
            command[command.index(flag)] = flag.replace("=false", "=true")
        else:
            command.append(flag.replace("=false", "=true"))
    with pytest.raises(ValueError):
        validator()(config, enabled=True)


@pytest.mark.parametrize("mode", ["bootstrap", "enabled"])
def test_checker_cli_runs_without_application_or_secret_reads(mode):
    result = subprocess.run([sys.executable, os.environ["PERSONAL_CONFIG_CHECKER_PATH"], "--mode", mode,
                             "--config", str(RENDERS / (mode + ".json"))], capture_output=True, text=True)
    assert result.returncode == 0 and "structure passed" in result.stdout
    assert "synthetic-github-client" not in result.stdout + result.stderr


def test_checker_cli_never_prints_rejected_private_metadata(tmp_path):
    config = rendered("enabled")
    sentinel = "SYNTHETIC-PRIVATE-METADATA-SENTINEL"
    config["services"]["api"]["environment"]["CODEX_PERSONAL_ACCOUNT_ID"] = sentinel
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(config))
    result = subprocess.run([sys.executable, os.environ["PERSONAL_CONFIG_CHECKER_PATH"], "--mode", "enabled",
                             "--config", str(path)], capture_output=True, text=True)
    assert result.returncode == 1 and result.stderr.strip() == "Personal configuration rejected"
    assert sentinel not in result.stdout + result.stderr
