"""Approved admin's constrained membership UI API, without operator credentials."""
import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import Request
from pydantic import Field, field_validator
from sqlalchemy import func, select, text

from .models import Account
from .publications import require_publication_schema
from .research import Input, fail, page_query, encode_cursor, stamp


class Membership(Input):
    role: Literal["editor", "commenter"]
    isApproved: bool
    reason: str = Field(min_length=1, max_length=4000)
    expectedVersion: str

    @field_validator("role", mode="before")
    @classmethod
    def forbid_admin_assignment(cls, value):
        if value == "admin":
            fail("forbidden", "Administrator assignment is operator-only", 403)
        return value

    @field_validator("expectedVersion")
    @classmethod
    def version(cls, value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            return stamp(parsed.astimezone(timezone.utc))
        except (ValueError, OverflowError):
            raise ValueError("A timezone-aware account version is required") from None

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError("An audit reason is required")
        return value.strip()


def account_detail(row):
    return {"accountId": str(row.id), "githubId": row.github_id, "login": row.login, "role": row.role,
            "isApproved": row.is_approved, "createdAt": stamp(row.created_at), "updatedAt": stamp(row.updated_at),
            "version": stamp(row.updated_at)}


def account_filters(approved=None, role=None, q=None):
    """#119 L1/L5: pending members first is approved=false; role and login substring filters."""
    query = select(Account)
    if approved is not None:
        query = query.where(Account.is_approved.is_(approved))
    if role is not None:
        if role not in ("commenter", "editor", "admin"):
            fail("validation_error", "Invalid role", 422)
        query = query.where(Account.role == role)
    if q is not None:
        if not 1 <= len(q) <= 100 or "\x00" in q:
            fail("validation_error", "Invalid search query", 422)
        query = query.where(Account.login.icontains(q, autoescape=True))
    return query


AUDIT = text("SELECT * FROM research.membership_audit(CAST(:cookie AS text),CAST(:origin AS text),CAST(:target AS uuid),"
             "CAST(:after_time AS timestamptz),CAST(:after_id AS uuid),CAST(:row_limit AS integer))")


def audit_page(db, cookie, origin, target, limit, cursor):
    """#119 L3: read through the reviewed definer function; the API role has no grant on auth.permission_audit."""
    from sqlalchemy.exc import DBAPIError
    if not 1 <= limit <= 100:
        fail("validation_error", "Limit must be between 1 and 100", 422)
    after_time = after_id = None
    if cursor:
        try:
            if len(cursor) > 512:
                raise ValueError()
            values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if not isinstance(values, list) or len(values) != 2 or not all(isinstance(v, str) for v in values):
                raise ValueError()
            after_time = datetime.fromisoformat(values[0].replace("Z", "+00:00"))
            if after_time.tzinfo is None:
                raise ValueError()
            after_id = uuid.UUID(values[1])
        except (ValueError, TypeError, UnicodeError, OverflowError):
            fail("validation_error", "Invalid cursor", 422)
    try:
        rows = db.execute(AUDIT, {"cookie": cookie, "origin": origin, "target": target, "after_time": after_time,
                                  "after_id": after_id, "row_limit": limit + 1}).all()
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "42501":
            fail("forbidden", "Administrator access required", 403)
        raise
    items = [audit_value(row) for row in rows[:limit]]
    following = None
    if len(rows) > limit:
        last = rows[limit - 1]
        following = base64.urlsafe_b64encode(json.dumps([stamp(last.audit_time), str(last.audit_id)]).encode()).decode().rstrip("=")
    return {"items": items, "nextCursor": following}


def audit_value(row):
    if row.actor_label.startswith("account:"):
        actor = {"kind": "account", "accountId": row.actor_label.removeprefix("account:"), "login": row.actor_login}
    else:
        actor = {"kind": "operator", "label": row.actor_label}
    return {"id": str(row.audit_id), "createdAt": stamp(row.audit_time), "actor": actor,
            "account": {"accountId": str(row.target_account_id), "login": row.target_login, "githubId": row.target_github_id},
            "reason": row.change_reason, "before": row.before_state, "after": row.after_state}


def register_membership(app, authorized, settings, response, write):
    root = settings.base_path + "/api/admin/accounts"

    def require_admin(store):
        if store.db.get(Account, store.owner).role != "admin":
            fail("forbidden", "Administrator access required", 403)
        require_publication_schema(store.db)

    @app.get(root)
    def listing(request: Request, limit: int = 30, cursor: str | None = None, approved: bool | None = None,
                role: str | None = None, q: str | None = None):
        with authorized(request) as store:
            require_admin(store)
            query = account_filters(approved, role, q)
            rows = store.db.scalars(page_query(query, Account, limit, cursor)).all()
            return response({"items": [account_detail(row) for row in rows[:limit]],
                             "nextCursor": encode_cursor(rows[limit - 1]) if len(rows) > limit else None,
                             "total": store.db.scalar(select(func.count()).select_from(query.subquery()))})

    @app.get(settings.base_path + "/api/admin/audit")
    def audit(request: Request, limit: int = 30, cursor: str | None = None, accountId: uuid.UUID | None = None):
        with authorized(request) as store:
            require_admin(store)
            return response(audit_page(store.db, request.cookies["anxious_session"], settings.origin, accountId, limit, cursor))

    @app.put(root + "/{identity}/membership")
    def change(identity: uuid.UUID, body: dict, request: Request):
        with authorized(request, True, membership=True) as store:
            require_admin(store)
            def action(parsed):
                result = store.db.execute(text("""
                    SELECT research.set_membership(:cookie,:origin,:target,:approved,:role,:expected,:reason)
                """), {"cookie": request.cookies["anxious_session"], "origin": settings.origin, "target": identity,
                        "approved": parsed.isApproved, "role": parsed.role,
                        "expected": datetime.fromisoformat(parsed.expectedVersion.replace("Z", "+00:00")), "reason": parsed.reason}).scalar_one()
                if result != "ok":
                    fail(result, "Membership change was rejected", 409 if result == "conflict" else 404 if result == "not_found" else 403)
                store.db.expire_all()
                return account_detail(store.db.get(Account, identity))
            return write(store, request, Membership, body, action)
