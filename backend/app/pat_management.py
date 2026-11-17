"""Cookie-only PAT issue/list/revoke. Issue is serialized for the whole owner."""
from contextlib import contextmanager
from datetime import timedelta
import hmac
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import func, select

from .models import Account
from .pat_models import Credential, Issuance
from .research import Input, fail, parse_id, mutation_fingerprint, page_query, encode_cursor
from .research_credentials import reject_token_input
from . import research_pats as pats
from . import research_api_budget as budget


class Create(Input):
    name: str = Field(min_length=1, max_length=80)
    scope: Literal['read', 'write'] | None = None  # Legacy wire input, no authority.
    expiryDays: int = Field(default=30, ge=1, le=90)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('Invalid PAT name')
        reject_token_input(value)
        return value


def register(app, sessions, settings, current_session, response):
    prefix = settings.base_path + '/api/research/pats'

    @contextmanager
    def managed(request, *, mutate=False, issuing=False):
        from .research_api import require_schema
        if not request.cookies.get('anxious_session'):
            fail('unauthenticated', 'Authentication required', 401)
        with budget.authorization():
            budget.checkout()
            with sessions.begin() as db:
                login = current_session(db, request)
                if login is None:
                    fail('unauthenticated', 'Authentication required', 401)
                account = db.scalar(select(Account).where(Account.id == login.account_id)
                    .with_for_update(read=not issuing).execution_options(populate_existing=True))
                login = current_session(db, request)
                if account is None or login is None or login.expires_at <= pats.now():
                    fail('unauthenticated', 'Authentication required', 401)
                if mutate:
                    if request.headers.getlist('origin') != [settings.origin]:
                        fail('origin_not_allowed', 'Origin not allowed', 403)
                    values = request.headers.getlist('x-csrf-token')
                    if len(values) != 1 or not hmac.compare_digest(values[0], login.csrf_token):
                        fail('csrf_invalid', 'Invalid CSRF token', 403)
                if issuing:
                    if not account.is_approved:
                        fail('forbidden', 'Account approval required', 403)
                    if not app.state.research_agent_api_enabled or app.state.research_policy == 'pending':
                        fail('not_ready', 'Research API is unavailable', 503)
                require_schema(db.connection())
                yield db, account

    @app.post(prefix)
    def create(body: dict, request: Request):
        with managed(request, mutate=True, issuing=True) as (db, account):
            keys = request.headers.getlist('idempotency-key')
            if len(keys) != 1:
                fail('validation_error', 'Idempotency-Key UUID required', 422)
            key = parse_id(keys[0])
            if str(key) != keys[0]:
                fail('validation_error', 'Canonical Idempotency-Key UUID required', 422)
            parsed = Create.model_validate(body)
            payload = parsed.model_dump(exclude={'scope'})
            # Keep the historical hash format for image rollback. Scope is
            # ignored as authority and when matching an identical retry.
            fingerprints = {scope: mutation_fingerprint('pat:create', {**payload, 'scope': scope})
                            for scope in ('read', 'write')}
            fingerprint = fingerprints[parsed.scope or 'write']
            previous = db.get(Issuance, (account.id, key))
            if previous is not None:
                if previous.fingerprint not in fingerprints.values():
                    fail('idempotency_conflict', 'Idempotency key already used', 409)
                row = db.get(Credential, previous.credential_id)
                return response({'credential': pats.value(row, account), 'token': None,
                                 'secretAvailable': False, 'replayed': True})
            at = pats.now()
            active = db.scalar(select(func.count()).select_from(Credential).where(
                Credential.owner_id == account.id, Credential.revoked_at.is_(None), Credential.expires_at > at))
            if active >= 10:
                fail('pat_limit', 'Active PAT limit reached', 409)
            identity = uuid4()
            token = pats.issue_value(identity)
            row = Credential(id=identity, owner_id=account.id, name=parsed.name, scope='write',
                purpose=pats.PURPOSE, token_hash=pats.token_hash(token), created_at=at,
                expires_at=at + timedelta(days=parsed.expiryDays), rate_count=0)
            db.add(row)
            db.flush()
            db.add(Issuance(owner_id=account.id, key=key, fingerprint=fingerprint, credential_id=identity))
            db.flush()
            result = {'credential': pats.value(row, account), 'token': token,
                      'secretAvailable': True, 'replayed': False}
        # Expose the one-time value only after commit. No response-cache stores it.
        # This single exact DTO is the intentional plaintext disclosure. The
        # ordinary research response redactor must mask all other occurrences.
        return JSONResponse(result, status_code=201, headers={'Cache-Control': 'no-store'})

    @app.get(prefix)
    def listing(request: Request, limit: int = 30, cursor: str | None = None):
        with managed(request) as (db, account):
            query = select(Credential).where(Credential.owner_id == account.id)
            rows = db.scalars(page_query(query, Credential, limit, cursor, time_field='created_at')).all()
            return response({'items': [pats.value(row, account) for row in rows[:limit]],
                             'nextCursor': encode_cursor(rows[limit-1], time_field='created_at') if len(rows) > limit else None})

    @app.delete(prefix + '/{identity}')
    def revoke(identity: UUID, request: Request):
        with managed(request, mutate=True) as (db, account):
            row = db.scalar(select(Credential).where(Credential.id == identity, Credential.owner_id == account.id)
                            .with_for_update().execution_options(populate_existing=True))
            if row is None:
                fail('not_found', 'Not found', 404)
            if row.revoked_at is None:
                row.revoked_at = pats.now()
            result = {'credential': pats.value(row, account)}
        return response(result)
