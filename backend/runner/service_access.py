"""Selected operator access snapshots, with real expiry and no trial window.

This is the explicitly chosen experimental auth path, not official support or
automatic source renewal. It never reads the original cache or exports refresh
tokens. Supply remains the existing local same-UID ingress. Legacy trial state
is not silently adopted: its guarded migration is a separate operator procedure.
"""
import hashlib
import time
import uuid

from app.codex_policy import CodexFailure, REASONS
from .access import AccessAuth, claims
from .auth import read_private, write_private
from .binding import canonical_uuid

MODE = "operator-access-snapshot"
LEDGER = "service-access-ledger.json"
FIELDS = {"version", "mode", "owner", "stateId", "accountId", "trialId", "requests",
          "snapshots", "snapshotId", "snapshotFingerprint", "rejectedFingerprints",
          "deadline", "blocked", "modelBlocked"}


def refuse(condition):
    if not condition:
        raise CodexFailure("policy_refused")


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def is_fingerprint(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


class ServiceAccessAuth(AccessAuth):
    service = True

    def __init__(self, control, owner, state_id, expected_account):
        super().__init__(control, owner, state_id)
        refuse(isinstance(expected_account, str) and 1 <= len(expected_account) <= 200)
        self.expected_account = expected_account

    def state(self):
        value = read_private(self.control / LEDGER)
        refuse(isinstance(value, dict) and set(value) == FIELDS and type(value["version"]) is int
               and value["version"] == 2 and value["mode"] == MODE
               and value["owner"] == self.owner and value["stateId"] == self.state_id
               and value["accountId"] == self.expected_account)
        canonical_uuid(value["trialId"])  # Stable existing projection context, not a trial budget.
        refuse(isinstance(value["requests"], list))
        ids = []
        for item in value["requests"]:
            refuse(isinstance(item, dict) and set(item) == {"id"})
            ids.append(canonical_uuid(item["id"]))
        refuse(len(ids) == len(set(ids)))
        refuse(isinstance(value["snapshots"], dict))
        for identity, digest in value["snapshots"].items():
            canonical_uuid(identity)
            refuse(is_fingerprint(digest))
        refuse(isinstance(value["rejectedFingerprints"], list)
               and all(is_fingerprint(item) for item in value["rejectedFingerprints"])
               and len(set(value["rejectedFingerprints"])) == len(value["rejectedFingerprints"]))
        refuse(value["blocked"] is None or (isinstance(value["blocked"], str) and value["blocked"] in REASONS))
        refuse(type(value["modelBlocked"]) is bool)
        if value["snapshotId"] is None:
            refuse(value["deadline"] is None and value["snapshotFingerprint"] is None and not value["snapshots"])
        else:
            identity = canonical_uuid(value["snapshotId"])
            refuse(type(value["deadline"]) is int and is_fingerprint(value["snapshotFingerprint"])
                   and value["snapshots"].get(identity) == value["snapshotFingerprint"])
        return value

    def install(self, envelope):
        expected = {"version", "ownerId", "stateId", "accountId", "accessToken", "expiresAt", "snapshotId"}
        refuse(isinstance(envelope, dict) and set(envelope) == expected
               and type(envelope["version"]) is int and envelope["version"] == 2
               and envelope["ownerId"] == self.owner and envelope["stateId"] == self.state_id)
        identity = canonical_uuid(envelope["snapshotId"])
        value, account = claims(envelope["accessToken"])
        refuse(account == self.expected_account == envelope["accountId"]
               and type(envelope["expiresAt"]) is int and envelope["expiresAt"] == value["exp"])
        digest = fingerprint(envelope["accessToken"])
        path = self.control / LEDGER
        if path.exists():
            ledger = self.state()
        else:
            # Never change the projection identity of existing native/trial history.
            refuse(not (self.control / "access-ledger.json").exists()
                   and not (self.control / "access-projection.json").exists())
            ledger = {"version": 2, "mode": MODE, "owner": self.owner, "stateId": self.state_id,
                      "accountId": account, "trialId": str(uuid.uuid4()), "requests": [],
                      "snapshots": {}, "snapshotId": None, "snapshotFingerprint": None,
                      "rejectedFingerprints": [], "deadline": None, "blocked": None, "modelBlocked": False}
        if ledger["modelBlocked"]:
            raise CodexFailure("model_unavailable")
        if ledger["blocked"] in {"policy_refused", "provider_rate_limited", "model_unavailable"}:
            raise CodexFailure(ledger["blocked"])  # A credential copy is not a policy/quota release.
        if digest in ledger["rejectedFingerprints"]:
            raise CodexFailure("auth_revoked")
        if identity in ledger["snapshots"]:
            refuse(identity == ledger["snapshotId"] and ledger["snapshots"][identity] == digest)
        ledger["snapshots"][identity] = digest
        ledger.update(snapshotId=identity, snapshotFingerprint=digest,
                      deadline=value["exp"] - 60, blocked=None)
        write_private(path, ledger)
        self.snapshot = {"accessToken": envelope["accessToken"], "chatgptAccountId": account}
        plan = value.get("https://api.openai.com/auth", {}).get("chatgpt_plan_type")
        if isinstance(plan, str):
            self.snapshot["chatgptPlanType"] = plan
        self.secrets.update((envelope["accessToken"], account))
        if isinstance(value.get("email"), str) and value["email"]:
            self.secrets.add(value["email"])
        self.remember_secrets()

    def check(self, budget=True):
        if not self.snapshot:
            raise CodexFailure("not_configured")
        value = self.state()
        if time.time() >= value["deadline"]:
            raise CodexFailure("auth_expired")
        if value["modelBlocked"]:
            raise CodexFailure("model_unavailable")
        if value["blocked"]:
            raise CodexFailure(value["blocked"])
        # `budget` remains compatible with the Native interface; no count/window gate.

    def consume(self, request):
        request = canonical_uuid(request)
        self.check()
        value = self.state()
        if any(item["id"] == request for item in value["requests"]):
            raise CodexFailure("unavailable")  # Preserve uncertainty/dedup across resupply/restart.
        value["requests"].append({"id": request})
        write_private(self.control / LEDGER, value)

    def block(self, reason):
        refuse(isinstance(reason, str) and reason in REASONS)
        value = self.state()
        value["blocked"] = reason
        if reason in {"auth_expired", "auth_revoked"}:
            refuse(is_fingerprint(value["snapshotFingerprint"]))
        if reason in {"auth_expired", "auth_revoked"} and value["snapshotFingerprint"] not in value["rejectedFingerprints"]:
            value["rejectedFingerprints"].append(value["snapshotFingerprint"])
        write_private(self.control / LEDGER, value)

    def block_model(self):
        value = self.state()
        value["modelBlocked"] = True
        write_private(self.control / LEDGER, value)

    def status(self):
        return {}  # No trial remainingAdmissions/accessExpiresAt fields in service mode.
