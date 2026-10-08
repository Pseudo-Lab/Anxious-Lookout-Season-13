// front 검증용 모의 게이트웨이. Traefik처럼 같은 origin에서 `${BASE}/api`는 모의 API(M2 계약 v1 + M3 research)로,
// 나머지는 Next standalone 서버로 전달한다. 실제 backend/GitHub/DB를 대체하는 테스트 도구일 뿐이다.
import http from "node:http";
import crypto from "node:crypto";
import { handleResearch, researchCounts, researchMode } from "./mock-research.mjs";

const BASE = process.env.BASE_PATH ?? "";
const NEXT = { host: "127.0.0.1", port: Number(process.env.NEXT_PORT ?? 3000) };
const PORT = Number(process.env.GATEWAY_PORT ?? 8080);

const sessions = new Map(); // sid -> { user, csrf }
// reuse=1: 다음 로그인이 직전 계정(같은 accountId)으로 다시 로그인한다(세션 만료 후 재로그인 검증용).
// as=<accountId>: 다음 로그인이 그 계정으로 로그인한다.
const mode = { me: "normal", health: "normal", nextLogin: "pending", logout: "normal", reuse: "0", as: "" };
let lastUser = null;
const users = new Map(); // accountId -> user
const calls = []; // 검증용 요청 기록

function json(res, status, body, extra = {}) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...extra });
  res.end(JSON.stringify(body));
}
const err = (res, status, code, message, extra) => json(res, status, { error: { code, message } }, extra);

function sid(req) {
  const m = /(?:^|;\s*)mock_sid=([^;]+)/.exec(req.headers.cookie ?? "");
  return m ? m[1] : null;
}

function api(req, res, path, url) {
  calls.push({
    method: req.method,
    path,
    csrf: req.headers["x-csrf-token"] ?? null,
    origin: req.headers.origin ?? null,
    idempotencyKey: req.headers["idempotency-key"] ?? null,
  });
  if (path.startsWith("/research/") || path.startsWith("/public/")) {
    return handleResearch(req, res, path, url, sessions.get(sid(req)) ?? null);
  }
  if (path === "/health") {
    if (mode.health === "503") return err(res, 503, "not_ready", "Service is not ready");
    return json(res, 200, { status: "ok" });
  }
  if (path === "/version") return json(res, 200, { sha: "b".repeat(40), builtAt: "2026-10-06T00:00:00Z" });
  if (path === "/auth/github/start") {
    res.writeHead(302, { Location: `${BASE}/api/auth/github/callback?code=mock&state=mock` });
    return res.end();
  }
  if (path === "/auth/github/callback") {
    if (mode.nextLogin !== "pending" && mode.nextLogin !== "approved" && mode.nextLogin !== "approved-commenter") {
      res.writeHead(303, { Location: `${BASE}/auth/login/?auth_error=${mode.nextLogin}` });
      return res.end();
    }
    const id = crypto.randomUUID();
    const user =
      mode.as && users.has(mode.as)
        ? users.get(mode.as)
        : mode.reuse === "1" && lastUser
        ? lastUser
        : {
            accountId: crypto.randomUUID(),
            githubId: "1234567",
            login: "mock-user",
            // approved: 승인된 편집자(M3 정책 대상) / approved-commenter: 승인됐지만 편집자가 아님
            role: mode.nextLogin === "approved" ? "editor" : "commenter",
            isApproved: mode.nextLogin === "approved" || mode.nextLogin === "approved-commenter",
          };
    lastUser = user;
    users.set(user.accountId, user);
    sessions.set(id, { csrf: crypto.randomUUID(), user });
    res.writeHead(303, {
      Location: `${BASE}/`,
      "Set-Cookie": `mock_sid=${id}; Path=${BASE}/api; HttpOnly; SameSite=Lax`,
    });
    return res.end();
  }
  if (path === "/auth/me") {
    if (mode.me === "503") return err(res, 503, "not_ready", "Service is not ready");
    if (mode.me === "html502") {
      res.writeHead(502, { "Content-Type": "text/html" });
      return res.end("<html>Bad Gateway</html>");
    }
    const s = sessions.get(sid(req));
    if (!s) return err(res, 401, "unauthenticated", "Authentication required");
    return json(res, 200, { user: s.user, csrfToken: s.csrf });
  }
  if (path === "/auth/logout") {
    if (req.method !== "POST") return err(res, 405, "method_not_allowed", "Method not allowed", { Allow: "POST" });
    if (mode.logout === "403") return err(res, 403, "csrf_invalid", "Invalid CSRF token");
    const expectedOrigin = `http://${req.headers.host}`;
    if (req.headers.origin !== expectedOrigin) return err(res, 403, "origin_not_allowed", "Origin not allowed");
    const id = sid(req);
    const s = sessions.get(id);
    const clear = { "Set-Cookie": `mock_sid=; Path=${BASE}/api; HttpOnly; SameSite=Lax; Max-Age=0` };
    if (!s) {
      res.writeHead(204, clear);
      return res.end();
    }
    if (req.headers["x-csrf-token"] !== s.csrf) return err(res, 403, "csrf_invalid", "Invalid CSRF token");
    sessions.delete(id);
    res.writeHead(204, clear);
    return res.end();
  }
  return err(res, 404, "not_found", "Not found");
}

function proxy(req, res) {
  const up = http.request(
    { ...NEXT, method: req.method, path: req.url, headers: req.headers },
    (r) => {
      res.writeHead(r.statusCode ?? 502, r.headers);
      r.pipe(res);
    }
  );
  up.on("error", () => {
    res.writeHead(502, { "Content-Type": "text/plain" });
    res.end("bad gateway");
  });
  req.pipe(up);
}

http
  .createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname === "/__mock/set") {
      for (const k of ["me", "health", "nextLogin", "logout", "reuse", "as"]) if (url.searchParams.has(k)) mode[k] = url.searchParams.get(k);
      // expire=1: 모든 로그인 session 만료(서버 측 폐기)
      if (url.searchParams.get("expire") === "1") sessions.clear();
      for (const k of ["access", "publish", "codex", "csrf"]) if (url.searchParams.has(k)) researchMode[k] = url.searchParams.get(k);
      return json(res, 200, { ...mode, ...researchMode });
    }
    if (url.pathname === "/__mock/counts") return json(res, 200, researchCounts());
    if (url.pathname === "/__mock/calls") return json(res, 200, calls);
    const apiRoot = `${BASE}/api`;
    // Traefik 규칙 후보: Path(`/api`) || PathPrefix(`/api/`)
    if (url.pathname === apiRoot || url.pathname.startsWith(`${apiRoot}/`)) {
      return api(req, res, url.pathname.slice(apiRoot.length), url);
    }
    return proxy(req, res);
  })
  .listen(PORT, "0.0.0.0", () => console.log(`mock gateway :${PORT} base='${BASE}' -> next :${NEXT.port}`));
