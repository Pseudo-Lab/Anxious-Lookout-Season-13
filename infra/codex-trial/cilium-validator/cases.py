"""Synthetic full rules and pre-fix counterexamples for actual Cilium validation."""
from copy import deepcopy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render import policy
from policy_probe import control_policy


def legacy(rules):
    values = deepcopy(rules)
    for rule in values:
        for direction in ("ingress", "egress"):
            if direction + "Deny" in rule:
                del rule[direction + "Deny"]
                rule[direction] = []
    return values


bootstrap = policy({})["specs"]
enabled = policy({"runner": {"providerHosts": ["native.example.invalid"]}})["specs"]
error = "rule must have at least one of Ingress, IngressDeny, Egress, EgressDeny"
loader = [rule for rule in legacy(bootstrap) if rule["endpointSelector"]["matchLabels"]["app"] == "trial-state-loader"]
print(json.dumps([
    {"name": "fixed-bootstrap-all-six-roles", "rules": bootstrap, "expectedError": ""},
    {"name": "fixed-runner-enabled-all-six-roles", "rules": enabled, "expectedError": ""},
    {"name": "legacy-bootstrap-rejected", "rules": legacy(bootstrap), "expectedError": error},
    {"name": "legacy-runner-enabled-rejected", "rules": legacy(enabled), "expectedError": error},
    {"name": "legacy-loader-reproduces-actual-failure", "rules": loader, "expectedError": error},
    {"name": "secretless-control-peer-policy", "rules": [control_policy()["spec"]], "expectedError": ""},
], indent=2))
