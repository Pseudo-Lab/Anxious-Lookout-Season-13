// front 화면·HTTP 검증. 모의 게이트웨이(:8080) 뒤의 Next standalone을 실제 브라우저(Chromium)로 확인한다.
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const EXPECT_SHA = process.env.EXPECT_SHA ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
// 클라이언트에서 늦게 그려지는 요소: 나타날 때까지 기다린 뒤 판정한다(즉시 isVisible()은 hydration과 경쟁).
async function appears(locator, timeout = 5000) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}
const setMock = (q) => fetch(`${ORIGIN}/__mock/set?${q}`).then((r) => r.json());

// ---------- HTTP ----------
// #28: 글·태그 상세는 DB 발행본을 브라우저가 조회하므로 없는 slug·태그도 셸은 200이다(화면에서 "찾을 수 없음").
for (const p of ["/", "/posts/", "/posts/welcome-to-observatory/", "/posts/not-built-slug/", "/tags/", "/tags/AI/", "/tags/NewTag/", "/status/", "/write/", "/admin/", "/auth/login/"]) {
  const r = await fetch(U(p), { redirect: "manual" });
  check(`GET ${p} -> 200`, r.status === 200, `status ${r.status}`);
}
// #28 D4: /view는 제거됐다(/posts/[slug]로 통일)
for (const p of ["/view/", "/nope/", "/auth/callback/", "/admin/users/"]) {
  const r = await fetch(U(p), { redirect: "manual" });
  const ct = r.headers.get("content-type") ?? "";
  check(`GET ${p} -> 404 html`, r.status === 404 && ct.includes("text/html"), `status ${r.status} ${ct}`);
}
{
  const r = await fetch(U("/apis"), { redirect: "manual" });
  const ct = r.headers.get("content-type") ?? "";
  check("GET /apis -> web (not API JSON)", !ct.includes("application/json"), `status ${r.status} ${ct}`);
}
{
  const r = await fetch(U("/version.json"));
  const body = await r.json().catch(() => null);
  check("version.json Cache-Control no-store", r.headers.get("cache-control") === "no-store", r.headers.get("cache-control"));
  check("version.json sha == expected", body?.sha === EXPECT_SHA, JSON.stringify(body));
}
{
  const html = await (await fetch(U("/"))).text();
  const asset = /(?:src|href)="([^"]*\/_next\/static\/[^"]+\.(?:js|css))"/.exec(html)?.[1];
  check(`asset path uses base '${BASE}'`, asset?.startsWith(`${BASE}/_next/static/`), asset);
  const r = await fetch(`${ORIGIN}${asset}`);
  check("static asset 200 immutable", r.status === 200 && (r.headers.get("cache-control") ?? "").includes("immutable"), r.headers.get("cache-control"));
  check("HTML has no supabase reference", !/supabase/i.test(html));
}

for (const p of ["/posts/welcome-to-observatory/", "/tags/AI/"]) {
  // 동적 셸은 요청마다 렌더하고 공유 캐시에 저장되지 않아야 한다(발행 취소 즉시 반영, 읽기 전용 FS에 ISR 쓰기 없음).
  const r = await fetch(U(p));
  const cc = r.headers.get("cache-control") ?? "";
  check(`${p} shell not cached (no s-maxage)`, !/s-maxage|immutable/.test(cc), cc);
  const html = await r.text();
  check(`${p} shell has no post body (browser loads it)`, !html.includes("Observatory는 스터디 그룹"));
}

// ---------- Browser ----------
const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();
const consoleErrors = [];
const externalHosts = new Set();
page.on("console", (m) => {
  if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text());
});
page.on("request", (req) => {
  const h = new URL(req.url()).host;
  if (h !== new URL(ORIGIN).host) externalHosts.add(h);
});
const header = page.locator("header").first();
if (process.env.EXPECT_INSECURE === "1") {
  await page.goto(U("/"));
  check("origin is not a secure context (public IP HTTP 조건)", (await page.evaluate(() => window.isSecureContext)) === false);
}

await setMock("me=normal&health=normal&nextLogin=pending&logout=normal");

// A. 비로그인
await page.goto(U("/"));
const loginLink = header.getByRole("link", { name: "GitHub 로그인" }).first();
await loginLink.waitFor();
check("A. unauthenticated header shows login link", await loginLink.isVisible());
check("A. login link -> <base>/api/auth/github/start", (await loginLink.getAttribute("href")) === `${BASE}/api/auth/github/start`, await loginLink.getAttribute("href"));
check("A. no 글 쓰기 nav when logged out", (await header.getByRole("link", { name: "글 쓰기" }).count()) === 0);

// B. 로그인(승인 대기)
await loginLink.click();
await page.waitForURL(U("/"));
await header.getByText("mock-user").first().waitFor();
check("B. after login header shows login name", await header.getByText("mock-user").first().isVisible());
check("B. pending badge shown", await header.getByText("승인 대기").first().isVisible());
check("B. pending user has no 글 쓰기 nav", (await header.getByRole("link", { name: "글 쓰기" }).count()) === 0);
const cookies = await context.cookies();
const sidCookie = cookies.find((c) => c.name === "mock_sid");
check("B. session cookie HttpOnly, Path=<base>/api", sidCookie?.httpOnly && sidCookie?.path === `${BASE}/api`, JSON.stringify(sidCookie && { path: sidCookie.path, httpOnly: sidCookie.httpOnly }));
const ls = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
check("B. no token in local/sessionStorage", ls === "{}", ls);

// C. 상태 화면(#94: 관리자만). 일반(승인 대기) 계정에는 메뉴·홈 버튼이 없고, 주소로 들어오면 안내만 보인다.
check("C. non-admin: no 상태 nav", (await header.getByRole("link", { name: "상태" }).count()) === 0);
check("C. non-admin: no 서비스 상태 button on home", (await page.locator("main").getByRole("link", { name: "서비스 상태" }).count()) === 0);
{ const m = header.getByRole("button", { name: /계정 메뉴/ }).first(); await m.click(); }
check("C. non-admin: account menu has API 토큰 but no 서비스 상태/관리 (#119 D4)", (await appears(header.getByRole("link", { name: "API 토큰" }))) && (await header.getByRole("link", { name: /서비스 상태|관리/ }).count()) === 0);
await page.keyboard.press("Escape");
await page.goto(U("/status/"));
check("C. non-admin: status page shows admin-only notice", await appears(page.getByText("서비스 상태는 관리자만 볼 수 있습니다.")));
check("C. non-admin: no status details", !(await page.locator("main").innerText()).includes("API 상태"));
// 관리자(별도 브라우저)는 메뉴와 상세를 본다.
async function adminStatusPage() {
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  const h = p.locator("header").first();
  await setMock("nextLogin=admin");
  await p.goto(U("/"));
  await h.getByRole("link", { name: "GitHub 로그인" }).first().click();
  await h.getByText("mock-user").first().waitFor();
  await setMock("nextLogin=pending");
  return { ctx, p, h };
}
{
  const a = await adminStatusPage();
  // #119 D4: 서비스 상태·관리는 상단 메뉴가 아니라 계정 메뉴에 있고, 홈 히어로 버튼은 없다
  check("C. admin: no 상태 in top nav", (await a.h.getByRole("navigation", { name: "주 메뉴" }).getByRole("link", { name: /상태|관리/ }).count()) === 0);
  check("C. admin: no 서비스 상태 button on home", (await a.p.locator("main").getByRole("link", { name: "서비스 상태" }).count()) === 0);
  await a.h.getByRole("button", { name: /계정 메뉴/ }).first().click();
  check("C. admin: account menu has 서비스 상태 + 관리", (await appears(a.h.getByRole("link", { name: "서비스 상태" }))) && (await appears(a.h.getByRole("link", { name: "관리" }))));
  await a.p.goto(U("/status/"));
  await a.p.getByText("API 상태").waitFor();
  await a.p.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
  const statusText = await a.p.locator("main").innerText();
  // #119 H1: 앞 7자만 보이고 전체 해시는 복사 버튼으로
  check("C. status shows web sha (7 chars)", statusText.includes(EXPECT_SHA.slice(0, 7)) && !statusText.includes(EXPECT_SHA), "");
  check("C. status shows build time in local format (#119 H2)", !/\d{4}-\d{2}-\d{2}T/.test(statusText));
  check("C. status shows API 정상", statusText.includes("정상 (요청을 받을 준비가 되었습니다)"));
  check("C. status shows API version", statusText.includes("b".repeat(40)));
  check("C. status shows account (admin)", statusText.includes("mock-user") && statusText.includes("관리자"));
  await a.ctx.close();
}

// D. 로그아웃 실패(403) -> 로그인 유지 + 오류 표시
await setMock("logout=403");
{ const m = header.getByRole("button", { name: /계정 메뉴/ }).first(); if ((await m.getAttribute("aria-expanded")) !== "true") await m.click(); }
await header.getByRole("button", { name: "로그아웃", exact: true }).first().click();
await header.getByRole("alert").first().waitFor();
check("D. logout 403 shows error", (await header.getByRole("alert").first().innerText()).includes("로그아웃 실패"));
check("D. still logged in after 403", await header.getByText("mock-user").first().isVisible());

// D2. #13 실행 환경 삭제 실패(503) -> 로그인 유지 + 오류 표시(성공으로 처리하지 않음)
await setMock("logout=503");
{ const m = header.getByRole("button", { name: /계정 메뉴/ }).first(); if ((await m.getAttribute("aria-expanded")) !== "true") await m.click(); }
await header.getByRole("button", { name: "로그아웃", exact: true }).first().click();
await header.getByText("codex_runner_unreachable").first().waitFor();
check("D2. logout 503 shows error", (await header.getByRole("alert").first().innerText()).includes("로그아웃 실패"));
check("D2. still logged in after 503", await header.getByText("mock-user").first().isVisible());

// E. 로그아웃 성공 (POST + X-CSRF-Token). #13: 서버가 실행 환경 삭제 요청으로 5초 넘게 걸려도 끊지 않고 성공한다.
await setMock("logout=slow");
{ const m = header.getByRole("button", { name: /계정 메뉴/ }).first(); if ((await m.getAttribute("aria-expanded")) !== "true") await m.click(); }
await header.getByRole("button", { name: "로그아웃", exact: true }).first().click();
await header.getByRole("link", { name: "GitHub 로그인" }).first().waitFor({ timeout: 20000 });
await setMock("logout=normal");
const calls = await (await fetch(`${ORIGIN}/__mock/calls`)).json();
const lastLogout = calls.filter((c) => c.path === "/auth/logout").at(-1);
check("E. logout POST with X-CSRF-Token and Origin", lastLogout?.method === "POST" && !!lastLogout.csrf && lastLogout.origin === ORIGIN, JSON.stringify(lastLogout));
check("E. header back to login link", await header.getByRole("link", { name: "GitHub 로그인" }).first().isVisible());

// F. 승인된 사용자 -> 글 쓰기 메뉴 -> 글 쓰기 화면(#28)
await setMock("nextLogin=approved");
await page.goto(U("/"));
await header.getByRole("link", { name: "GitHub 로그인" }).first().click();
await page.waitForURL(U("/"));
await header.getByRole("link", { name: "글 쓰기" }).first().waitFor();
check("F. approved user sees 글 쓰기 nav", true);
check("F. approved user has no pending badge", (await header.getByText("승인 대기").count()) === 0);
await header.getByRole("link", { name: "글 쓰기" }).first().click();
await page.waitForURL(U("/write/"));
check("F. /write/ shows post writing (not 준비 중)", await appears(page.locator("main").getByRole("heading", { name: "글 쓰기" })));

// G. callback 실패 코드
await setMock("nextLogin=access_denied");
await page.goto(U("/auth/login/"));
// 이미 로그인된 상태라 로그아웃 후 시도
await page.goto(U("/"));
{ const m = header.getByRole("button", { name: /계정 메뉴/ }).first(); if ((await m.getAttribute("aria-expanded")) !== "true") await m.click(); }
await header.getByRole("button", { name: "로그아웃", exact: true }).first().click();
await header.getByRole("link", { name: "GitHub 로그인" }).first().click();
await page.waitForURL(/auth_error=access_denied/);
check("G. access_denied message", await appears(page.getByText("GitHub 로그인 동의가 취소되었습니다.")));
await page.goto(U("/auth/login/?auth_error=%3Cb%3Ex%3C%2Fb%3E"));
await page.getByRole("alert").first().waitFor();
const alertText = await page.getByRole("alert").first().innerText();
check("G. unknown code -> generic message, not reflected", alertText.includes("로그인에 실패했습니다") && !alertText.includes("<b>"), alertText);

// H. me 503 / 비JSON 502 -> 확인 실패(비로그인으로 가장하지 않음)
for (const m of ["503", "html502"]) {
  await setMock(`me=${m}`);
  await page.goto(U("/"));
  const errBtn = header.getByRole("button", { name: /로그인 상태 확인 실패/ }).first();
  await errBtn.waitFor();
  check(`H. me ${m} -> header shows 확인 실패`, await errBtn.isVisible());
  check(`H. me ${m} -> no login link shown`, (await header.getByRole("link", { name: "GitHub 로그인" }).count()) === 0);
}
// #94: 로그인 상태를 확인하지 못하면 관리자인지 알 수 없으므로 상태 화면은 안내만 보인다.
await page.goto(U("/status/"));
check("H. me error: status page stays admin-only", await appears(page.getByText("서비스 상태는 관리자만 볼 수 있습니다.")));
await setMock("me=normal");
await header.getByRole("button", { name: /로그인 상태 확인 실패/ }).first().click();
await header.getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
check("H. retry after recovery -> unauthenticated", true);

// I. health 503(관리자 화면)
{
  const prevLogin = "pending";
  const a = await adminStatusPage();
  await setMock("health=503");
  await a.p.goto(U("/status/"));
  await a.p.getByText("API 상태").waitFor();
  await a.p.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
  check("I. health 503 -> 준비되지 않음", (await a.p.locator("main").innerText()).includes("준비되지 않음 (not_ready)"));
  await setMock(`health=normal&nextLogin=${prevLogin}`);
  await a.ctx.close();
}

// J. 글 상세: #119 F5 내부 사정 문구 없음, 목록·태그로 가는 길(E3)
await page.goto(U("/posts/welcome-to-observatory/"));
await page.locator("article h1").first().waitFor();
check("J. post detail has no 'moving servers' notice (#119 F5)", (await page.getByText("새 서버로 옮기는 중").count()) === 0);
check("J. post detail links to tag list (#119 E3)", await appears(page.locator("main").getByRole("link", { name: "모든 태그" })));
check("J. tab title is the post title (#119 A3)", (await page.title()).startsWith("Observatory에 오신 것을 환영합니다"), await page.title());
// #80 F5: 다른 날 다시 발행한 글(mock seed)은 최초 발행일과 수정일을 함께 보인다
{
  const head = page.locator("article header");
  const revised = head.locator('time[datetime="2026-03-21T03:00:00Z"]');
  check("J. #80: republished post shows first publish date and revised date", (await appears(revised)) && (await revised.innerText()).endsWith("수정") && (await head.locator('time[title^="최초 발행일 "]').count()) === 1);
}

// P. #28 공개 글(DB 발행본, 모의 seed = 이관된 MDX 3개)
{
  const apiCalls = [];
  const onReq = (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith(`${BASE}/api/public/`)) apiCalls.push(u.pathname + u.search);
  };
  page.on("request", onReq);
  await page.goto(U("/"));
  check("P. home lists recent published posts", await appears(page.getByRole("link", { name: /Observatory에 오신 것을 환영합니다/ })));
  const home = await page.locator("main").innerText();
  check("P. home shows all 3 imported posts, newest first",
    home.indexOf("Observatory에 오신") < home.indexOf("Transformer 아키텍처") && home.indexOf("Transformer 아키텍처") < home.indexOf("개발자를 위한 Docker"), home.slice(0, 400));
  check("P. home shows imported author name", home.includes("관리자"));
  check("P. #80: card of the republished post shows the revised date", await appears(page.locator("main article", { hasText: "Observatory에 오신" }).locator('time[datetime="2026-03-21T03:00:00Z"]')));
  check("P. home asks first page only (limit=5)", apiCalls.some((c) => c.startsWith(`${BASE}/api/public/posts?`) && c.includes("limit=5")), apiCalls.join(","));
  await page.goto(U("/posts/"));
  check("P. /posts lists posts", await appears(page.getByRole("link", { name: /개발자를 위한 Docker 기초/ })));
  await page.getByRole("link", { name: /개발자를 위한 Docker 기초/ }).first().click();
  await page.waitForURL(U("/posts/docker-basics-for-developers/"));
  await page.locator("article h1").first().waitFor();
  check("P. detail title", (await page.locator("article h1").innerText()) === "개발자를 위한 Docker 기초");
  // #32 블록 본문: HTML처럼 보이는 글자도 텍스트 노드로만 그린다
  check("P. HTML-looking text stays text", (await page.locator("article b").count()) === 0 && (await page.locator("article").innerText()).includes("<b>raw html</b>은 렌더하지 않습니다."));
  check("P. no image loaded for a post without own images", (await page.locator("article img").count()) === 0);
  check("P. #32: migrated block body renders without a format error", (await page.locator("article [role=alert]").count()) === 0 && (await page.locator("article .prose h2").count()) === 1);
  check("P. tab title from post", (await page.title()).startsWith("개발자를 위한 Docker 기초"), await page.title());
  check("P. #80: never-republished post shows no revised date", (await page.locator('article header time[title^="최초 발행일 "]').count()) === 1 && !(await page.locator("article header").innerText()).includes("수정"));
  await page.goto(U("/posts/understanding-transformer-architecture/"));
  await page.locator("article table").first().waitFor();
  check("P. table block rendered (header row as th)", (await page.locator("article table").count()) === 1 && (await page.locator("article table th").count()) === 2);
  await page.getByRole("link", { name: "AI", exact: true }).first().click();
  await page.waitForURL(U("/tags/AI/"));
  check("P. tag page lists tagged post", await appears(page.getByRole("link", { name: /Transformer 아키텍처 이해하기/ })));
  check("P. tag page excludes others", !(await page.locator("main").innerText()).includes("개발자를 위한 Docker"));
  await page.goto(U("/tags/"));
  check("P. tags page shows counts", await appears(page.getByRole("link", { name: "Docker (1)" })));
  await page.goto(U(`/tags/${encodeURIComponent("논문리뷰")}/`));
  check("P. non-ASCII tag decoded", await appears(page.getByRole("link", { name: /Transformer 아키텍처/ })));
  check("P. non-ASCII tag heading", (await page.locator("main h1").innerText()).includes("논문리뷰"));
  await page.goto(U("/posts/not-built-slug/"));
  check("P. unknown slug -> not found notice", await appears(page.getByText("글을 찾을 수 없습니다")));
  await page.goto(U("/tags/NewTag/"));
  check("P. unknown tag -> empty list", await appears(page.getByText("아직 게시된 글이 없습니다")));
  page.off("request", onReq);
}

// K. 로그아웃과 me 조회 경쟁: 로그아웃 이후 늦게 도착한 authenticated 응답이 로그인 UI를 되살리면 안 된다.
// me 재조회는 상태 화면의 "다시 확인"으로 일으킨다. #94부터 상태 화면은 관리자만 보므로 승인된 관리자로 로그인한다
// (승인 계정 메뉴 "글 쓰기"는 관리자에게도 보이므로 아래 "되살아나지 않음" 단언은 그대로 유효하다).
async function loginApproved() {
  await setMock("me=normal&nextLogin=admin&logout=normal");
  await page.goto(U("/"));
  await header.getByRole("link", { name: "GitHub 로그인" }).first().click();
  await header.getByText("mock-user").first().waitFor();
  await page.goto(U("/status/"));
  await page.getByRole("button", { name: "다시 확인", exact: true }).waitFor();
  await page.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
}
async function assertStaysLoggedOut(label) {
  await header.getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
  await page.waitForTimeout(1500);
  check(`${label}: login link remains`, await header.getByRole("link", { name: "GitHub 로그인" }).first().isVisible());
  check(`${label}: user name not restored`, (await header.getByText("mock-user").count()) === 0);
  check(`${label}: approved menu not restored`, (await header.getByRole("link", { name: "글 쓰기" }).count()) === 0);
  check(`${label}: admin menus not restored`, (await header.getByRole("link", { name: "관리" }).count()) === 0 && (await header.getByRole("link", { name: "상태" }).count()) === 0);
  const real = await context.request.get(`${ORIGIN}${BASE}/api/auth/me`);
  check(`${label}: backend me is 401`, real.status() === 401, `status ${real.status()}`);
}
{
  // K1. me 보류 → 로그아웃 204 → 보류 해제 (리뷰 재현 순서)
  await loginApproved();
  let release;
  let captured;
  const gate = new Promise((r) => (release = r));
  const seen = new Promise((r) => (captured = r));
  await page.route("**/api/auth/me", async (route) => {
    const response = await route.fetch();
    const body = await response.text();
    captured();
    await gate;
    await route.fulfill({ response, body });
  });
  await page.getByRole("button", { name: "다시 확인", exact: true }).click();
  await seen;
  { const m = header.getByRole("button", { name: /계정 메뉴/ }).first(); if ((await m.getAttribute("aria-expanded")) !== "true") await m.click(); }
  await header.getByRole("button", { name: "로그아웃", exact: true }).first().click();
  await header.getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
  release();
  await assertStaysLoggedOut("K1 stale me after logout");
  await page.unroute("**/api/auth/me");
}
{
  // K2. 로그아웃 요청 보류 중에 보낸 me(아직 authenticated)가 먼저 도착
  await loginApproved();
  let releaseLogout;
  const gate = new Promise((r) => (releaseLogout = r));
  await page.route("**/api/auth/logout", async (route) => {
    await gate;
    await route.continue();
  });
  { const m = header.getByRole("button", { name: /계정 메뉴/ }).first(); if ((await m.getAttribute("aria-expanded")) !== "true") await m.click(); }
  await header.getByRole("button", { name: "로그아웃", exact: true }).first().click();
  const meDone = page.waitForResponse((r) => r.url().endsWith("/api/auth/me"));
  await page.getByRole("button", { name: "다시 확인", exact: true }).click();
  const meRes = await meDone;
  check("K2 me during logout returned 200 (still authenticated server-side)", meRes.status() === 200, `status ${meRes.status()}`);
  releaseLogout();
  await assertStaysLoggedOut("K2 me during pending logout");
  await page.unroute("**/api/auth/logout");
}

check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
console.log(`INFO external hosts requested by browser: ${[...externalHosts].join(", ") || "(none)"}`);

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
