// Pages 정적 산출물 검증: API 없는 배포에서 화면이 깨지지 않고 /api 요청을 하지 않는지 확인한다.
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;
const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

for (const p of ["/", "/posts/", "/posts/welcome-to-observatory/", "/tags/", "/tags/AI/", "/status/", "/write/", "/admin/", "/view/", "/auth/login/"]) {
  const r = await fetch(U(p));
  check(`GET ${p} -> 200`, r.status === 200, `status ${r.status}`);
}
{
  const r = await fetch(U("/nope/"));
  check("GET /nope/ -> 404", r.status === 404, `status ${r.status}`);
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

await page.goto(U("/"));
await page.waitForLoadState("networkidle");
check("header has no login link", (await header.getByRole("link", { name: "GitHub 로그인" }).count()) === 0);
check("header has no 확인 실패 button", (await header.getByRole("button", { name: /로그인 상태 확인 실패/ }).count()) === 0);
check("home lists mdx posts", (await page.locator("main").innerText()).includes("최근 글"));

await page.goto(U("/auth/login/"));
await page.waitForLoadState("networkidle");
check("login page shows no-API notice", (await page.locator("main").innerText()).includes("API 서버가 없어 로그인을 제공하지 않습니다"));
check("login page has no GitHub login link", (await page.getByRole("link", { name: /GitHub로 계속하기/ }).count()) === 0);

await page.goto(U("/status/"));
await page.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 10000 });
const st = await page.locator("main").innerText();
check("status shows no-API for API rows", st.includes("이 배포(정적 사이트)에는 API 서버가 없습니다."));
check("status shows login not provided", st.includes("이 배포에서는 로그인을 제공하지 않습니다."));

await page.goto(U("/posts/welcome-to-observatory/"));
await page.waitForLoadState("networkidle");
check("post detail renders", (await page.locator("article h1").count()) === 1);

check("browser made no /api requests", apiRequests.length === 0, apiRequests.join(", "));
check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
