"""Bounded process-local anonymous abuse guard; trusted-peer XFF only."""
from collections import OrderedDict
import ipaddress
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from .research import ResearchError
from . import research_api_budget as budget
from . import research_pats as pats


def client_key(request, networks):
    try:
        peer = ipaddress.ip_address(request.client.host)
    except (ValueError, AttributeError):
        return 'unknown'
    trusted = lambda address: any(address in network for network in networks)
    if not trusted(peer):
        return str(peer)
    values = request.headers.getlist('x-forwarded-for')
    if len(values) != 1 or len(values[0]) > 1024:
        return str(peer)
    parts = values[0].split(',')
    if not 1 <= len(parts) <= 16:
        return str(peer)
    try:
        addresses = [ipaddress.ip_address(part.strip()) for part in parts]
    except ValueError:
        return str(peer)
    for address in reversed(addresses):
        if not trusted(address):
            return str(address)
    return str(peer)


class Guard:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.window, self.total, self.keys = clock(), 0, OrderedDict()

    def admit(self, key):
        with self.lock:
            at = self.clock()
            if at >= self.window + 60:
                self.window, self.total = at, 0
                self.keys.clear()
            if self.total >= 600 or self.keys.get(key, 0) >= 120:
                return False
            # Never evict an active bucket to reset its allowance. Refuse new
            # identities at capacity (and the lower global cap bounds growth).
            if key not in self.keys and len(self.keys) >= 4096:
                return False
            self.keys[key] = self.keys.get(key, 0) + 1
            self.total += 1
            return True


def failure(code, status):
    return JSONResponse({'error': {'code': code, 'message': 'Research request could not be admitted'}},
        status_code=status, headers={'Cache-Control': 'no-store', **({'Retry-After': '60'} if status == 429 else {})})


def install(app, sessions, settings):
    networks = tuple(ipaddress.ip_network(cidr) for cidr in settings.trusted_proxy_cidrs)
    public = settings.base_path + '/api/public/'
    private = settings.base_path + '/api/research/agent/v1/'
    guard = Guard()

    def public_pat(token):
        if not app.state.research_agent_api_enabled:
            raise ResearchError('not_ready', 'Research API unavailable', 503)
        with budget.authorization():
            principal = pats.observe(sessions, token)
            if principal.scope == 'none':
                raise ResearchError('forbidden', 'Account approval required', 403)

    class ResearchRate:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http' or not scope['path'].startswith((public, private)):
                return await self.app(scope, receive, send)
            request = Request(scope)
            if not guard.admit(client_key(request, networks)):
                return await failure('rate_limited', 429)(scope, receive, send)
            if scope['path'].startswith(public) and request.headers.getlist('authorization'):
                values = request.headers.getlist('authorization')
                try:
                    if len(values) != 1 or not values[0].startswith('Bearer '):
                        raise ResearchError('unauthenticated', 'Authentication required', 401)
                    await run_in_threadpool(public_pat, values[0][7:])
                except ResearchError as error:
                    return await failure(error.code, error.status)(scope, receive, send)
                except (SQLAlchemyError, RuntimeError):
                    return await failure('not_ready', 503)(scope, receive, send)
            return await self.app(scope, receive, send)
    app.add_middleware(ResearchRate)
