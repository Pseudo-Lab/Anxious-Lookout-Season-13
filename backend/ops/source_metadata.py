"""Selected file only, readonly metadata; no login, refresh, copy or writer proof."""
import json
import os
import stat
from pathlib import Path

from app.codex_policy import CodexFailure
from runner.auth import read_private


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inspect_source(path, binding_path=None):
    supplied = binding_path is not None
    binding = read_private(Path(binding_path)) if supplied else {"providerAccountId": None, "sourceUid": os.geteuid()}
    if (not isinstance(binding, dict) or set(binding) != {"providerAccountId", "sourceUid"}
        or (supplied and (not isinstance(binding["providerAccountId"], str) or not binding["providerAccountId"]))
        or type(binding["sourceUid"]) is not int or binding["sourceUid"] < 0):
        raise CodexFailure("policy_refused")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r") as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != binding["sourceUid"]
            or before.st_mode & 0o077 or before.st_nlink != 1 or before.st_size > 1_000_000):
            raise CodexFailure("policy_refused")
        cache = json.load(source)
        after = os.fstat(source.fileno())
        if stamp(before) != stamp(after) or stamp(after) != stamp(os.lstat(path)):
            raise CodexFailure("policy_refused")
    tokens = cache.get("tokens") if isinstance(cache, dict) else None
    compatible = (isinstance(tokens, dict) and not cache.get("OPENAI_API_KEY")
        and cache.get("auth_mode", "chatgpt") == "chatgpt"
        and all(isinstance(tokens.get(k), str) and tokens[k] for k in ("id_token", "access_token", "refresh_token", "account_id")))
    matches = bool(supplied and compatible and tokens["account_id"] == binding["providerAccountId"])
    # Do not return path, cache, ID, token/hash, decoded JWT or source timestamps.
    return {"sourceFilePrivate": True, "sourceReadStable": True,
            "structureCompatible": bool(compatible), "bindingProvided": supplied, "providerBindingMatches": matches,
            "authenticated": False, "sourceAuthoritative": "not_established",
            "refreshOwnership": "owner_evidence_required", "modelAccess": "not_tested"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--binding", help="Optional private expected-profile metadata; absence never establishes binding")
    args = parser.parse_args()
    try:
        result = inspect_source(args.source, args.binding)
    except Exception:
        parser.exit(1, "Selected source metadata refused; private values are not printed\n")
    print(json.dumps(result))
    if not result["structureCompatible"] or (result["bindingProvided"] and not result["providerBindingMatches"]):
        parser.exit(1)
