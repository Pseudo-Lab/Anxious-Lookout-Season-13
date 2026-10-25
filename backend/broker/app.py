"""codex-broker HTTP front (#60): POST /v1/access issues envelope v2 to one runner Pod.

Errors carry fixed codes only. No token, account claim or Kubernetes detail is logged
or returned on refusal.
"""
from contextlib import asynccontextmanager
import hashlib
import os
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.codex_policy import CodexFailure
from runner.lifecycle import Refused
from .credentials import Credentials
from .identity import Kube, Verifier, canonical


def snapshot_id(identity, token):
    # Stable per Pod and token, so a repeated pull of the same token is idempotent in the runner ledger.
    return uuid5(NAMESPACE_URL, '/'.join((str(identity.owner), str(identity.state_id), str(identity.generation),
                                          str(identity.pod_uid), hashlib.sha256(token.encode()).hexdigest())))


def envelope(identity, credentials, force=False):
    token, account, expiry = credentials.access(force=force)
    return {'version': 2, 'ownerId': str(identity.owner), 'stateId': str(identity.state_id),
            'snapshotId': str(snapshot_id(identity, token)), 'accountId': account,
            'accessToken': token, 'expiresAt': expiry}


def create_app(verifier=None, credentials=None):
    if verifier is None:
        verifier = Verifier(Kube(), namespace=os.getenv('BROKER_RUNNER_NAMESPACE', 'codex-runner'))
    if credentials is None:
        account = os.environ['BROKER_ACCOUNT_ID']
        if not 1 <= len(account) <= 200 or any(c.isspace() for c in account):
            raise ValueError('Explicit operator account id is required')
        credentials = Credentials(os.environ.get('CODEX_HOME', '/codex-home'), account,
                                  margin=int(os.getenv('BROKER_REFRESH_MARGIN_SECONDS', '1800')))

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            credentials.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def refused(status, code):
        return JSONResponse({'error': code}, status_code=status, headers={'Cache-Control': 'no-store'})

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return refused(400, 'invalid_request')  # Never reflect the submitted body (FastAPI 422 would).

    @app.get('/healthz')
    def health():
        return {'ok': True}

    @app.post('/v1/access')
    def access(body: dict, request: Request):  # Sync: Kubernetes and app-server calls run off the event loop.
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer '):
            return refused(401, 'unauthenticated')
        try:
            if type(body) is not dict or not set(body) <= {'ownerId', 'stateId', 'refresh'} or not {'ownerId', 'stateId'} <= set(body):
                raise ValueError()
            owner, state = canonical(body['ownerId']), canonical(body['stateId'])
            force = body.get('refresh', False)
            if type(force) is not bool:
                raise ValueError()
        except (ValueError, TypeError):
            return refused(400, 'invalid_request')
        try:
            identity = verifier.verify(header[len('Bearer '):])
            if (identity.owner, identity.state_id) != (owner, state):
                raise Refused('owner/state mismatch')
        except Exception:
            return refused(403, 'refused')
        try:
            value = envelope(identity, credentials, force)
        except CodexFailure as failure:
            code = failure.reason if failure.reason in {'not_configured', 'auth_expired'} else 'unavailable'
            return refused(503, code)
        except Exception:
            return refused(503, 'unavailable')
        return JSONResponse(value, headers={'Cache-Control': 'no-store'})

    return app
