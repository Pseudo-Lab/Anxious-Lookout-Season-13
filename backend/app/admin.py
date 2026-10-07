import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import delete, select

from .database import database
from .models import Account, LoginSession, PermissionAudit
from .settings import secret


def change_permissions(url, github_id, approved, role, actor, reason):
    if not github_id.isdecimal() or int(github_id) <= 0 or role not in {"commenter", "editor", "admin"} or not actor.strip() or not reason.strip():
        raise ValueError("Verified GitHub ID, actor and reason are required")
    engine, sessions = database(url)
    try:
        with sessions.begin() as db:
            account = db.scalar(select(Account).where(Account.github_id == github_id).with_for_update())
            if not account:
                raise ValueError("Account must first complete GitHub identification")
            before = {"role": account.role, "isApproved": account.is_approved}
            account.role, account.is_approved = role, approved
            account.updated_at = datetime.now(timezone.utc)
            db.add(PermissionAudit(actor=actor, account_id=account.id, reason=reason, before=before, after={"role": role, "isApproved": approved}))
            db.execute(delete(LoginSession).where(LoginSession.account_id == account.id))
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Operator-only verified account approval/role change; invalidates all account sessions")
    parser.add_argument("--github-id", required=True)
    parser.add_argument("--approved", choices=["true", "false"], required=True)
    parser.add_argument("--role", choices=["commenter", "editor", "admin"], required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        change_permissions(secret("ADMIN_DATABASE_URL"), args.github_id, args.approved == "true", args.role, args.actor, args.reason)
    except Exception:
        print("Permission change failed; no credentials or database details printed", file=sys.stderr)
        return 1
    print("Permission change audited; prior account sessions invalidated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
