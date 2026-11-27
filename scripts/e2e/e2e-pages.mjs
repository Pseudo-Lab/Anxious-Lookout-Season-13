// Pages 정적 산출물 검증: API 없는 배포에서 화면이 깨지지 않고 /api 요청을 하지 않는지 확인한다.
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;

// 클라이언트에서 늦게 그려지는 요소: 나타날 때까지 기다린 뒤 판정한다(즉시 isVisible()은 hydration과 경쟁).
async function appears(locator, timeout = 5000) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}
const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

// #28: 글·태그는 API(DB 발행본)에서 오므로 export에는 동적 경로 자리표시(_)만 있다. /view는 제거됐다.
for (const p of ["/", "/posts/", "/posts/_/", "/tags/", "/tags/_/", "/status/", "/write/", "/admin/", "/auth/login/", "/research/", "/research/material/", "/research/document/", "/research/session/", "/research/material/new/", "/research/document/new/", "/public/", "/public/document/"]) {
  const r = await fetch(U(p));
  check(`GET ${p} -> 200`, r.status === 200, `status ${r.status}`);
}
{
  for (const p of ["/nope/", "/view/"]) {
    const r = await fetch(U(p));
    check(`GET ${p} -> 404`, r.status === 404, `status ${r.status}`);
  }
  const html = await (await fetch(U("/"))).text();
  const asset = /(?:src|href)="([^"]*\/_next\/static\/[^"]+\.(?:js|css))"/.exec(html)?.[1];
  check(`asset path uses base '${BASE}'`, asset?.startsWith(`${BASE}/_next/static/`), asset);
  check("static asset 200", (await fetch(`${ORIGIN}${asset}`)).status === 200);
}

const browser = await chromium.launch();
const page = await browser.newPage();
const apiRequests = [];
const consoleErrors = [];
page.on("request", (r) => { if (new URL(r.url()).pathname.includes("/api/")) apiRequests.push(r.url()); });
page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
const header = page.locator("header").first();
if (process.env.EXPECT_INSECURE === "1") {
  await page.goto(U("/"));
  check("origin is not a secure context (public IP HTTP 조건)", (await page.evaluate(() => window.isSecureContext)) === false);
}

await page.goto(U("/"));
await page.waitForLoadState("networkidle");
check("header has no login link", (await header.getByRole("link", { name: "GitHub 로그인" }).count()) === 0);
check("header has no 확인 실패 button", (await header.getByRole("button", { name: /로그인 상태 확인 실패/ }).count()) === 0);
check("home shows no-API posts notice", await appears(page.getByText("API 서버가 없어 글을 제공하지 않습니다")));

await page.goto(U("/auth/login/"));
await page.waitForLoadState("networkidle");
check("login page shows no-API notice", await appears(page.getByText("API 서버가 없어 로그인을 제공하지 않습니다")));
check("login page has no GitHub login link", (await page.getByRole("link", { name: /GitHub로 계속하기/ }).count()) === 0);

// #94: 서비스 상태는 관리자만 본다. 정적 배포에는 로그인이 없으므로 메뉴가 없고, 주소로 들어오면 안내만 보인다.
check("header has no status link (admin only)", (await header.getByRole("link", { name: "상태" }).count()) === 0);
await page.goto(U("/status/"));
check("status page shows admin-only notice", await appears(page.getByText("서비스 상태는 관리자만 볼 수 있습니다.")));
check("status page shows no API details", !(await page.locator("main").innerText()).includes("API 상태"));

// M3 개인/공개 화면: API 없는 배포 안내만 표시하고 조회하지 않는다.
// #119 B2: 경로형 상세(자리표시 _ 하나)와 예전 ?id= 주소 모두 같은 안내
for (const p of ["/research/", "/research/material/?id=x", "/research/material/_/", "/research/document/_/", "/write/_/", "/research/session/?id=x"]) {
  await page.goto(U(p));
  check(`${p} shows no-API notice`, await appears(page.getByText("API 서버가 없어 개인 자료 기능을 제공하지 않습니다")));
}
for (const p of ["/public/", "/public/document/?id=x", "/public/document/_/"]) {
  await page.goto(U(p));
  check(`${p} shows no-API notice`, await appears(page.getByText("API 서버가 없어 공개 문서를 제공하지 않습니다")));
}
check("header has no 내 연구/공개 문서 nav", (await header.getByRole("link", { name: /내 연구|공개 문서/ }).count()) === 0);

for (const p of ["/posts/", "/posts/_/", "/tags/", "/tags/_/"]) {
  await page.goto(U(p));
  check(`${p} shows no-API posts notice`, await appears(page.getByText("API 서버가 없어 글을 제공하지 않습니다")));
}

check("browser made no /api requests", apiRequests.length === 0, apiRequests.join(", "));
check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
