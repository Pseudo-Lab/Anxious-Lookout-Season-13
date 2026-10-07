"""Disposable provider fixture, reachable only on the isolated test network."""
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
codes = {}
tokens = {}


@app.get("/authorize")
def authorize(request: Request):
    query = request.query_params
    assert query["code_challenge_method"] == "S256"
    if query.get("case") == "denied":
        return RedirectResponse(query["redirect_uri"] + "?" + urlencode({"state": query["state"], "error": "access_denied"}), 302)
    code = secrets.token_urlsafe(32)
    codes[code] = {"challenge": query["code_challenge"], "redirect": query["redirect_uri"], "id": int(query.get("github_id", "123456")), "login": query.get("login", "mock-user"), "case": query.get("case", "ok")}
    return RedirectResponse(query["redirect_uri"] + "?" + urlencode({"state": query["state"], "code": code}), 302)


@app.post("/token")
async def token(request: Request):
    data = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    item = codes.pop(data.get("code", ""), None)
    if not item or item["case"] == "token_error":
        return JSONResponse({"error": "bad_verification_code"}, status_code=400)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(data.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
    if challenge != item["challenge"] or data.get("redirect_uri") != item["redirect"] or data.get("client_secret") != "mock-secret":
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    access = secrets.token_urlsafe(32)
    tokens[access] = item
    return {"access_token": access, "scope": "", "token_type": "bearer"}


@app.get("/user")
def user(request: Request):
    item = tokens.pop(request.headers.get("authorization", "").removeprefix("Bearer "), None)
    if not item or item["case"] == "user_error":
        return JSONResponse({"message": "Provider failure"}, status_code=503)
    return {"id": item["id"], "login": item["login"], "site_admin": True, "email": "same-email-must-not-link@example.invalid"}
