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
const setMock = (q) => fetch(`${ORIGIN}/__mock/set?${q}`).then((r) => r.json());

// ---------- HTTP ----------
for (const p of ["/", "/posts/", "/posts/welcome-to-observatory/", "/tags/", "/tags/AI/", "/status/", "/write/", "/admin/", "/view/?id=x", "/auth/login/"]) {
  const r = await fetch(U(p), { redirect: "manual" });
  check(`GET ${p} -> 200`, r.status === 200, `status ${r.status}`);
}
for (const p of ["/posts/not-built-slug/", "/tags/NewTag/", "/nope/", "/auth/callback/", "/admin/users/"]) {
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

// C. 상태 화면
await page.goto(U("/status/"));
await page.getByText("API 상태").waitFor();
await page.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
const statusText = await page.locator("main").innerText();
check("C. status shows web sha", statusText.includes(EXPECT_SHA), "");
check("C. status shows API 정상", statusText.includes("정상 (요청을 받을 준비가 되었습니다)"));
check("C. status shows API version", statusText.includes("b".repeat(40)));
check("C. status shows account pending", statusText.includes("mock-user") && statusText.includes("승인 대기"));

// D. 로그아웃 실패(403) -> 로그인 유지 + 오류 표시
await setMock("logout=403");
await header.getByRole("button", { name: "로그아웃" }).first().click();
await header.getByRole("alert").first().waitFor();
check("D. logout 403 shows error", (await header.getByRole("alert").first().innerText()).includes("로그아웃 실패"));
check("D. still logged in after 403", await header.getByText("mock-user").first().isVisible());

// E. 로그아웃 성공 (POST + X-CSRF-Token)
await setMock("logout=normal");
await header.getByRole("button", { name: "로그아웃" }).first().click();
await header.getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
const calls = await (await fetch(`${ORIGIN}/__mock/calls`)).json();
const lastLogout = calls.filter((c) => c.path === "/auth/logout").at(-1);
check("E. logout POST with X-CSRF-Token and Origin", lastLogout?.method === "POST" && !!lastLogout.csrf && lastLogout.origin === ORIGIN, JSON.stringify(lastLogout));
check("E. header back to login link", await header.getByRole("link", { name: "GitHub 로그인" }).first().isVisible());

// F. 승인된 사용자 -> 글 쓰기 메뉴 -> 준비 중
await setMock("nextLogin=approved");
await page.goto(U("/"));
await header.getByRole("link", { name: "GitHub 로그인" }).first().click();
await page.waitForURL(U("/"));
await header.getByRole("link", { name: "글 쓰기" }).first().waitFor();
check("F. approved user sees 글 쓰기 nav", true);
check("F. approved user has no pending badge", (await header.getByText("승인 대기").count()) === 0);
await header.getByRole("link", { name: "글 쓰기" }).first().click();
await page.waitForURL(U("/write/"));
check("F. /write/ shows 준비 중", (await page.locator("main").innerText()).includes("준비 중"));

// G. callback 실패 코드
await setMock("nextLogin=access_denied");
await page.goto(U("/auth/login/"));
// 이미 로그인된 상태라 로그아웃 후 시도
await page.goto(U("/"));
await header.getByRole("button", { name: "로그아웃" }).first().click();
await header.getByRole("link", { name: "GitHub 로그인" }).first().click();
await page.waitForURL(/auth_error=access_denied/);
check("G. access_denied message", await page.getByText("GitHub 로그인 동의가 취소되었습니다.").isVisible());
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
await page.goto(U("/status/"));
await page.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
check("H. status account row shows 확인 실패", (await page.locator("main").innerText()).includes("로그인 상태를 확인하지 못했습니다"));
await setMock("me=normal");
await header.getByRole("button", { name: /로그인 상태 확인 실패/ }).first().click();
await header.getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
check("H. retry after recovery -> unauthenticated", true);

// I. health 503
await setMock("health=503");
await page.goto(U("/status/"));
await page.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
check("I. health 503 -> 준비되지 않음", (await page.locator("main").innerText()).includes("준비되지 않음 (not_ready)"));
await setMock("health=normal");

// J. 글 상세: 좋아요·댓글 준비 중
await page.goto(U("/posts/welcome-to-observatory/"));
check("J. post detail shows like/comment 준비 중", (await page.locator("main").innerText()).includes("좋아요와 댓글 기능은 새 서버로 옮기는 중입니다"));

// K. 로그아웃과 me 조회 경쟁: 로그아웃 이후 늦게 도착한 authenticated 응답이 로그인 UI를 되살리면 안 된다.
async function loginApproved() {
  await setMock("me=normal&nextLogin=approved&logout=normal");
  await page.goto(U("/"));
  await header.getByRole("link", { name: "GitHub 로그인" }).first().click();
  await header.getByText("mock-user").first().waitFor();
  await page.goto(U("/status/"));
  await page.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
}
async function assertStaysLoggedOut(label) {
  await header.getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
  await page.waitForTimeout(1500);
  check(`${label}: login link remains`, await header.getByRole("link", { name: "GitHub 로그인" }).first().isVisible());
  check(`${label}: user name not restored`, (await header.getByText("mock-user").count()) === 0);
  check(`${label}: approved menu not restored`, (await header.getByRole("link", { name: "글 쓰기" }).count()) === 0);
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
