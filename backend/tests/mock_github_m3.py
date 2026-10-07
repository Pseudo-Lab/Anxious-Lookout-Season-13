"""Integration-only provider: stable identity per browser, distinct new browsers."""
import secrets
from urllib.parse import parse_qsl, urlencode

from fastapi import Request

from tests.mock_github import app

identities = {}
next_identity = 800000


@app.middleware("http")
async def browser_identity(request: Request, call_next):
    global next_identity
    if request.url.path != "/authorize":
        return await call_next(request)
    query = dict(parse_qsl(request.scope["query_string"].decode()))
    cookie = request.cookies.get("m3_fixture_identity", "")
    if not cookie or cookie not in identities:
        cookie = secrets.token_urlsafe(24)
        next_identity += 1
        identities[cookie] = next_identity
    if "github_id" not in query:
        query["github_id"] = str(identities[cookie])
        query["login"] = "fixture-" + query["github_id"]
        request.scope["query_string"] = urlencode(query).encode()
    response = await call_next(request)
    response.set_cookie("m3_fixture_identity", cookie, httponly=True, samesite="lax", path="/_fixture/github")
    return response
