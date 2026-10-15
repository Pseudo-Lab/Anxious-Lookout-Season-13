"""Durable account/state binding. Initialization never starts native or reads auth."""
import os
import stat
import uuid
from pathlib import Path

from app.codex_policy import CodexFailure
from .auth import private_directory, read_private, write_private


def canonical_uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, AttributeError, TypeError):
        raise CodexFailure("policy_refused") from None


def require_binding(root, owner, state_id):
    owner, state_id = canonical_uuid(owner), canonical_uuid(state_id)
    root = private_directory(Path(root))
    if read_private(root / "account-id", json_format=False).strip() != owner:
        raise CodexFailure("policy_refused")
    value = read_private(root / "state-binding.json")
    if not isinstance(value, dict) or type(value.get("version")) is not int or value != {"version": 1, "ownerId": owner, "stateId": state_id}:
        raise CodexFailure("policy_refused")


def initialize_binding(root, owner, state_id):
    """Explicit reviewed first-use only; never adopt an old/partial state root."""
    owner, state_id = canonical_uuid(owner), canonical_uuid(state_id)
    root = Path(root)
    if root != root.resolve() or root.is_symlink():
        raise CodexFailure("policy_refused")
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or any(root.iterdir()):
        raise CodexFailure("policy_refused")
    root.chmod(0o700)  # Selected fresh runner-owned root only, never host auth.
    fd = os.open(root / "account-id", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(owner)
        target.flush()
        os.fsync(target.fileno())
    write_private(root / "state-binding.json", {"version": 1, "ownerId": owner, "stateId": state_id})
    require_binding(root, owner, state_id)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--inputs", required=True, help="Private JSON ownerId/stateId input, never token values")
    parser.add_argument("--initialize", action="store_true")
    args = parser.parse_args()
    try:
        inputs = read_private(Path(args.inputs))
        if set(inputs) != {"ownerId", "stateId"}:
            raise CodexFailure("policy_refused")
        operation = initialize_binding if args.initialize else require_binding
        operation(args.root, inputs["ownerId"], inputs["stateId"])
    except Exception:
        parser.exit(1, "Runner state binding refused; private values are not printed\n")
    print("PASS runner owner/state binding; no authentication or native process")
