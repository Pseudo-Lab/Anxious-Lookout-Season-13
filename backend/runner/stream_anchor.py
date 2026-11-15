"""#95 route 1 (design r4.2, review-approved): the operator's one-shot anchor for the retired-literal holdback.

Run by an operator, once per owner, inside that owner's runner Pod (`kubectl exec … -- python -m runner.stream_anchor`)
after the #95 rollout with `stream.retired: true`. The runner itself never sends `attest`; this command is the only
caller, and it makes **at most one** broker request per run, with no retry.

Steps:
1. Reserve: with the sidecar lock, the sidecar must be valid (owner / trial / MAC) and hold no anchor; its
   `anchorAttempts` is incremented and written before anything is sent (a record of operator runs). Otherwise nothing
   is requested.
2. One request: `refresh: true, attest: true` with this Pod's projected broker token (the same identity as the runner's
   own pulls). A refused, failed, late or unattested answer leaves the owner `pending`.
3. Record: the token's `iat` becomes the anchor only if the sidecar is still the same epoch with no anchor.
The returned token is not installed here; the runner takes the broker's current token on its next pull.

Output: one JSON line `{"result": ..., "reason": ...}` from fixed lists - never a token, claim, id or path.
Exit codes: 0 anchored or already anchored; 3 nothing requested (no record, a configuration problem); 4 the request
was made but no anchor was recorded (refused, failed, not attested, sidecar changed). Retry only after checking why
(B4); runs are spaced at least 60 s apart across owners.
"""
import json
import os
import sys
from pathlib import Path

RESULTS = {"anchored", "already", "pending", "refused"}
REASONS = {None, "configuration", "no_record", "record_write_failed", "request_failed", "not_attested", "no_iat", "off",
           "lock_failed",
           "epoch_changed", "already_anchored", "claims_refused"}


def say(result, reason=None):
    print(json.dumps({"result": result if result in RESULTS else "refused",
                      "reason": reason if reason in REASONS else "other"}, sort_keys=True), flush=True)


def main(environ=os.environ, client_factory=None):
    from app.codex_policy import CodexFailure
    from .access import claims
    from .auth import read_private
    from .broker_client import BrokerClient
    from .service_access import ServiceAccessAuth, ServiceJournal
    from . import stream_retired
    try:
        if environ.get("CODEX_AUTH_MODE") != "access-service":
            raise ValueError()
        owner, state = environ["RUNNER_ACCOUNT_ID"], environ["RUNNER_STATE_ID"]
        control = Path(environ["CODEX_ACCESS_CONTROL_DIR"])
        account = read_private(Path(environ["CODEX_OPERATOR_ACCOUNT_FILE"]), json_format=False).strip()
        auth = ServiceAccessAuth(control, owner, state, account)
        with auth.store.transaction() as db:
            journal = ServiceJournal(auth, auth._state(db))
        client = (client_factory or BrokerClient)(environ["BROKER_URL"], environ["BROKER_TOKEN_FILE"],
                                                  auth.owner, auth.state_id)
    except Exception:
        say("refused", "configuration")
        return 3
    epoch, why = stream_retired.reserve_anchor(control, journal)
    if epoch is None:
        if why == "already_anchored":
            say("already", None)
            return 0
        say("refused", why)
        return 3
    try:
        envelope = client.fetch(refresh=True, attest=True)  # the one request of this run; never retried here
    except (CodexFailure, Exception):
        say("pending", "request_failed")
        return 4
    if envelope.get("refreshedForRequest") is not True:
        say("pending", "not_attested")
        return 4
    try:
        value, _ = claims(envelope["accessToken"])
    except Exception:
        say("pending", "claims_refused")
        return 4
    outcome = stream_retired.record_anchor(control, journal, epoch, value.get("iat"))
    if outcome == "anchored":
        say("anchored", None)
        return 0
    say("pending", outcome if outcome in REASONS else None)
    return 4


if __name__ == "__main__":
    sys.exit(main())
