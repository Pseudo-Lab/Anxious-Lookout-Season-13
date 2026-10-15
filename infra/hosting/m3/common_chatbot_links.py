"""Offline DB/native binding comparison. No database/network/native calls."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from common_chatbot_prepare import HISTORICAL_PAIR_SCOPE, identity_digest, require, validate_protected

DEFINITE_FAILURES = {"codex_rejected", "codex_auth_expired", "codex_auth_revoked", "codex_budget_exhausted",
                     "codex_policy_refused", "codex_model_unavailable", "codex_rate_limited", "codex_not_enabled_for_account"}


def compare(before, after, native, preservation, current):
    now = datetime.now(timezone.utc)
    dates = [datetime.fromisoformat(value["checkedAt"].replace("Z", "+00:00")) for value in (before, after, native)]
    require(all(date.tzinfo is not None and date <= now for date in dates))
    require(dates[0] <= dates[1] and all((now - date).total_seconds() <= 120 for date in dates[1:]))
    require(before["admissionHeld"] is True and after["admissionHeld"] is True)
    require({key: value for key, value in before.items() if key != "checkedAt"} ==
            {key: value for key, value in after.items() if key != "checkedAt"})
    require(after["ownerDigest"] == native["ownerDigest"] and after["stateDigest"] == native["stateDigest"])
    validate_protected(preservation["protectedState"])
    require(native["protectedState"] == preservation["protectedState"] and native["ownerStateVerified"] is True)
    require(type(native["nativeRunning"]) is int and native["nativeRunning"] == 0
            and type(native["ambiguous"]) is int and native["ambiguous"] == 0)
    require(type(native["lifetimeReservations"]) is int and native["lifetimeReservations"] == preservation["lifetimeReservations"])
    db = {row["session"]: row for row in after["sessions"]}
    broker = {row["session"]: row for row in native["sessions"]}
    require(len(db) == len(after["sessions"]) and len(broker) == len(native["sessions"]))
    require(set(broker) <= set(db))
    definite = 0
    latest_verified, latest_absent, latest_unrecorded, historical = 0, 0, 0, 0
    for session, row in db.items():
        require(row["state"] in {"idle", "failed"})
        entry = broker.get(session)
        if entry is None:
            require(row["thread"] is None and row["turns"] == [])
            require(row["request"] is None or (row["state"] == "failed" and row["error"] in DEFINITE_FAILURES))
            require(not any(item["turn"] is not None for item in row["platformRequests"]))
            definite += row["request"] is not None
            continue
        require(row["thread"] == entry["thread"])
        require(row["turns"] == entry["turns"])
        if row["request"] in entry["requests"]:
            require(row["request"] == entry["request"] and row["state"] == entry["state"])
        else:
            require(row["state"] == "failed" and row["error"] in DEFINITE_FAILURES)
            definite += 1
        require(len({item["request"] for item in row["platformRequests"]}) == len(row["platformRequests"]))
        latest = [item for item in row["platformRequests"] if item["request"] == entry["request"]]
        if not latest:
            # Legacy DB records may have no per-request turn metadata. Preserve
            # them explicitly without claiming independent pair verification.
            latest_absent += 1
        elif latest[0]["turn"] is not None:
            require(latest[0]["turn"] == entry["turn"] and entry["turn"] in entry["turns"])
            latest_verified += 1
        else:
            require(entry["state"] == "failed" and (entry["turn"] is None or entry["turn"] not in entry["turns"]))
            latest_unrecorded += 1
        for item in row["platformRequests"]:
            if item["turn"] is not None:
                require(item["request"] in entry["requests"] and item["turn"] in entry["turns"])
                historical += item["request"] != entry["request"]
    return {"dbNativeLinksVerified": True, "currentDigest": identity_digest(current),
            "ownerDigest": after["ownerDigest"], "stateDigest": after["stateDigest"],
            "checkedAt": min(dates[1:]).isoformat(), "protectedState": preservation["protectedState"],
            "lifetimeReservations": preservation["lifetimeReservations"], "dbSessions": len(db),
            "nativeSessions": len(broker), "definiteUnreservedFailures": definite,
            "latestRecordedPairsVerified": latest_verified, "latestRequestsWithoutPlatformMetadata": latest_absent,
            "latestRequestsWithoutRecordedTurn": latest_unrecorded, "historicalRecordedPairsPreserved": historical,
            "historicalPairVerification": HISTORICAL_PAIR_SCOPE}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before", "after", "native", "preservation", "current"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        values = [json.loads(Path(getattr(args, name)).read_text()) for name in ("before", "after", "native", "preservation", "current")]
        print(json.dumps(compare(*values)))
    except Exception:
        parser.exit(1, "DB/native link comparison refused; private values not printed\n")


if __name__ == "__main__":
    main()
