"""Public turn research API. DB and runner status never own overlapping checkouts."""
from datetime import datetime, timezone
import time

from fastapi import Request
from pydantic import ValidationError
from sqlalchemy import select, text

from .models import Account, LoginSession
from .research import Store, ResearchError, fail, mutation_lock, parse_id
from .research_models import Conversation
from .research_tools import TOOLS, dispatch
from .page_scope import request_metadata, tool_allowed
from .research_credentials import locate, resolve, redact, reject_token_input
from . import research_api_budget as budget
from . import research_receipts as receipts
from .version_authors import VersionAuthor

OPERATIONS = frozenset(TOOLS) - {'research_page_context'}


def require_schema(connection):
    from .research_schema_contract import validate_research
    try:
        revision = validate_research(connection)
    except RuntimeError:
        fail('not_ready', 'Research schema is not ready', 503)
    if revision != '0008_version_authors':
        fail('not_ready', 'Research schema is not ready', 503)


def operation_failure(error):
    """Preserve known domain statuses/details; unknown categories fail closed."""
    known = isinstance(error, ResearchError) and error.code in receipts.ERRORS
    reason = error.code if known else 'not_ready' if isinstance(error, ResearchError) else 'validation_error'
    code = error.status if known else 503 if isinstance(error, ResearchError) else 422
    if reason == 'not_ready':
        code = 503
    result = {'error': {'code': reason, 'message': 'Research operation failed'}}
    if known and error.details is not None:
        result['error']['details'] = redact(error.details)
    return result, code


def current_binding(db, owner, result):
    """Fresh SQL only, supplied connection. Never Registry._current/RPC/checkout."""
    from .runner_registry import Registry
    from .runner_termination_control import stop_for
    from runner.lifecycle import Phase, require
    state = Registry(db).read(owner)
    require(state is not None and state.phase in {Phase.READY, Phase.UNREACHABLE}
            and result.get('ownerId') == str(owner) and result.get('stateId') == str(state.state_id)
            and result.get('podUid') == str(state.pod_uid) and state.pod_uid is not None
            and type(result.get('generation')) is int and result['generation'] == state.generation
            and stop_for(db, state) is None)
    return state


def bearer(request):
    values = request.headers.getlist('authorization')
    if len(values) != 1 or not values[0].startswith('Bearer '):
        fail('unauthenticated', 'Research authentication required', 401)
    value = values[0][7:]
    locate(value)
    return value


def register(app, sessions, settings, response, runner_for):
    budget.install(app.state.engine)

    @app.post(settings.base_path + '/api/research/agent/v1/{operation}')
    def call(operation: str, body: dict, request: Request):
        if not app.state.research_agent_api_enabled:
            fail('not_found', 'Not found', 404)
        if operation not in OPERATIONS:
            fail('tool_not_allowed', 'Research operation is unavailable', 403)
        token = bearer(request)
        reject_token_input(body)
        conversation, request_id = locate(token)
        with budget.authorization() as deadline:
            budget.checkout()
            # 1. Resolve a candidate, then return its connection before any RPC.
            with sessions.begin() as db:
                row = db.scalar(select(Conversation).where(Conversation.id == conversation))
                if row is None:
                    fail('unauthenticated', 'Research authentication required', 401)
                principal = resolve(row, token, request_metadata(row, request_id))
            if not app.state.user_runners_enabled:
                fail('not_ready', 'Research runner transport is unavailable', 503)
            # 2. Service-authenticated live binding/status. The context closes
            # before the final Session/connection is acquired. No status cache.
            try:
                runner = runner_for(principal.owner)
                observed_at = time.monotonic()
                status = runner.research_authorize(conversation, request_id)
                if (status.get('authorized') is not True or status.get('sessionId') != str(conversation)
                        or status.get('requestId') != str(request_id)
                        or not isinstance(status.get('nativeTurnId'), str)
                        or not 1 <= len(status['nativeTurnId']) <= 200):
                    raise ValueError()
            except ResearchError:
                raise
            except Exception:
                fail('research_turn_inactive', 'Research turn could not be authorized', 401)
            budget.checkout()
            # 3. New Session, fresh SQL under locks. No RPC or extra checkout.
            with sessions.begin() as db:
                account = db.scalar(select(Account).where(Account.id == principal.owner).with_for_update(read=True))
                if operation in receipts.MUTATIONS:
                    key = parse_id(body.get('mutationId'))
                    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                               {'key': mutation_lock(principal.owner, key)})
                row = db.scalar(select(Conversation).where(Conversation.id == conversation).with_for_update())
                if row is None:
                    fail('unauthenticated', 'Research authentication required', 401)
                metadata = request_metadata(row, request_id)
                checked = resolve(row, token, metadata, datetime.now(timezone.utc))
                login = db.scalar(select(LoginSession).where(LoginSession.token_hash == row.login_hash,
                    LoginSession.account_id == checked.owner,
                    LoginSession.expires_at > datetime.now(timezone.utc)))
                if (checked != principal or account is None or account.is_approved is not True
                        or account.role not in {'editor', 'admin'} or not login
                        or app.state.research_policy == 'pending'):
                    fail('forbidden', 'Research permission was revoked', 403)
                if not tool_allowed(metadata['scope'], operation, body):
                    fail('tool_not_allowed', 'Research operation is unavailable', 403)
                try:
                    runner.research_binding(db.connection(), status)
                except ResearchError:
                    raise
                except Exception:
                    fail('research_turn_inactive', 'Research runner binding changed', 401)
                require_schema(db.connection())
                receipts.ensure_capacity(row, request_id)
                resolve(row, token, metadata, datetime.now(timezone.utc))
                if login.expires_at <= datetime.now(timezone.utc):
                    fail('unauthenticated', 'Research authentication expired', 401)
                deadline.remaining()
                if time.monotonic() - observed_at > 5:
                    fail('research_authorization_stale', 'Research authorization timed out', 503)
                # No external calls beyond this authorization point. Keep DB
                # statements/locks bounded during the action as well.
                deadline.end = time.monotonic() + 30
                store = Store(db, checked.owner, author=VersionAuthor(checked.conversation))
                code = 200
                try:
                    with db.begin_nested():
                        result = dispatch(store, operation, body)
                        value = receipts.receipt(operation, result, body, request_id,
                                                 turn_id=status['nativeTurnId'])
                except (ResearchError, ValidationError) as error:
                    result, code = operation_failure(error)
                    value = receipts.receipt(operation, None, {}, request_id, code=result['error']['code'],
                                             turn_id=status['nativeTurnId'])
                receipts.append(row, value)
                db.flush()
            # The action/receipt outer transaction committed before success leaves.
            return response(result, code)
