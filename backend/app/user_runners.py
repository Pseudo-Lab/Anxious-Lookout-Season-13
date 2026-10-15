"""Per-account HTTP preparation and read-only runtime health, trial compatible.

Registry phase is lifecycle evidence, not provider readiness. An explicitly loaded
operator binding is required before controller creation or native transport.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hmac
import os
import httpx
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .codex_policy import MODEL, RUNNER_PREFLIGHT_ERRORS, ERRORS
from .models import Account
from .research import fail
from .runner_registry import Registry
from .runner_schema import validate
from runner.lifecycle import Phase, Refused


def feature_enabled():
    value = os.getenv('CODEX_USER_RUNNERS_ENABLE', 'false')
    if value not in {'false', 'true'}:
        raise ValueError('Invalid user runner enable flag')
    return value == 'true'


def utc_now():
    return datetime.now(timezone.utc)


def eligible(account, policy):
    return bool(account and account.is_approved and account.role in {'editor', 'admin'} and policy != 'pending')


def can_prepare(app, account):
    return bool(account and selected(app, account.id) and eligible(account, app.state.research_policy))


def selected_account():
    value = os.getenv('CODEX_USER_RUNNER_ACCOUNT_ID', '')
    if not value:
        return None
    owner = UUID(value)
    if str(owner) != value:
        raise ValueError('Runner account must be a canonical UUID')
    return owner


def selected(app, owner):
    return bool(app.state.user_runners_enabled and owner == app.state.user_runner_account)


def status_body(state=None, *, permitted=True, creation_reserved=False):
    result = {'available': False, 'reason': 'not_prepared', 'verification': 'unverified', 'model': MODEL}
    if not permitted:
        return {**result, 'reason': 'not_enabled_for_account'}
    if state is None:
        return result
    # No trusted service transport is connected in this HTTP source chunk.
    # A ready Pod cannot establish actual authentication/model availability.
    reason = {Phase.READY: 'runner_unreachable', Phase.STOPPING: 'runner_unreachable'}.get(state.phase, state.phase.value)
    if state.pod_uid is None and creation_reserved:
        reason = 'runner_unreachable'
    result['reason'] = reason
    hint = {'provisioning': 2, 'capacity_wait': 15, 'runner_unreachable': 5}.get(reason)
    if hint is not None:
        result['retryAfterSeconds'] = hint
    return result


def checked_registry(db):
    from .research_schema_contract import validate_research
    try:
        validate_research(db.connection())
        validate(db.connection())
    except RuntimeError:
        fail('not_ready', 'Runner state is not ready', 503)
    return Registry(db)


def preflight(store):
    status = registry_status(checked_registry(store.db), store.owner)
    fail(RUNNER_PREFLIGHT_ERRORS.get(status['reason'], 'codex_unavailable'), 'Codex cannot accept this request', 503)


def registry_status(registry, owner, state=None):
    state = registry.read(owner) if state is None else state
    return status_body(state, creation_reserved=registry.creation_reserved(state))


def live_status(app, owner, fallback):
    runtime = app.state.user_runner_runtime
    if runtime is None or fallback['reason'] != 'runner_unreachable':
        return fallback
    try:
        health = runtime.runner(owner).health()
        available = health['available'] and health.get('reason') is None
        return {'available': available, 'reason': None if available else health.get('reason') or 'unavailable',
                'model': MODEL, 'verification': health['verification']}
    except Exception:
        return fallback


def runtime_runner(app, owner):
    runtime = app.state.user_runner_runtime
    if runtime is None:
        fail('codex_runner_unreachable', 'Codex transport is not ready', 503)
    try:
        runner = runtime.runner(owner)
        health = runner.health()
        if not health['available'] or health.get('reason') is not None:
            fail(ERRORS.get(health.get('reason'), 'codex_unavailable'), 'Codex cannot accept this request', 503)
        return runner
    except (ValueError, OSError, httpx.HTTPError):
        fail('codex_runner_unreachable', 'Codex transport is not ready', 503)


def stop_runtime(app, owner):
    runtime = app.state.user_runner_runtime
    if runtime is None:
        return
    try:
        runtime.stop(owner)
    except Exception:
        fail('codex_runner_unreachable', 'Runner termination requires retry', 503)


def register(app, sessions, settings, current_session):
    prefix = settings.base_path + '/api/research/codex'

    @contextmanager
    def authenticated(request, mutate=False):
        with sessions.begin() as db:
            login = current_session(db, request)
            if not login:
                fail('unauthenticated', 'Authentication required', 401)
            account = db.scalar(select(Account).where(Account.id == login.account_id).with_for_update(read=True))
            if not current_session(db, request):
                fail('unauthenticated', 'Authentication required', 401)
            permitted = eligible(account, app.state.research_policy)
            if mutate:
                if not permitted or not selected(app, account.id):
                    fail('forbidden', 'Runner preparation is not permitted', 403)
                if request.headers.get('origin') != settings.origin:
                    fail('origin_not_allowed', 'Origin not allowed', 403)
                if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), login.csrf_token):
                    fail('csrf_invalid', 'Invalid CSRF token', 403)
            yield db, account, permitted

    def response(value, status=200):
        return JSONResponse(value, status_code=status, headers={'Cache-Control': 'no-store'})

    def status(request):
        with authenticated(request) as (db, account, permitted):
            if not permitted:
                return response(status_body(permitted=False))
            fallback = registry_status(checked_registry(db), account.id)
            owner = account.id
        return response(live_status(app, owner, fallback))

    @app.delete(prefix + '/runner')
    def stop(request: Request):
        with authenticated(request, mutate=True) as (_, account, _):
            owner = account.id
        stop_runtime(app, owner)
        return response({'terminationRequested': True})

    @app.post(prefix + '/runner')
    def prepare(body: dict, request: Request):
        with authenticated(request, mutate=True) as (db, account, _):
            if body != {}:
                fail('validation_error', 'Invalid request fields', 422)
            try:
                raw_key = request.headers.get('idempotency-key')
                key = UUID(raw_key)
            except (ValueError, TypeError, AttributeError):
                fail('validation_error', 'A UUID Idempotency-Key is required', 422)
            registry = checked_registry(db)
            try:
                state, _ = registry.enter(account.id, key, utc_now)
            except Refused:
                fail('codex_runner_unreachable', 'Runner state requires reconciliation', 503)
            # Body shares GET's parser, and contains no private runtime identities.
            code = 200 if state.pod_uid is not None else 202
            fallback = registry_status(registry, account.id, state)
            owner = account.id
        if app.state.user_runner_runtime:
            try:
                app.state.user_runner_runtime.ensure(owner)
            except Exception:
                fail('codex_runner_unreachable', 'Runner preparation requires reconciliation', 503)
            with sessions.begin() as db:
                fallback = registry_status(checked_registry(db), owner)
            fallback = live_status(app, owner, fallback)
        return response(fallback, code)

    return status
