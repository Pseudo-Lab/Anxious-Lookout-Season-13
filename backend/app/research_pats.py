"""Purpose-separated PAT authentication and bounded, separately committed admission."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from uuid import UUID, uuid4

from sqlalchemy import select
from .models import Account
from .pat_models import Credential
from .research import fail, stamp
from . import research_api_budget as budget

PURPOSE = 'research-pat-v1'
TOKEN = re.compile(r'rpat1\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}')
READS = frozenset({'research_list', 'research_get', 'research_versions', 'research_version_get',
                   'research_relations', 'research_post_draft_get', 'research_lint'})


def now():
    return datetime.now(timezone.utc)


def token_hash(token):
    return hashlib.sha256((PURPOSE + '\0' + token).encode()).hexdigest()


def locate(token):
    try:
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError()
        identity = token.split('.')[1]
        if str(UUID(identity)) != identity:
            raise ValueError()
        return UUID(identity)
    except (ValueError, TypeError, AttributeError):
        fail('unauthenticated', 'Research authentication required', 401)


def issue_value(identity):
    return 'rpat1.' + str(identity) + '.' + secrets.token_urlsafe(32)


def effective(account):
    if account is None or not account.is_approved:
        return 'none'
    if account.role == 'commenter':
        return 'public'
    return 'write' if account.role in {'editor', 'admin'} else 'none'


def value(row, account):
    return {'id': str(row.id), 'name': row.name,
            'issuedAt': stamp(row.created_at), 'expiresAt': stamp(row.expires_at),
            'revokedAt': stamp(row.revoked_at) if row.revoked_at else None,
            'lastUsedAt': stamp(row.last_used_at) if row.last_used_at else None}


@dataclass(frozen=True)
class Principal:
    owner: UUID
    credential: UUID
    name: str
    scope: str


def resolve(row, token, account, at=None):
    at = at or now()
    if (row is None or row.id != locate(token) or row.purpose != PURPOSE
            or not hmac.compare_digest(row.token_hash, token_hash(token))
            or row.revoked_at is not None or row.expires_at <= at):
        fail('unauthenticated', 'Research authentication required', 401)
    if account is None or account.id != row.owner_id:
        fail('unauthenticated', 'Research authentication required', 401)
    return Principal(row.owner_id, row.id, row.name, effective(account))


def locked(db, token, owner):
    account = db.scalar(select(Account).where(Account.id == owner).with_for_update(read=True)
                        .execution_options(populate_existing=True))
    row = db.scalar(select(Credential).where(Credential.id == locate(token), Credential.owner_id == owner)
                    .with_for_update().execution_options(populate_existing=True))
    return account, row, resolve(row, token, account, now())


def observe(sessions, token):
    """Return all connections and commit counters before action begins.

    Wrong hash never writes quota/lastUsed. Any SQL failure propagates fail-closed.
    Account -> credential is shared with action/revoke; no runner RPC occurs here.
    """
    identity = locate(token)
    budget.checkout()
    with sessions.begin() as db:
        candidate = db.scalar(select(Credential.owner_id).where(Credential.id == identity))
        if candidate is None:
            fail('unauthenticated', 'Research authentication required', 401)
        _, row, principal = locked(db, token, candidate)
        at = now()
        if row.rate_window is None or at >= row.rate_window + timedelta(minutes=1):
            row.rate_window, row.rate_count = at, 0
        if row.rate_count >= 60:
            fail('rate_limited', 'Research request limit reached', 429)
        row.rate_count += 1
        row.last_used_at = at
        db.flush()
    return principal


def allowed(principal, operation, mutations):
    if principal.scope != 'write':
        fail('forbidden', 'Private research is unavailable for this role', 403)
    if operation not in READS and operation not in mutations:
        fail('forbidden', 'Operation not permitted', 403)
