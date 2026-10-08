"""Approved admin's constrained membership UI API, without operator credentials."""
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import Request
from pydantic import Field, field_validator
from sqlalchemy import select, text

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


def register_membership(app, authorized, settings, response, write):
    root = settings.base_path + "/api/admin/accounts"

    def require_admin(store):
        if store.db.get(Account, store.owner).role != "admin":
            fail("forbidden", "Administrator access required", 403)
        require_publication_schema(store.db)

    @app.get(root)
    def listing(request: Request, limit: int = 30, cursor: str | None = None):
        with authorized(request) as store:
            require_admin(store)
            rows = store.db.scalars(page_query(select(Account), Account, limit, cursor)).all()
            return response({"items": [account_detail(row) for row in rows[:limit]],
                             "nextCursor": encode_cursor(rows[limit - 1]) if len(rows) > limit else None})

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
