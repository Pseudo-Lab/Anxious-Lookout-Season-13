"""Opt-in personal managed cache. Real provisioning needs procedure review.

The control directory is outside native-home backups. Declarations here cannot
prove that an existing host stopped refreshing the same renewable session.
"""
import fcntl
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.codex_policy import CodexFailure


def private_directory(path):
    path = Path(path)
    try:
        value = path.lstat()
    except OSError:
        raise CodexFailure("policy_refused") from None
    if path != path.resolve() or not stat.S_ISDIR(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077:
        raise CodexFailure("policy_refused")
    return path


def read_private(path, json_format=True):
    private_directory(path.parent)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise CodexFailure("policy_refused") from None
    with os.fdopen(descriptor, "r") as source:
        value = os.fstat(source.fileno())
        if not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077 or value.st_nlink != 1 or value.st_size > 1_000_000:
            raise CodexFailure("policy_refused")
        try:
            return json.load(source) if json_format else source.read()
        except (ValueError, UnicodeError):
            raise CodexFailure("policy_refused") from None


def write_private(path, value):
    private_directory(path.parent)
    temporary = path.parent / (".private-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as target:
            json.dump(value, target)
            target.flush()
            os.fsync(target.fileno())
        if path.is_symlink():
            raise CodexFailure("policy_refused")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class PersonalAuth:
    def __init__(self, control, owner):
        self.control, self.owner = private_directory(Path(control)), str(uuid.UUID(owner))
        self.cache = None
        self.owns_cache = False
        self.secrets = set()
        self.lease = None
        self.ready = False

    def grant(self):
        try:
            value = read_private(self.control / "grant.json")
            required = {"version", "platformAccountId", "providerAccountId", "trialId", "expiresAt", "maxDispatches",
                        "executionScope", "refreshOwnership", "procedureReviewed", "hostGrantQuiescent", "revoked"}
            if set(value) != required or value["version"] != 1 or value["platformAccountId"] != self.owner or value["maxDispatches"] != 3:
                raise CodexFailure("policy_refused")
            if value["executionScope"] != "personal-private" or value["refreshOwnership"] != "exclusive-managed-native" or value["procedureReviewed"] is not True or value["hostGrantQuiescent"] is not True:
                raise CodexFailure("policy_refused")
            uuid.UUID(value["trialId"])
            if not isinstance(value["providerAccountId"], str) or not value["providerAccountId"]:
                raise CodexFailure("policy_refused")
            if value["revoked"] is not False:
                raise CodexFailure("auth_revoked")
            expiry = datetime.fromisoformat(value["expiresAt"].replace("Z", "+00:00"))
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise CodexFailure("auth_expired")
            if remaining > 3600:
                raise CodexFailure("policy_refused")
            return value
        except CodexFailure:
            raise
        except Exception:
            raise CodexFailure("policy_refused") from None

    def state(self):
        value = read_private(self.control / "ledger.json")
        if value.get("trialId") != self.grant()["trialId"] or value.get("owner") != self.owner or not isinstance(value.get("requests"), list):
            raise CodexFailure("policy_refused")
        return value

    def validate_cache(self, cache):
        grant = self.grant()
        tokens = cache.get("tokens")
        if cache.get("OPENAI_API_KEY") or cache.get("auth_mode", "chatgpt") != "chatgpt" or not isinstance(tokens, dict):
            raise CodexFailure("policy_refused")
        if tokens.get("account_id") != grant["providerAccountId"] or not all(isinstance(tokens.get(k), str) and tokens[k] for k in ("id_token", "access_token", "refresh_token")):
            raise CodexFailure("policy_refused")
        self.secrets.update(tokens[k] for k in ("id_token", "access_token", "refresh_token", "account_id"))
        return cache

    def attach(self, codex_home):
        if read_private(codex_home.parent / "account-id", json_format=False).strip() != self.owner:
            raise CodexFailure("policy_refused")
        if self.control.is_relative_to(codex_home.parent.resolve()) or codex_home.parent.resolve().is_relative_to(self.control):
            raise CodexFailure("policy_refused")
        self.cache = private_directory(codex_home) / "auth.json"
        self.lease = (self.control / "lease.lock").open("a+")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            grant = self.grant()
            ledger = self.control / "ledger.json"
            if not ledger.exists():
                if (codex_home.parent / "broker.json").exists():
                    raise CodexFailure("policy_refused")  # First trial uses fresh state, never adopts an old credential/history bundle.
                write_private(ledger, {"trialId": grant["trialId"], "owner": self.owner, "requests": [], "nativeActive": False, "revoked": False})
            state = self.state()
            if state.get("nativeActive") or state.get("revoked"):
                raise CodexFailure("auth_revoked")  # Crash/restore cannot silently reactivate old cache.
            write_private(self.cache, self.validate_cache(read_private(self.control / "auth.json")))
            self.owns_cache = True
            state["nativeActive"] = True
            write_private(ledger, state)
            self.ready = True
        except CodexFailure:
            self.release()
            raise
        except Exception:
            self.release()
            raise CodexFailure("policy_refused") from None

    def check(self):
        self.grant()
        self.validate_cache(read_private(self.cache))
        state = self.state()
        if state.get("revoked"):
            raise CodexFailure("auth_revoked")
        if len(state["requests"]) >= 3:
            raise CodexFailure("budget_exhausted")

    def consume(self, request):
        self.check()
        state = self.state()
        if request in state["requests"]:
            raise CodexFailure("unavailable")  # Ledger survived but native reservation did not: ambiguous, no replay.
        state["requests"].append(request)
        write_private(self.control / "ledger.json", state)

    def permitted_tool(self, arguments):
        self.grant()
        if self.state().get("revoked"):
            raise CodexFailure("auth_revoked")
        # Native may have rotated its private tokens. Refresh redaction knowledge.
        self.validate_cache(read_private(self.cache))
        if any(secret in json.dumps(arguments, ensure_ascii=False) for secret in self.secrets):
            raise CodexFailure("policy_refused")

    def sanitize(self, value):
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, list):
            return [self.sanitize(entry) for entry in value]
        if isinstance(value, dict):
            return {self.sanitize(key): self.sanitize(entry) for key, entry in value.items()}
        return value

    def close(self):
        was_ready = self.ready
        try:
            if self.ready:
                state = self.state()
                # Only this approved private export is updated, never host auth.
                write_private(self.control / "auth.json", self.validate_cache(read_private(self.cache)))
                state["nativeActive"] = False
                write_private(self.control / "ledger.json", state)
        except Exception:
            pass  # Leave nativeActive=true: reconciliation required, no old-cache restart.
        finally:
            if self.cache and self.owns_cache:
                self.cache.unlink(missing_ok=True)
                self.owns_cache = False
            self.release()
            self.ready = False
            if was_ready:
                self.secrets.clear()

    def release(self):
        if self.lease:
            self.lease.close()
            self.lease = None


def cleanup(control, native_root=None):
    directory = private_directory(Path(control))
    grant = read_private(directory / "grant.json")
    grant["revoked"] = True
    write_private(directory / "grant.json", grant)
    # Control tombstone/dispatch ledger survive cleanup and native-home restore.
    with (directory / "lease.lock").open("a+") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (directory / "auth.json").unlink(missing_ok=True)
        if native_root:
            root = private_directory(Path(native_root))
            if read_private(root / "account-id", json_format=False).strip() != grant["platformAccountId"]:
                raise CodexFailure("policy_refused")
            home = root / "codex"
            if home.exists() or home.is_symlink():
                (private_directory(home) / "auth.json").unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Explicit private personal-cache validation/cleanup; never discovers host login")
    parser.add_argument("--control", required=True)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--native-root")
    args = parser.parse_args()
    try:
        if args.validate == args.cleanup:
            raise CodexFailure("policy_refused")
        control = private_directory(Path(args.control))
        if args.cleanup:
            cleanup(control, args.native_root)
        else:
            manifest = read_private(control / "grant.json")
            candidate = PersonalAuth(control, manifest["platformAccountId"])
            candidate.grant()
            candidate.validate_cache(read_private(control / "auth.json"))
    except Exception:
        print("Private personal-cache procedure failed; input values are not printed", file=sys.stderr)
        sys.exit(1)
    print("PASS private personal-cache structure/cleanup; no provider authentication or model call")
