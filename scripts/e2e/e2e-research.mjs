// M3 화면 검증: 모의 research API(mock-research.mjs) 뒤의 Next standalone을 실제 브라우저로 확인한다.
// 실제 backend/DB/Codex 동작 검증이 아니다(모의 응답은 계약 형태만 흉내 낸다).
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
async function appears(locator, timeout = 8000) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}
const setMock = (q) => fetch(`${ORIGIN}/__mock/set?${q}`).then((r) => r.json());
const counts = () => fetch(`${ORIGIN}/__mock/counts`).then((r) => r.json());
const mockCalls = () => fetch(`${ORIGIN}/__mock/calls`).then((r) => r.json());

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

async function login(page) {
  await page.goto(U("/"));
  const header = page.locator("header").first();
  await header.getByRole("link", { name: "GitHub 로그인" }).first().click();
  await header.getByText("mock-user").first().waitFor();
}

// 화면 밖 준비 작업용: 같은 session cookie로 계약 API 호출(브라우저와 같은 Origin/CSRF/Idempotency-Key 규칙)
function apiClient(page) {
  async function csrf() {
    const r = await page.request.get(U("/api/auth/me"));
    return (await r.json()).csrfToken;
  }
  return {
    async get(path) {
      return (await page.request.get(U(`/api${path}`))).json();
    },
    async send(method, path, data) {
      const r = await page.request.fetch(U(`/api${path}`), {
        method,
        data,
        headers: { Origin: ORIGIN, "X-CSRF-Token": await csrf(), "Idempotency-Key": crypto.randomUUID() },
      });
      return { status: r.status(), body: await r.json() };
    },
  };
}

await setMock("me=normal&health=normal&logout=normal&nextLogin=approved&access=pending&publish=pending&codex=unavailable");
const { context, page } = await newPage();
const main = page.locator("main");
const api = apiClient(page);

// R1. 비로그인
await page.goto(U("/research/"));
check("R1. logged out /research/ asks login", await appears(main.getByText("로그인이 필요합니다")));
check("R1. no 내 연구 nav when logged out", (await page.locator("header").getByRole("link", { name: "내 연구" }).count()) === 0);

// R2. 정책 확정 전: 서버 policy_pending을 그대로 안내(역할 추정 없음)
await login(page);
check("R2. 내 연구 nav when logged in", await appears(page.locator("header").getByRole("link", { name: "내 연구" })));
await page.goto(U("/research/"));
check("R2. policy_pending shown", await appears(main.getByText("이용 정책이 확정되기 전")));
await setMock("access=approved");

// R3. 자료 생성 + 안전한 Markdown 표시
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
await main.getByLabel("제목").fill("자료 A");
await main.getByLabel("출처 URL").fill("https://example.com/a");
await main.getByLabel("내용").fill(unsafe);
await main.getByRole("button", { name: "자료 저장" }).click();
await page.waitForURL(/\/research\/material\/\?id=/);
const materialAUrl = page.url();
const materialAId = new URL(materialAUrl).searchParams.get("id");
check("R3. created material detail", await appears(main.getByRole("heading", { name: "자료 A" })));
const prose = main.locator(".prose").first();
await prose.waitFor();
const safeLink = prose.getByRole("link", { name: "안전 링크" });
check("R3. safe link kept", (await safeLink.getAttribute("href")) === "https://example.com/x");
check("R3. safe link rel noopener nofollow", /noopener/.test((await safeLink.getAttribute("rel")) ?? "") && /nofollow/.test((await safeLink.getAttribute("rel")) ?? ""));
check("R3. javascript: link not rendered as link", (await prose.locator('a[href^="javascript"]').count()) === 0);
check("R3. raw HTML not rendered", (await prose.locator("img, script").count()) === 0 && (await page.evaluate(() => window.__xss)) === undefined);
check("R3. external image shown as link only", (await prose.getByRole("link", { name: /이미지: 외부 이미지/ }).count()) === 1);

// R4. 수정 = 새 버전, 이전 버전 열람
await main.getByRole("button", { name: "내용 수정" }).click();
await main.getByLabel("내용").fill("두 번째 내용");
await main.getByRole("button", { name: "새 버전으로 저장" }).click();
check("R4. updated content shown", await appears(main.getByText("두 번째 내용")));
check("R4. version number v2", (await main.locator("header").innerText()).includes("v2"));
await main.getByRole("button", { name: /v1\s/ }).click();
check("R4. v1 snapshot readable", await appears(main.getByText("첫 번째 내용")));

// 준비: 자료 B, 문서 D (계약 API 직접 호출)
const matB = await api.send("POST", "/research/materials", {
  title: "자료 B",
  sourceUrl: "https://example.com/b",
  collectedAt: new Date().toISOString(),
  contentKind: "summary",
  content: "B 내용",
});
const docD = await api.send("POST", "/research/documents", { title: "문서 D", content: "# 문서 D 본문" });
check("setup. material B / document D created", matB.status === 201 && docD.status === 201, `${matB.status} ${docD.status}`);

// R5. 관계: 방향 없는 자료↔자료, 문서→자료 고정
await page.goto(materialAUrl);
await main.getByLabel("연결할 대상").selectOption({ label: "자료 B" });
await main.getByLabel("관계 종류").fill("관련");
await main.getByRole("button", { name: "관계 추가" }).click();
check("R5. undirected relation listed", await appears(main.getByRole("link", { name: "자료 B" })));
check("R5. undirected label", await appears(main.getByText("상호 관련 (방향 없음)").nth(1)));
await page.goto(U(`/research/document/?id=${docD.body.id}`));
await main.getByLabel("연결할 대상").selectOption({ label: "자료 A" });
check("R5. document→material direction fixed", await appears(main.getByText("항상 문서 → 자료 방향")));
await main.getByLabel("관계 종류").fill("근거");
await main.getByRole("button", { name: "관계 추가" }).click();
check("R5. document→material relation listed", await appears(main.getByRole("link", { name: "자료 A" })));
// 공개 노출 검증용: 문서 D → 자료 B 관계(선택은 하지 않음)
await api.send("POST", "/research/relations", {
  source: { type: "document", id: docD.body.id },
  target: { type: "material", id: matB.body.id },
  kind: "배경",
  description: "",
  directed: true,
});
await page.goto(materialAUrl);
check("R5. incoming relation on material A", await appears(main.getByText("대상 → 이 항목")));

// R6. 동시 수정 충돌 → 409 conflict 안내, 입력 유지
await main.getByRole("button", { name: "내용 수정" }).click();
await main.getByLabel("내용").fill("UI 쪽 수정");
const current = await api.get(`/research/materials/${materialAId}`);
await api.send("PATCH", `/research/materials/${materialAId}`, {
  title: current.title,
  sourceUrl: current.sourceUrl,
  collectedAt: current.collectedAt,
  contentKind: current.contentKind,
  content: "다른 곳의 수정",
  expectedVersion: current.version,
});
await main.getByRole("button", { name: "새 버전으로 저장" }).click();
check("R6. conflict message", await appears(main.getByText("다른 곳에서 먼저 변경되었습니다")));
check("R6. input preserved", (await main.getByLabel("내용").inputValue()) === "UI 쪽 수정");
check("R6. reload latest offered", await appears(main.getByRole("button", { name: /최신 내용 다시 불러오기/ })));

// R7. 응답 유실 후 재시도: 같은 Idempotency-Key → 중복 생성 없음
const before = await counts();
await page.goto(U("/research/document/new/"));
await main.getByLabel("제목").fill("재시도 문서");
await main.getByLabel("본문").fill("본문");
let dropped = false;
await page.route("**/api/research/documents", async (route) => {
  if (route.request().method() === "POST" && !dropped) {
    dropped = true;
    await route.fetch(); // 서버에는 반영
    return route.abort(); // 브라우저는 응답을 받지 못함
  }
  return route.continue();
});
await main.getByRole("button", { name: "문서 저장" }).click();
check("R7. lost response shows failure", await appears(main.getByText("서버에 연결할 수 없습니다")));
await main.getByRole("button", { name: "문서 저장" }).click();
await page.waitForURL(/\/research\/document\/\?id=/);
await page.unroute("**/api/research/documents");
const after = await counts();
const posts = (await mockCalls()).filter((c) => c.method === "POST" && c.path === "/research/documents").slice(-2);
check("R7. retry reused Idempotency-Key", posts.length === 2 && posts[0].idempotencyKey === posts[1].idempotencyKey, JSON.stringify(posts.map((p) => p.idempotencyKey)));
check("R7. exactly one document created", after.documents === before.documents + 1, `${before.documents} -> ${after.documents}`);

// R8. 공개: 정책 대기 → 허용 후 선택 자료만 공개 → 철회
const docUrl = U(`/research/document/?id=${docD.body.id}`);
await page.goto(docUrl);
await main.getByRole("button", { name: "공개하기" }).click();
await main.getByRole("checkbox", { name: "자료 A" }).check();
await main.getByRole("button", { name: "이 내용으로 공개" }).click();
check("R8. publish policy_pending shown", await appears(main.getByText("이용 정책이 확정되기 전")));
await setMock("publish=allowed");
await main.getByRole("button", { name: "이 내용으로 공개" }).click();
check("R8. published state", await appears(main.getByText("공개 중")));
{
  const visitor = await newPage();
  await visitor.page.goto(U(`/public/document/?id=${docD.body.id}`));
  const vm = visitor.page.locator("main");
  check("R8. public page shows document", await appears(vm.getByRole("heading", { name: "문서 D", exact: true })));
  check("R8. selected material shown", await appears(vm.getByText("자료 A")));
  check("R8. relation-only material not exposed", !(await vm.innerText()).includes("자료 B"));
  check("R8. public page offers login to chat", await appears(vm.getByRole("link", { name: /로그인하고 대화 시작/ })));
  await page.goto(docUrl);
  await main.getByRole("button", { name: "공개 철회" }).click();
  check("R8. revoked -> 비공개", await appears(main.getByText("비공개 문서입니다")));
  await visitor.page.reload();
  check("R8. public page after revoke", await appears(vm.getByText("공개가 철회된 문서")));
  await visitor.context.close();
}

// R9. 세션: Codex 미연결 안내 → 전송 실패 시 입력 보존 → 모의 연결 후 이어하기·도구 이력·원본
await page.goto(U("/research/?tab=sessions"));
check("R9. codex unavailable banner", await appears(main.getByText("Codex에 연결되어 있지 않아")));
await main.getByLabel("새 대화 제목").fill("세션 1");
await main.getByRole("button", { name: "새 대화" }).click();
await page.waitForURL(/\/research\/session\/\?id=/);
const sessionUrl = page.url();
await main.getByLabel("메시지").fill("질문 하나");
await main.getByRole("button", { name: "보내기" }).click();
check("R9. codex_unavailable message", await appears(main.getByText("Codex에 연결할 수 없어")));
check("R9. message input preserved", (await main.getByLabel("메시지").inputValue()) === "질문 하나");
await setMock("codex=fixture");
await page.reload();
check("R9. fixture banner (not real)", await appears(main.getByText("테스트용 모의 Codex")));
await main.getByLabel("메시지").fill("질문 하나");
await main.getByRole("button", { name: "보내기" }).click();
check("R9. running state shown", await appears(main.getByText("Codex가 응답하는 중입니다.")));
await page.reload(); // 진행 중 재접속
check("R9. reply after reconnect (polling)", await appears(main.getByText("모의 응답:"), 15000));
await main.getByRole("button", { name: /research\.search_materials/ }).click();
check("R9. tool input shown inert", await appears(main.locator("pre", { hasText: '"query": "질문 하나"' })));
await main.getByRole("button", { name: "저장된 원본 기록 보기" }).click();
check("R9. stored raw item shown", await appears(main.locator("pre", { hasText: '"kind": "function_call"' })));

// R12. 메시지 응답 유실 → polling으로 서버 version 변경 → 같은 내용 재전송: 모델 turn 중복 없음
const userMessages = (t) => main.locator("li.bg-indigo-50", { hasText: t });
async function dropFirstMessagePost() {
  let dropped = false;
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    if (!dropped) {
      dropped = true;
      await route.fetch();
      return route.abort();
    }
    return route.continue();
  });
}
{
  await page.goto(sessionUrl);
  await main.getByLabel("메시지").waitFor();
  await dropFirstMessagePost();
  await main.getByLabel("메시지").fill("중복 방지 질문");
  await main.getByRole("button", { name: "보내기" }).click();
  check("R12. lost send -> uncertain notice", await appears(main.getByText("이전 전송의 결과를 확인하지 못했습니다")));
  check("R12. turn completed on server meanwhile", await appears(main.getByText("모의 응답: 중복 방지 질문"), 15000));
  await main.getByRole("button", { name: "다시 보내기" }).click();
  await main.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.waitForTimeout(3500);
  await page.reload();
  await main.getByText("모의 응답: 중복 방지 질문").first().waitFor();
  check("R12. exactly one user message after retry", (await userMessages("중복 방지 질문").count()) === 1, String(await userMessages("중복 방지 질문").count()));
  const sends = (await mockCalls()).filter((c) => c.method === "POST" && c.path.endsWith("/messages")).slice(-2);
  check("R12. retry reused same Idempotency-Key", sends.length === 2 && sends[0].idempotencyKey === sends[1].idempotencyKey);
  await page.unroute("**/api/research/sessions/*/messages");
}
{
  // R12b. 응답 유실 후 페이지 재접속: 입력과 key가 복구되어 같은 요청으로 재전송
  await dropFirstMessagePost();
  await main.getByLabel("메시지").fill("재접속 질문");
  await main.getByRole("button", { name: "보내기" }).click();
  await main.getByText("이전 전송의 결과를 확인하지 못했습니다").waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await page.reload();
  await main.getByLabel("메시지").waitFor();
  await appears(main.getByText("모의 응답: 재접속 질문"), 15000);
  check("R12b. pending text restored after reload", (await main.getByLabel("메시지").inputValue()) === "재접속 질문");
  await main.getByRole("button", { name: "다시 보내기" }).click();
  await main.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.waitForTimeout(3500);
  await page.reload();
  await main.getByText("모의 응답: 재접속 질문").first().waitFor();
  check("R12b. no duplicate turn after reconnect", (await userMessages("재접속 질문").count()) === 1, String(await userMessages("재접속 질문").count()));
  const store = await page.evaluate(() => JSON.stringify({ ...sessionStorage }));
  check("R12b. pending send cleared after confirmation", store === "{}", store);
}

// R13. 좁은 화면: 가로 스크롤 없이 표시
await page.setViewportSize({ width: 360, height: 800 });
for (const [label, url] of [
  ["material", materialAUrl],
  ["document", docUrl],
  ["session", sessionUrl],
  ["research list", U("/research/?tab=materials")],
]) {
  await page.goto(url);
  await page.waitForLoadState("networkidle");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(`R13. ${label} fits 360px`, overflow <= 0, `overflow ${overflow}px`);
}
await page.setViewportSize({ width: 1280, height: 800 });

// R10. 다른 사용자: 타인 ID는 404로 숨김
{
  const other = await newPage();
  await login(other.page);
  const om = other.page.locator("main");
  await other.page.goto(materialAUrl);
  check("R10. foreign material -> not found", await appears(om.getByText("항목을 찾을 수 없습니다")));
  await other.page.goto(sessionUrl);
  check("R10. foreign session -> not found", await appears(om.getByText("항목을 찾을 수 없습니다")));
  await other.page.goto(U("/research/"));
  check("R10. other user's list empty", await appears(om.getByText("아직 저장한 자료가 없습니다")));
  await other.context.close();
}

// R11. 모든 research 변경 요청: Origin + X-CSRF-Token + UUID v4 Idempotency-Key (브라우저 요청)
{
  const uiMutations = (await mockCalls()).filter((c) => c.method !== "GET" && c.path.startsWith("/research/"));
  const bad = uiMutations.filter(
    (c) => c.origin !== ORIGIN || !c.csrf || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(c.idempotencyKey ?? "")
  );
  check("R11. mutations carry Origin/CSRF/Idempotency-Key", uiMutations.length > 0 && bad.length === 0, `${uiMutations.length} calls, bad=${JSON.stringify(bad.slice(0, 2))}`);
}
const ls = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
check("R11. nothing stored in local/sessionStorage", ls === "{}", ls);

check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
check("no external image/tracker requests", ![...externalHosts].some((h) => h.includes("tracker") || h.includes("example.com")), [...externalHosts].join(", "));

await context.close();
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
