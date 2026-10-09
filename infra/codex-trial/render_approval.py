"""Emit a trial-only permission Job using a privately verified operator decision."""
import json
import re
import sys

from render import render


def approval(config, decision):
    if (set(decision) != {"githubId", "approved", "role", "actor", "reason"}
            or not isinstance(decision["githubId"], str) or not re.fullmatch(r"[1-9][0-9]{0,31}", decision["githubId"])
            or type(decision["approved"]) is not bool or decision["role"] not in {"commenter", "editor", "admin"}
            or not all(isinstance(decision[key], str) and 0 < len(decision[key]) <= 200 for key in ("actor", "reason"))):
        raise ValueError()
    job = render(config, "migrate")["items"][0]
    job["metadata"].pop("name")
    job["metadata"]["generateName"] = "trial-approval-"
    job["spec"]["template"]["spec"]["containers"][0]["command"] = ["python", "-m", "app.admin",
        "--github-id", decision["githubId"], "--approved", str(decision["approved"]).lower(),
        "--role", decision["role"], "--actor", decision["actor"], "--reason", decision["reason"]]
    return job


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as source:
            config = json.load(source)
        with open(sys.argv[2]) as source:
            decision = json.load(source)
        print(json.dumps(approval(config, decision), indent=2))
    except Exception:
        print("Trial approval render refused; private decision is not printed", file=sys.stderr)
        sys.exit(1)
