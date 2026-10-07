// 실제 API 통합 화면 검증: back의 격리 fixture(backend/compose.m3-integration.yml)에서
// 실제 FastAPI/PostgreSQL/Traefik + front web을 브라우저로 확인한다. 모의 API 제어(__mock)를 쓰지 않는다.
// GitHub는 fixture provider, Codex는 미연결(unavailable)이며 공개 쓰기는 policy_pending이다.
// 계정 승인은 운영자 명령이라 live.sh가 /shared/<name>.id를 읽어 승인한 뒤 /shared/<name>.ok를 만든다.
import { chromium } from "playwright";
import fs from "node:fs/promises";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:18100";
const BASE = process.env.BASE_PATH ?? "";
const SHARED = process.env.SHARED ?? "/shared";
const U = (p) => `${ORIGIN}${BASE}${p}`;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
async function appears(locator, timeout = 10000) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}

const browser = await chromium.launch();
const consoleErrors = [];
const externalHosts = new Set();

async function newPage() {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text());
  });
  page.on("request", (req) => {
    const h = new URL(req.url()).host;
    if (h !== new URL(ORIGIN).host) externalHosts.add(h);
  });
  page.on("dialog", (d) => void d.accept());
  return { context, page };
}

async function signIn(page) {
  await page.goto(U("/"));
  await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().click();
  await page.waitForURL(U("/"));
  await page.locator("header").getByRole("button", { name: "로그아웃" }).first().waitFor();
}

// 로그인 → fixture githubId 공유 → 운영자 승인 대기 → 같은 browser로 재로그인(승인 시 기존 session 폐기)
async function approvedAccount(name, page) {
  let meRes = await page.request.get(U("/api/auth/me"));
  if (meRes.status() !== 200) {
    await signIn(page);
    meRes = await page.request.get(U("/api/auth/me"));
  }
  const me = await meRes.json();
  await fs.writeFile(`${SHARED}/${name}.id`, me.user.githubId);
  const deadline = Date.now() + 180_000;
  for (;;) {
    try {
      await fs.access(`${SHARED}/${name}.ok`);
      break;
    } catch {
      if (Date.now() > deadline) throw new Error(`approval for ${name} timed out`);
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  // 승인 명령은 기존 session을 폐기한다. 같은 browser(같은 provider identity)로 다시 로그인한다.
  const revoked = (await page.request.get(U("/api/auth/me"))).status();
  check(`${name}. approval revoked previous session`, revoked === 401, `status ${revoked}`);
  await signIn(page);
  const after = await (await page.request.get(U("/api/auth/me"))).json();
  check(`${name}. same provider identity after re-login`, after.user.githubId === me.user.githubId);
  return after.user;
}

function apiClient(page) {
  async function csrf() {
    return (await (await page.request.get(U("/api/auth/me"))).json()).csrfToken;
  }
  return {
    async get(path) {
      const r = await page.request.get(U(`/api${path}`));
      return { status: r.status(), body: await r.json().catch(() => null) };
    },
    async send(method, path, data) {
      const r = await page.request.fetch(U(`/api${path}`), {
        method,
        data,
        headers: { Origin: ORIGIN, "X-CSRF-Token": await csrf(), "Idempotency-Key": crypto.randomUUID() },
      });
      return { status: r.status(), body: await r.json().catch(() => null) };
    },
  };
}

const A = await newPage();
const page = A.page;
const main = page.locator("main");
const api = apiClient(page);

// L0. 상태 화면: 실제 M2 health / M3 research health
{
  await page.goto(U("/status/"));
  await page.waitForFunction(() => !document.body.innerText.includes("확인 중..."), null, { timeout: 15000 });
  const st = await main.innerText();
  check("L0. status API 정상", st.includes("정상 (요청을 받을 준비가 되었습니다)"));
  check("L0. status research health 정상", st.includes("정상 (자료·문서 저장소 준비됨)"));
}

// L1. 비로그인
await page.goto(U("/research/"));
check("L1. logged out asks login", await appears(main.getByText("로그인이 필요합니다")));

// L2. 로그인했지만 승인 전: 서버 403 forbidden을 그대로 안내
await signIn(page);
await page.goto(U("/research/"));
check("L2. unapproved -> forbidden notice", await appears(main.getByText("이 기능을 이용할 권한이 없습니다")));

// L3. 승인 후 자료 생성 + 안전한 Markdown 표시
const userA = await approvedAccount("A", page);
check("L3. account approved by operator", userA.isApproved === true, `${userA.role}/${userA.isApproved}`);
const unsafe = [
  "첫 번째 내용",
  "",
  "[안전 링크](https://example.com/x) [나쁜 링크](javascript:alert(1))",
  "",
  '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>',
  "",
  "![외부 이미지](https://tracker.invalid/pixel.png)",
].join("\n");
await page.goto(U("/research/material/new/"));
await main.getByLabel("제목").fill("실물 자료 A");
await main.getByLabel("출처 URL").fill("https://example.com/a");
await main.getByLabel("내용").fill(unsafe);
await main.getByRole("button", { name: "자료 저장" }).click();
await page.waitForURL(/\/research\/material\/\?id=/);
const materialAUrl = page.url();
const materialAId = new URL(materialAUrl).searchParams.get("id");
check("L3. created via real API", await appears(main.getByRole("heading", { name: "실물 자료 A" })));
const prose = main.locator(".prose").first();
await prose.waitFor();
check("L3. stored Markdown preserved verbatim", (await api.get(`/research/materials/${materialAId}`)).body?.content === unsafe);
check("L3. safe link kept", (await prose.getByRole("link", { name: "안전 링크" }).getAttribute("href")) === "https://example.com/x");
check("L3. javascript: link not rendered", (await prose.locator('a[href^="javascript"]').count()) === 0);
check("L3. raw HTML not rendered", (await prose.locator("img, script").count()) === 0 && (await page.evaluate(() => window.__xss)) === undefined);

// L4. 수정 = 새 버전, 이전 버전 열람
await main.getByRole("button", { name: "내용 수정" }).click();
await main.getByLabel("내용").fill("두 번째 내용");
await main.getByRole("button", { name: "새 버전으로 저장" }).click();
await main.getByRole("button", { name: "내용 수정" }).waitFor();
check("L4. v2 shown", await appears(main.locator(".prose").getByText("두 번째 내용")));
check("L4. header v2", (await main.locator("header").innerText()).includes("v2"));
await main.getByRole("button", { name: /v1\s/ }).click();
check("L4. v1 snapshot readable", await appears(main.getByText("첫 번째 내용")));

// 준비: 자료 B, 문서 D (실제 API)
const matB = await api.send("POST", "/research/materials", {
  title: "실물 자료 B",
  sourceUrl: "https://example.com/b",
  collectedAt: new Date().toISOString(),
  contentKind: "summary",
  content: "B 내용",
});
const docD = await api.send("POST", "/research/documents", { title: "실물 문서 D", content: "# D 본문" });
check("setup. B/D created", matB.status === 201 && docD.status === 201, `${matB.status} ${docD.status}`);

// L5. 관계
await page.goto(materialAUrl);
await main.getByLabel("연결할 대상").selectOption({ label: "실물 자료 B" });
await main.getByLabel("관계 종류").fill("관련");
await main.getByRole("button", { name: "관계 추가" }).click();
check("L5. undirected relation via real API", await appears(main.getByRole("link", { name: "실물 자료 B" })));
check("L5. undirected label", await appears(main.getByText("상호 관련 (방향 없음)").nth(1)));
const docUrl = U(`/research/document/?id=${docD.body.id}`);
await page.goto(docUrl);
await main.getByLabel("연결할 대상").selectOption({ label: "실물 자료 A" });
await main.getByLabel("관계 종류").fill("근거");
await main.getByRole("button", { name: "관계 추가" }).click();
check("L5. document→material relation", await appears(main.getByRole("link", { name: "실물 자료 A" })));
await page.goto(materialAUrl);
check("L5. incoming direction on material", await appears(main.getByText("대상 → 이 항목")));

// L6. 동시 수정 충돌(실제 expectedVersion)
await main.getByRole("button", { name: "내용 수정" }).click();
await main.getByLabel("내용").fill("UI 쪽 수정");
{
  const cur = (await api.get(`/research/materials/${materialAId}`)).body;
  const r = await api.send("PATCH", `/research/materials/${materialAId}`, {
    title: cur.title,
    sourceUrl: cur.sourceUrl,
    collectedAt: cur.collectedAt,
    contentKind: cur.contentKind,
    content: "다른 곳의 수정",
    expectedVersion: cur.version,
  });
  check("setup. external PATCH 200", r.status === 200, String(r.status));
}
await main.getByRole("button", { name: "새 버전으로 저장" }).click();
check("L6. real 409 conflict shown", await appears(main.getByText("다른 곳에서 먼저 변경되었습니다")));
check("L6. draft preserved", (await main.getByLabel("내용").inputValue()) === "UI 쪽 수정");
await main.getByRole("button", { name: /최신 내용 다시 불러오기/ }).click();
await page.waitForTimeout(1000);
await main.getByRole("button", { name: "새 버전으로 저장" }).click();
await main.getByRole("button", { name: "내용 수정" }).waitFor();
check("L6. save after reload succeeds", await appears(main.locator(".prose").getByText("UI 쪽 수정")));

// L7. 응답 유실 후 재시도: 실제 Idempotency-Key replay로 1건만 생성
{
  const countDocs = async () => (await api.get("/research/documents?limit=100")).body.items.length;
  const before = await countDocs();
  await page.goto(U("/research/document/new/"));
  await main.getByLabel("제목").fill("실물 재시도 문서");
  await main.getByLabel("본문").fill("본문");
  let dropped = false;
  const keys = [];
  await page.route("**/api/research/documents", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    keys.push(route.request().headers()["idempotency-key"]);
    if (!dropped) {
      dropped = true;
      await route.fetch();
      return route.abort();
    }
    return route.continue();
  });
  await main.getByRole("button", { name: "문서 저장" }).click();
  check("L7. lost response shows failure", await appears(main.getByText("서버에 연결할 수 없습니다")));
  await main.getByRole("button", { name: "문서 저장" }).click();
  await page.waitForURL(/\/research\/document\/\?id=/);
  await page.unroute("**/api/research/documents");
  check("L7. same key reused", keys.length === 2 && keys[0] === keys[1], JSON.stringify(keys));
  check("L7. exactly one document created", (await countDocs()) === before + 1);
}

// L8. 공개: 정책 확정 전 실제 서버 policy_pending
await page.goto(docUrl);
await main.getByRole("button", { name: "공개하기" }).click();
await main.getByRole("checkbox", { name: "실물 자료 A" }).check();
await main.getByRole("button", { name: "이 내용으로 공개" }).click();
check("L8. publish policy_pending from real API", await appears(main.getByText("이용 정책이 확정되기 전")));
await main.getByRole("button", { name: "취소" }).click();
check("L8. still private", await appears(main.getByText("비공개 문서입니다")));

// L9. 세션: 실제 Codex 미연결 상태 표시, 전송 실패 시 입력 보존
await page.goto(U("/research/?tab=sessions"));
check("L9. codex status not shown as available", await appears(main.getByText(/Codex에 연결되어 있지 않아|실제 응답은 아직 확인되지 않았습니다/)));
await main.getByLabel("새 대화 제목").fill("실물 세션");
await main.getByRole("button", { name: "새 대화" }).click();
await page.waitForURL(/\/research\/session\/\?id=/);
const sessionUrl = page.url();
check("L9. session created via real API", await appears(main.getByRole("heading", { name: "실물 세션" })));
await main.getByLabel("메시지").fill("실물 질문");
await main.getByRole("button", { name: "보내기" }).click();
check("L9. send -> codex_unavailable shown", await appears(main.getByText("Codex에 연결할 수 없어")));
check("L9. input preserved", (await main.getByLabel("메시지").inputValue()) === "실물 질문");
check("L9. no unconfirmed send kept for definitive 503", !(await page.evaluate(() => JSON.stringify({ ...sessionStorage }))).includes("실물 질문"));
await page.reload();
check("L9. session persists after reload", await appears(main.getByRole("heading", { name: "실물 세션" })));

// L10. 다른 승인 계정(B): A의 자료·세션은 404로 숨김, B 목록은 비어 있음
{
  const B = await newPage();
  await approvedAccount("B", B.page);
  const bm = B.page.locator("main");
  await B.page.goto(materialAUrl);
  check("L10. foreign material -> not found", await appears(bm.getByText("항목을 찾을 수 없습니다")));
  await B.page.goto(sessionUrl);
  check("L10. foreign session -> not found", await appears(bm.getByText("항목을 찾을 수 없습니다")));
  await B.page.goto(U("/research/"));
  check("L10. B list empty", await appears(bm.getByText("아직 저장한 자료가 없습니다")));
  await B.context.close();
}

// L11. 로그아웃 후 실제 API 401, 화면은 로그인 안내
await page.goto(U("/research/"));
await page.locator("header").getByRole("button", { name: "로그아웃" }).first().click();
await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
check("L11. after logout API 401", (await api.get("/research/materials")).status === 401);
check("L11. after logout UI asks login", await appears(main.getByText("로그인이 필요합니다")));
check("L11. nothing left in session/localStorage", (await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))) === "{}");

check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
check("no external tracker requests", ![...externalHosts].some((h) => h.includes("tracker") || h.includes("example.com")), [...externalHosts].join(", "));

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
