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

// 결과 미확인 전송 보관분만(대화창 상태 chat-ui·작성 중 입력 chat-draft 제외)
const pendingSends = () =>
  page.evaluate(() => JSON.stringify(Object.fromEntries(Object.entries(sessionStorage).filter(([k]) => k.includes(":session-send:")))));

const browser = await chromium.launch();
const consoleErrors = [];
const externalHosts = new Set();

async function newPage() {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
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

await setMock("me=normal&health=normal&logout=normal&nextLogin=approved&access=pending&publish=allowed&codex=unavailable");
const { context, page } = await newPage();
const main = page.locator("main");
const api = apiClient(page);

// R1. 비로그인
await page.goto(U("/research/"));
check("R1. logged out /research/ asks login", await appears(main.getByText("로그인이 필요합니다")));
check("R1. no 내 연구 nav when logged out", (await page.locator("header").getByRole("link", { name: "내 연구" }).count()) === 0);

// R2. 서버가 policy_pending을 주는 상태(이전 정책 대기 재현)도 그대로 안내한다
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
// 편집 중 textarea도 같은 문구를 담으므로, 저장 완료(편집 종료 후 v2 표시)를 기다린다.
await main.getByRole("button", { name: "내용 수정" }).waitFor();
check("R4. updated content shown", await appears(main.locator(".prose").getByText("두 번째 내용")));
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

// R7. 응답 유실 후 재시도: 같은 Idempotency-Key → 중복 생성 없음. #22: 새 문서는 서버 초안을 commit해 만든다.
const COMMIT = "**/api/research/document-drafts/*/commit";
const isCommit = (c) => c.method === "POST" && /^\/research\/document-drafts\/[^/]+\/commit$/.test(c.path);
const before = await counts();
await page.goto(U("/research/document/new/"));
await main.getByLabel("제목").fill("재시도 문서");
await main.getByLabel("본문").fill("본문");
let dropped = false;
await page.route(COMMIT, async (route) => {
  if (!dropped) {
    dropped = true;
    await route.fetch(); // 서버에는 반영
    return route.abort(); // 브라우저는 응답을 받지 못함
  }
  return route.continue();
});
await main.getByRole("button", { name: "문서 저장" }).click();
check("R7. lost response shows failure", await appears(main.getByText("서버에 연결할 수 없습니다")));
check("R7. draft address kept for recovery", await page.waitForURL(/\?draft=/, { timeout: 8000 }).then(() => true, () => false), page.url());
await main.getByRole("button", { name: "문서 저장" }).click();
await page.waitForURL(/\/research\/document\/\?id=/);
await page.unroute(COMMIT);
const after = await counts();
const posts = (await mockCalls()).filter(isCommit).slice(-2);
check("R7. retry reused Idempotency-Key", posts.length === 2 && posts[0].idempotencyKey === posts[1].idempotencyKey, JSON.stringify(posts.map((p) => p.idempotencyKey)));
check("R7. exactly one document created", after.documents === before.documents + 1, `${before.documents} -> ${after.documents}`);

// R8. 공개(확정 정책): 글→자료 직접 연결 전체를 미리보기 후 공개, 간접 연결 제외, 미리보기 뒤 변경은 409 → 재미리보기,
//     보관 자료는 공개 차단(연결 해제로 해소), 최신 공개본만 열람, 철회
const docUrl = U(`/research/document/?id=${docD.body.id}`);
const newMaterial = (title) =>
  api.send("POST", "/research/materials", {
    title,
    sourceUrl: "https://example.com/" + encodeURIComponent(title),
    collectedAt: new Date().toISOString(),
    contentKind: "excerpt",
    content: `${title} 저장 내용`,
  });
const relate = (sourceType, sourceId, targetId, kind) =>
  api.send("POST", "/research/relations", {
    source: { type: sourceType, id: sourceId },
    target: { type: "material", id: targetId },
    kind,
    description: "",
    directed: true,
  });
const publishButton = () => main.getByRole("button", { name: /글과 참고 자료 \d+개 공개/ });
{
  const matC = await newMaterial("자료 C 간접");
  await relate("material", materialAId, matC.body.id, "간접"); // 자료 A → 자료 C (글 D와 직접 연결 없음)
  await page.goto(docUrl);
  await main.getByRole("button", { name: "공개하기" }).click();
  await publishButton().waitFor();
  const previewSection = main.locator("section").filter({ has: page.getByRole("heading", { name: "함께 공개될 참고 자료" }) }).last();
  const previewText = await previewSection.innerText();
  check("R8. preview lists direct materials", previewText.includes("자료 A") && previewText.includes("자료 B"));
  check("R8. preview excludes indirect material", !previewText.includes("자료 C 간접"));
  check("R8. preview counts 2", (await publishButton().innerText()).includes("2개"));
  // 미리보기 뒤 새 직접 연결 → 공개 시 409 → 최신 미리보기 자동 재조회(공개 재전송 없음)
  const matE = await newMaterial("자료 E");
  await relate("document", docD.body.id, matE.body.id, "추가");
  await publishButton().click();
  check("R8. stale preview -> refreshed notice", await appears(main.getByText("최신 미리보기를 다시 불러왔습니다")));
  check("R8. refreshed preview includes new material", await appears(main.getByRole("button", { name: "글과 참고 자료 3개 공개" })));
  // 보관된 직접 연결 자료 → 공개 차단, 연결 해제로 해소
  const matF = await newMaterial("자료 F 보관");
  await relate("document", docD.body.id, matF.body.id, "보관 예정");
  await api.send("DELETE", `/research/materials/${matF.body.id}`, { expectedVersion: matF.body.version });
  await main.getByRole("button", { name: "취소" }).click();
  await main.getByRole("button", { name: "공개하기" }).click();
  check("R8. archived reference shown", await appears(main.getByText("보관됨")));
  check("R8. archived reference blocks publish", await publishButton().isDisabled());
  await main.getByRole("button", { name: "이 글과의 연결 해제" }).click();
  check("R8. unlink restores publishable", await appears(main.getByRole("button", { name: "글과 참고 자료 3개 공개" })) && (await publishButton().isEnabled()));
  await publishButton().click();
  check("R8. published state", await appears(main.getByText("공개 중", { exact: true })));
}
{
  const visitor = await newPage();
  await visitor.page.goto(U(`/public/document/?id=${docD.body.id}`));
  const vm = visitor.page.locator("main");
  check("R8. public page shows document", await appears(vm.getByRole("heading", { name: "문서 D", exact: true })));
  const pubText = await vm.innerText();
  check("R8. all direct materials public", ["자료 A", "자료 B", "자료 E"].every((t) => pubText.includes(t)));
  check("R8. indirect/archived not public", !pubText.includes("자료 C 간접") && !pubText.includes("자료 F 보관"));
  await vm.getByText("저장된 내용 보기").first().click();
  check("R8. material stored content public", await appears(vm.getByText("저장 내용").first()) || (await vm.innerText()).includes("내용"));
  check("R8. public page offers login to chat", await appears(vm.getByRole("link", { name: /로그인하고 대화 시작/ })));
  // 최신 공개본만: 글 수정 후 다시 공개 → 이전 공개본 ID로는 열람 불가
  const before = await api.get(`/research/documents/${docD.body.id}`);
  const firstRelease = before.publication.id;
  await api.send("PATCH", `/research/documents/${docD.body.id}`, { title: "문서 D", content: "# 문서 D 두 번째 공개", expectedVersion: before.version });
  await page.goto(docUrl);
  await main.getByRole("button", { name: "새 내용으로 다시 공개" }).click();
  await publishButton().click();
  await main.getByText("공개 중", { exact: true }).waitFor();
  await visitor.page.reload();
  check("R8. readers see latest release", await appears(vm.getByText("문서 D 두 번째 공개")));
  const oldRelease = await visitor.page.request.get(U(`/api/public/releases/${firstRelease}`));
  check("R8. previous release id -> 404", oldRelease.status() === 404, String(oldRelease.status()));
  await page.goto(docUrl);
  await main.getByRole("button", { name: "공개 철회" }).click();
  check("R8. revoked -> 비공개", await appears(main.getByText("비공개 문서입니다")));
  await visitor.page.reload();
  check("R8. public page after revoke", await appears(vm.getByText("공개가 철회된 문서")));
  await visitor.context.close();
}

// R8c. 공개 중 문서를 보관해도 철회할 수 있다(리뷰 F1): 보관 → 철회 버튼 유지 → 철회 → 익명 404
{
  const g = await api.send("POST", "/research/documents", { title: "모의 보관 공개 문서", content: "보관 공개 본문" });
  const gid = g.body.id;
  const pvG = await api.get(`/research/documents/${gid}/publication-preview`);
  const pubG = await api.send("POST", `/research/documents/${gid}/publications`, { versionId: pvG.versionId, expectedVersion: pvG.expectedVersion, previewToken: pvG.previewToken });
  check("R8c. setup published", pubG.status === 201, String(pubG.status));
  await page.goto(U(`/research/document/?id=${gid}`));
  await main.getByRole("button", { name: "보관", exact: true }).click();
  await main.getByText("보관한 문서입니다").waitFor();
  check("R8c. archived+published still offers withdraw", await appears(main.getByRole("button", { name: "공개 철회" })));
  check("R8c. archived doc cannot be republished", (await main.getByRole("button", { name: /다시 공개|공개하기/ }).count()) === 0);
  const anonBefore = await (await browser.newContext()).request.get(U(`/api/public/documents/${gid}`));
  check("R8c. still public while archived", anonBefore.status() === 200, String(anonBefore.status()));
  await main.getByRole("button", { name: "공개 철회" }).click();
  check("R8c. withdrawn", await appears(main.getByText("비공개 문서입니다")));
  const anonCtx = await browser.newContext();
  const anonAfter = await anonCtx.request.get(U(`/api/public/documents/${gid}`));
  check("R8c. anonymous public read 404 after withdraw", anonAfter.status() === 404, String(anonAfter.status()));
  await anonCtx.close();
  const own = await api.get(`/research/documents/${gid}`);
  check("R8c. owner record preserved after withdraw", own.archived === true && own.publication === null && own.content === "보관 공개 본문");
}

// R8d. 보관 자료의 직접 관계 2개 해제 중 두 번째만 503 → 미리보기 재조회 → 남은 관계만 같은 key로 재시도(리뷰 F2)
{
  const h = await api.send("POST", "/research/documents", { title: "모의 연결 정리 문서", content: "본문" });
  const x = await api.send("POST", "/research/materials", {
    title: "모의 보관 자료 X",
    sourceUrl: "https://example.com/x",
    collectedAt: new Date().toISOString(),
    contentKind: "summary",
    content: "X 내용",
  });
  for (const kind of ["근거", "배경"]) {
    await api.send("POST", "/research/relations", {
      source: { type: "document", id: h.body.id },
      target: { type: "material", id: x.body.id },
      kind,
      description: "",
      directed: true,
    });
  }
  await api.send("DELETE", `/research/materials/${x.body.id}`, { expectedVersion: x.body.version });
  await page.goto(U(`/research/document/?id=${h.body.id}`));
  await main.getByRole("button", { name: "공개하기" }).click();
  await main.getByText("보관됨").waitFor();
  const deletes = [];
  let n = 0;
  await page.route("**/api/research/relations/*", async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    n += 1;
    deletes.push({ path: new URL(route.request().url()).pathname, key: route.request().headers()["idempotency-key"] });
    if (n === 2) {
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "not_ready", message: "Service is not ready" } }) });
    }
    return route.continue();
  });
  await main.getByRole("button", { name: "이 글과의 연결 해제" }).click();
  check("R8d. partial failure shown", await appears(main.getByText("연결을 해제하지 못했습니다")));
  await main.getByRole("button", { name: "이 글과의 연결 해제" }).click();
  check("R8d. retry clears remaining relation", await appears(main.getByRole("button", { name: "글과 참고 자료 0개 공개" })));
  check("R8d. publish no longer blocked", await main.getByRole("button", { name: "글과 참고 자료 0개 공개" }).isEnabled());
  await page.unroute("**/api/research/relations/*");
  check(
    "R8d. retry targeted only the failed relation with its original key",
    deletes.length === 3 && deletes[2].path === deletes[1].path && deletes[2].key === deletes[1].key && deletes[0].path !== deletes[1].path,
    JSON.stringify(deletes.map((d) => d.path.split("/").pop().slice(0, 8) + ":" + d.key.slice(0, 8)))
  );
}

// R8e. 두 번째 DELETE 503 + 이어지는 미리보기 재조회도 503 → 오래된 목록에서도 완료한 관계는 건너뛰고 실패 관계만 같은 key로(리뷰 F2 잔여)
{
  const h = await api.send("POST", "/research/documents", { title: "모의 재조회 실패 문서", content: "본문" });
  const x = await api.send("POST", "/research/materials", {
    title: "모의 보관 자료 Y",
    sourceUrl: "https://example.com/y",
    collectedAt: new Date().toISOString(),
    contentKind: "summary",
    content: "Y 내용",
  });
  for (const kind of ["근거", "배경"]) {
    await api.send("POST", "/research/relations", {
      source: { type: "document", id: h.body.id },
      target: { type: "material", id: x.body.id },
      kind,
      description: "",
      directed: true,
    });
  }
  await api.send("DELETE", `/research/materials/${x.body.id}`, { expectedVersion: x.body.version });
  await page.goto(U(`/research/document/?id=${h.body.id}`));
  await main.getByRole("button", { name: "공개하기" }).click();
  await main.getByText("보관됨").waitFor();
  const deletes = [];
  let n = 0;
  let failNextPreview = false;
  await page.route("**/api/research/relations/*", async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    n += 1;
    deletes.push({ path: new URL(route.request().url()).pathname, key: route.request().headers()["idempotency-key"] });
    if (n === 2) {
      failNextPreview = true;
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "not_ready", message: "Service is not ready" } }) });
    }
    return route.continue();
  });
  await page.route("**/api/research/documents/*/publication-preview*", async (route) => {
    if (failNextPreview) {
      failNextPreview = false;
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "not_ready", message: "Service is not ready" } }) });
    }
    return route.continue();
  });
  await main.getByRole("button", { name: "이 글과의 연결 해제" }).click();
  check("R8e. delete failure shown", await appears(main.getByText("연결을 해제하지 못했습니다")));
  check("R8e. preview refresh failure shown, last preview kept", await appears(main.getByText("최신 미리보기를 불러오지 못했습니다")) && (await main.getByText("보관됨").count()) > 0);
  await main.getByRole("button", { name: "이 글과의 연결 해제" }).click();
  check("R8e. retry clears remaining relation", await appears(main.getByRole("button", { name: "글과 참고 자료 0개 공개" })));
  await page.unroute("**/api/research/relations/*");
  await page.unroute("**/api/research/documents/*/publication-preview*");
  check(
    "R8e. stale list: completed relation skipped, failed relation resent with original key",
    deletes.length === 3 && deletes[2].path === deletes[1].path && deletes[2].key === deletes[1].key && deletes[0].path !== deletes[1].path,
    JSON.stringify(deletes.map((d) => d.path.split("/").pop().slice(0, 8) + ":" + d.key.slice(0, 8)))
  );
}

// R9. 세션: Codex 미연결 안내 → 전송 실패 시 입력 보존 → 모의 연결 후 이어하기·도구 이력·원본
// #44: 내 연구에 대화 탭이 없다. 대화는 공통 대화창에서만 시작한다(Codex 안내·새 대화 폼도 대화창에 있다).
const chat = page.locator("#chat-panel");
// 새로고침 뒤 대화창은 기억한 열림 상태로 다시 그려진다: 대화창이나 여는 버튼 중 하나가 정착할 때까지 보고 나서 연다
async function openChatPanel() {
  const button = page.getByRole("button", { name: "Codex 대화창 열기" });
  for (let i = 0; i < 50 && !(await chat.isVisible()); i++) {
    if (await button.isVisible()) await button.click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(200);
  }
  await chat.getByRole("heading", { name: "Codex 대화" }).waitFor();
}
await page.goto(U("/research/"));
check("R9. #44: research page has no conversation tab", (await main.getByRole("navigation", { name: "내 연구 구분" }).getByRole("link", { name: "대화" }).count()) === 0 && !(await main.innerText()).includes("나눈 대화"));
// 대화는 일반 화면(홈)에서 시작한다: 예전 대화 탭처럼 보낸 화면이 '일반'이고 소유자 도구 범위다
await page.goto(U("/"));
await openChatPanel();
check("R9. codex unavailable banner", await appears(chat.getByText("새 대화를 진행할 수 없지만")));
// R9a. 계약에 아직 없는 사유 코드와 서버 고정 모델: 형식 오류로 막지 않고 일반 안내 + 모델 표시(실제 응답 성공으로 표시하지 않음)
await setMock("codex=auth_expired");
await page.reload();
await openChatPanel();
check("R9a. auth_expired reason explained (operator reconnect)", await appears(chat.getByText("서버의 Codex 인증이 만료되어 재연결이 필요합니다")));
check("R9a. fixed model shown, no auto switch", await appears(chat.getByText("모델: gpt-6.1-sol (서버에서 고정)")) && (await appears(chat.getByText("다른 모델로 자동 전환하지 않습니다"))));
check("R9a. no login/API-key input offered", (await chat.getByLabel(/API|키|ChatGPT/).count()) === 0);
await setMock("codex=unavailable");
await page.reload();
await openChatPanel();
await chat.getByLabel("새 대화 제목").fill("세션 1");
await chat.getByRole("button", { name: "새 대화" }).click();
// 새 대화는 페이지를 옮기지 않고 공통 대화창에서 열린다. 기존 대화 링크는 그 대화를 대화창에 여는 호환 진입점이다.
await chat.getByLabel("메시지").waitFor();
check("R9. new session opens in chat panel without navigation", page.url() === U("/") && (await chat.getByRole("heading", { name: "세션 1" }).count()) === 1, page.url());
const chatUi = () =>
  page.evaluate(() => {
    const k = Object.keys(sessionStorage).find((key) => key.endsWith(":chat-ui"));
    return k ? JSON.parse(sessionStorage.getItem(k)) : null;
  });
const sessionUrl = U(`/research/session/?id=${(await chatUi()).sessionId}`);
// 서버가 접수 불가로 보고한 동안은 보내기를 잠그고 입력은 그대로 둔다. 운영자 재연결 뒤 '상태 다시 확인'으로 풀린다(새로고침 불필요).
{
  const posts = [];
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    posts.push(route.request().method());
    return route.continue();
  });
  await chat.getByLabel("메시지").fill("질문 하나");
  check("R9. send locked while codex unavailable", (await appears(chat.getByText("보내기가 잠겨 있습니다"))) && (await chat.getByRole("button", { name: "보내기" }).isDisabled()));
  await chat.locator("form").evaluate((f) => f.requestSubmit());
  await page.waitForTimeout(300);
  check("R9. locked composer sends nothing", posts.length === 0, String(posts.length));
  check("R9. message input preserved while locked", (await chat.getByLabel("메시지").inputValue()) === "질문 하나");
  await page.unroute("**/api/research/sessions/*/messages");
}
await setMock("codex=fixture");
await chat.getByRole("button", { name: "상태 다시 확인" }).click();
check("R9. recheck unlocks without reload", await appears(chat.getByText("테스트용 모의 Codex")));
check("R9. input still there after unlock", (await chat.getByLabel("메시지").inputValue()) === "질문 하나");
await chat.getByRole("button", { name: "보내기" }).click();
check("R9. running state shown", await appears(chat.getByText("Codex가 응답하는 중입니다.")));
await page.reload(); // 진행 중 재접속
check("R9. reply after reconnect (polling)", await appears(chat.getByText("모의 응답:"), 15000));
// 홈은 general: #14부터 소유자 도구 전부라 모의 모델의 research_list 호출이 실행된다(거부는 R22 공개 글).
check("R9. user message shows sent page hint (general)", await appears(chat.locator("li[data-role=user]", { hasText: "질문 하나" }).getByText("보낸 화면: 일반")));
check("R9. owner tool call completed on general page", await appears(chat.locator("li", { hasText: "research_list" }).last().getByText("completed")));
await chat.getByRole("button", { name: /research_list/ }).first().click();
check("R9. tool input shown inert", await appears(chat.locator("pre", { hasText: '"query": "질문 하나"' })));
await chat.getByRole("button", { name: "저장된 원본 기록 보기" }).click();
check("R9. stored raw item shown", await appears(chat.locator("pre", { hasText: '"kind": "function_call"' })));

// R9b. runner 명확 거절: 202 후 failed/codex_rejected, 입력은 '미기록 입력'으로 보존(모델 이력·성공으로 표시 안 함)
{
  await setMock("codex=reject");
  await chat.getByLabel("메시지").fill("거절 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  check("R9b. failed codex_rejected shown", await appears(chat.getByText("Codex가 이 요청을 받지 않았습니다")));
  const rejected = chat.locator("li[data-role=user]", { hasText: "거절 질문" });
  check("R9b. platform input labeled 미기록", await appears(rejected.getByText("미기록 입력")));
  check("R9b. not presented as Codex record", await appears(rejected.getByText("Codex 대화 기록에 반영되지 않은 입력입니다")));
  await rejected.getByRole("button", { name: "보존된 입력 기록" }).click();
  check("R9b. raw shows platformInput, not Codex original", await appears(rejected.locator("pre", { hasText: '"type": "platformInput"' })) && (await rejected.getByText("Codex 원본 아님").count()) === 1);
  await setMock("codex=fixture");
  await chat.getByLabel("메시지").fill("거절 후 새 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  check("R9b. new explicit message after failure works", await appears(chat.getByText("모의 응답: 거절 후 새 질문"), 15000));
  check("R9b. rejected input still preserved after new turn", (await chat.locator("li[data-role=user]", { hasText: "거절 질문" }).count()) === 1);
}

// R9g. #7 선접수 실패(503 codex_*)는 확정 거절: 안내 + 입력 유지 + 같은 key 재전송 대상 아님 / 접수 후 모델 불일치는 명시 실패
{
  // 화면을 연 뒤에 접수 불가가 된 경우(열어 둔 화면에서 만료 등): 서버 선접수 거절 → 안내·입력 유지 → 상태 재확인으로 잠금
  for (const [mode, text] of [
    ["auth_expired", "서버의 Codex 인증이 만료되어"],
    ["not_enabled_for_account", "이 계정에는 아직 Codex가 열려 있지 않습니다"],
    ["model_unavailable", "고정된 모델을 사용할 수 없어"],
    ["budget_exhausted", "사용 한도에 도달해"],
    ["rate_limited", "제공자의 사용 한도에 걸려"],
  ]) {
    await setMock("codex=fixture");
    await page.reload();
    await chat.getByLabel("메시지").waitFor();
    await chat.getByText("테스트용 모의 Codex").waitFor();
    await setMock(`codex=${mode}`);
    await chat.getByLabel("메시지").fill(`${mode} 질문`);
    await chat.getByRole("button", { name: "보내기" }).click();
    check(`R9g. ${mode}: specific guidance`, await appears(chat.getByText(text).first()));
    check(`R9g. ${mode}: input preserved`, (await chat.getByLabel("메시지").inputValue()) === `${mode} 질문`);
    check(
      `R9g. ${mode}: definitive (no same-key resend, nothing stored)`,
      (await chat.getByRole("button", { name: "다시 보내기" }).count()) === 0 && !(await pendingSends()).includes(`${mode} 질문`)
    );
    check(`R9g. ${mode}: status rechecked -> send locked`, await appears(chat.getByText("보내기가 잠겨 있습니다")));
  }
  check("R9g. rate limit reason shown in status", await appears(chat.getByText("Codex 제공자의 사용 한도에 걸려 지금은 사용할 수 없습니다")));
  // 계약에 없는 codex_* 503은 결과 불명: 같은 key·본문 보관, "다시 보내기" 제공, 새로고침 뒤 입력 복구(리뷰 F7-1)
  {
    await setMock("codex=unknown_code");
    await page.reload();
    await chat.getByLabel("메시지").waitFor();
    const keys = [];
    await page.route("**/api/research/sessions/*/messages", async (route) => {
      keys.push(route.request().headers()["idempotency-key"]);
      return route.continue();
    });
    await chat.getByLabel("메시지").fill("미지 코드 질문");
    await chat.getByRole("button", { name: "보내기" }).click();
    check("R9g. unknown codex_* 503 -> same-request retry offered", await appears(chat.getByRole("button", { name: "다시 보내기" })));
    await page.reload();
    await chat.getByLabel("메시지").waitFor();
    check("R9g. unknown codex_* 503 -> input restored after reload", (await chat.getByLabel("메시지").inputValue()) === "미지 코드 질문");
    await setMock("codex=fixture");
    await chat.getByRole("button", { name: "다시 보내기" }).click();
    await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
    check("R9g. retry after unknown code reused original key", keys.length === 2 && keys[0] === keys[1], JSON.stringify(keys.map((k) => k.slice(0, 8))));
    await page.unroute("**/api/research/sessions/*/messages");
    await chat.getByText("모의 응답: 미지 코드 질문").waitFor({ timeout: 15000 });
    await chat.getByLabel("메시지").and(chat.locator(":enabled")).waitFor({ timeout: 15000 });
  }
  await setMock("codex=mismatch");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  const answersBefore = await chat.getByText("모의 응답:").count();
  await chat.getByLabel("메시지").fill("모델 불일치 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  check(
    "R9g. model mismatch -> explicit model failure, no auto switch",
    (await appears(chat.getByText("마지막 요청이 실패했습니다"))) && (await appears(chat.getByText("고정된 모델을 사용할 수 없어")))
  );
  check("R9g. raw provider text not shown", !(await chat.innerText()).includes("gpt-other"));
  const mm = chat.locator("li[data-role=user]", { hasText: "모델 불일치 질문" });
  check("R9g. mismatched turn items badged as not a normal response", await appears(mm.getByText("정상 응답 아님")));
  check("R9g. mismatched tool call badged", await appears(chat.locator("li", { hasText: "research_document_save" }).filter({ hasText: "정상 응답 아님" })));
  check("R9g. no assistant answer shown for mismatched turn", (await chat.getByText("모의 응답:").count()) === answersBefore);
  // 차단은 계정 runner 단위로 지속: turn 실패 뒤 상태를 다시 읽어 보내기 잠금, 입력 유지, 새 대화 안내 없음
  // (서버 선접수 거절 자체는 위 model_unavailable 반복에서 확인)
  await chat.getByLabel("메시지").fill("차단 뒤 질문");
  check("R9g. follow-up locked while runner blocked (status rechecked after failed turn)", (await appears(chat.getByText("보내기가 잠겨 있습니다"))) && (await chat.getByRole("button", { name: "보내기" }).isDisabled()));
  check("R9g. follow-up input kept", (await chat.getByLabel("메시지").inputValue()) === "차단 뒤 질문");
  check("R9g. no 'start a new conversation' workaround suggested", !(await chat.innerText()).includes("새 대화를 시작"));
  await page.reload();
  check("R9g. status explains operator confirmation", await appears(chat.getByText("운영자가 확인한 뒤 다시 사용할 수 있습니다")));
  // 이전 정상 이력은 그대로(일괄 실패 처리하지 않음)
  check("R9g. earlier normal answers kept", (await chat.getByText("모의 응답:").count()) === answersBefore);
  await setMock("codex=fixture");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  await chat.getByLabel("메시지").fill("");
}

// R9h. 접수(202) 뒤 실행 중 인증 실패(back access-only 계약): 만료 → codex_auth_expired, 외부 갱신 요구 → codex_auth_revoked.
//      provider 원문 비노출, 입력은 기록 여부대로 표시, 자동 재실행 없음, 상태 재확인으로 잠금, 운영자 재공급 뒤 같은 대화에서 이어짐
{
  for (const [mode, text, recorded] of [
    ["midturn_expired", "서버의 Codex 인증이 만료되어", false],
    ["midturn_revoked", "서버의 Codex 인증이 더 이상 유효하지 않아", true],
  ]) {
    await setMock("codex=fixture");
    await page.reload();
    await chat.getByLabel("메시지").waitFor();
    const before = await chat.locator("ol > li").count();
    const posts = [];
    await page.route("**/api/research/sessions/*/messages", async (route) => {
      posts.push(route.request().headers()["idempotency-key"]);
      return route.continue();
    });
    await setMock(`codex=${mode}`);
    await chat.getByLabel("메시지").fill(`${mode} 질문`);
    await chat.getByRole("button", { name: "보내기" }).click();
    check(`R9h. ${mode}: failed turn explained`, (await appears(chat.getByText("마지막 요청이 실패했습니다"))) && (await appears(chat.getByText(text).first())));
    check(`R9h. ${mode}: provider text not shown`, !(await chat.innerText()).includes("acct_secret") && !(await chat.innerText()).includes("token_expired"));
    const item = chat.locator("li[data-role=user]", { hasText: `${mode} 질문` });
    check(
      `R9h. ${mode}: input shown as ${recorded ? "Codex record" : "unrecorded platform input"}`,
      (await item.count()) === 1 && (await item.getByText("미기록 입력").count()) === (recorded ? 0 : 1)
    );
    check(`R9h. ${mode}: status rechecked -> send locked`, await appears(chat.getByText("보내기가 잠겨 있습니다")));
    await page.waitForTimeout(500);
    check(`R9h. ${mode}: no automatic resend`, posts.length === 1, String(posts.length));
    await page.unroute("**/api/research/sessions/*/messages");
    // 운영자 재공급(같은 계정) 뒤: 상태 재확인 → 같은 대화에 새 메시지
    await setMock("codex=fixture");
    await chat.getByRole("button", { name: "상태 다시 확인" }).click();
    await chat.getByText("테스트용 모의 Codex").waitFor();
    await chat.getByLabel("메시지").fill(`${mode} 재개 질문`);
    await chat.getByRole("button", { name: "보내기" }).click();
    check(`R9h. ${mode}: same conversation continues after resupply`, await appears(chat.getByText(`모의 응답: ${mode} 재개 질문`), 15000));
    check(`R9h. ${mode}: earlier items kept`, (await chat.locator("ol > li").count()) >= before + 3);
    await chat.getByLabel("메시지").and(chat.locator(":enabled")).waitFor({ timeout: 15000 });
  }
}

// R9w. 시험 창: 접수 종료 시각·남은 횟수 표시, 종료 시각이 지나면 화면을 그대로 둔 채 상태 재확인 → 잠금
{
  await setMock("codex=fixture&window=10&remaining=2");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  check("R9w. access window end shown", await appears(chat.getByText(/이용 가능 기한: .+까지/)));
  check("R9w. remaining admissions shown", await appears(chat.getByText("남은 요청 횟수: 2")));
  await chat.getByLabel("메시지").fill("창 안 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("모의 응답: 창 안 질문").waitFor({ timeout: 15000 });
  check("R9w. remaining admissions refreshed after turn", await appears(chat.getByText("남은 요청 횟수: 1")));
  await chat.getByLabel("메시지").fill("창 끝난 뒤 질문");
  check("R9w. window end -> locked without reload", await appears(chat.getByText("보내기가 잠겨 있습니다"), 15000));
  check("R9w. expired reason shown", await appears(chat.getByText("서버의 Codex 인증이 만료되어 재연결이 필요합니다")));
  check("R9w. input kept after window end", (await chat.getByLabel("메시지").inputValue()) === "창 끝난 뒤 질문");
  await setMock("codex=fixture&window=&remaining=");
  await chat.getByRole("button", { name: "상태 다시 확인" }).click();
  await chat.getByText("테스트용 모의 Codex").waitFor();
  await chat.getByLabel("메시지").fill("");
}

// R9x. 브라우저 시계가 서버보다 빠른 경우(리뷰 ACCESS-UI-R1): 기한 재확인이 일찍 '이용 가능'으로 끝나도
//      간격을 늘려 다시 물어 서버가 만료를 알리면 잠근다. 상태 조회만 하고 전송은 하지 않는다.
{
  const sk = await context.newPage();
  sk.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(`[skew] ${m.text()}`);
  });
  await sk.addInitScript(() => {
    const real = Date.now;
    Date.now = () => real() + 60000;
  });
  const reads = [];
  const posts = [];
  // 화면이 기한을 보인 뒤 첫 재확인은 "아직 이용 가능"(마지막 응답 그대로)으로 고정한다. 느린 환경에서 첫 재확인이
  // 이미 서버 만료 뒤에 도착해 이른 응답 경로를 건너뛰지 않게 한다(리뷰 c0815aa 질문). 이후 조회는 실제 모의 응답.
  // 각 조회의 시각(모의 기한 설정 기준 초)과 응답(ok/expired, 고정 응답은 *)을 남겨 실패 시 원인을 구분한다.
  let armed = false;
  let lastBody = null;
  let forced = 0;
  let t0 = 0;
  const trace = () => reads.map((r) => `${(r.at / 1000).toFixed(1)}s:${r.available ? "ok" : "expired"}${r.forced ? "*" : ""}`).join(" ");
  await sk.route("**/api/research/codex/status", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    if (armed && forced === 0 && lastBody?.available === true) {
      forced++;
      reads.push({ at: Date.now() - t0, available: true, forced: true });
      return route.fulfill({ response: res, json: lastBody });
    }
    reads.push({ at: Date.now() - t0, available: body.available === true });
    lastBody = body;
    return route.fulfill({ response: res, json: body });
  });
  await sk.route("**/api/research/sessions/*/messages", async (route) => {
    posts.push(1);
    return route.continue();
  });
  t0 = Date.now();
  await setMock("codex=fixture&window=8");
  await sk.goto(sessionUrl);
  const sm = sk.locator("#chat-panel");
  await sm.getByText(/이용 가능 기한/).waitFor();
  armed = true;
  const readsBefore = reads.length;
  await sm.getByLabel("메시지").fill("시계 차이 질문");
  check("R9x. clock ahead: locked once server reports expiry", await appears(sm.getByText("보내기가 잠겨 있습니다"), 40000), trace());
  check("R9x. clock ahead: early recheck answered available (setup)", forced === 1, trace());
  check("R9x. clock ahead: rechecked again after early answer", reads.length - readsBefore >= 2 && reads.at(-1)?.available === false, trace());
  check("R9x. clock ahead: no send, draft kept", posts.length === 0 && (await sm.getByLabel("메시지").inputValue()) === "시계 차이 질문");
  await sk.close();
}

// R9y. 기한 재확인이 일시 실패해도(네트워크) 마지막 상태를 표시한 채 다시 물어, 회복 뒤 만료를 반영해 잠근다.
{
  await setMock("codex=fixture&window=4");
  await page.goto(sessionUrl);
  await chat.getByText(/이용 가능 기한/).waitFor();
  let failing = true;
  let aborted = 0;
  await page.route("**/api/research/codex/status", async (route) => {
    if (failing) {
      aborted++;
      return route.abort();
    }
    return route.continue();
  });
  await chat.getByLabel("메시지").fill("조회 실패 질문");
  check("R9y. failed expiry recheck shown, last status kept", await appears(chat.getByText("Codex 상태를 다시 확인하지 못했습니다"), 20000));
  check("R9y. not locked from a failed read alone", !(await chat.getByRole("button", { name: "보내기" }).isDisabled()));
  await page.waitForTimeout(8000);
  failing = false;
  check("R9y. recheck retried after failure", aborted >= 2, String(aborted));
  check("R9y. locked after reads recover", await appears(chat.getByText("보내기가 잠겨 있습니다"), 40000));
  check("R9y. draft kept", (await chat.getByLabel("메시지").inputValue()) === "조회 실패 질문");
  await page.unroute("**/api/research/codex/status");
  await setMock("codex=fixture&window=&remaining=");
  await chat.getByRole("button", { name: "상태 다시 확인" }).click();
  await chat.getByText("테스트용 모의 Codex").waitFor();
  await chat.getByLabel("메시지").fill("");
}

// R12. 메시지 응답 유실 → polling으로 서버 version 변경 → 같은 내용 재전송: 모델 turn 중복 없음
const userMessages = (t) => chat.locator("li[data-role=user]", { hasText: t });
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
  await chat.getByLabel("메시지").waitFor();
  await dropFirstMessagePost();
  await chat.getByLabel("메시지").fill("중복 방지 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  check("R12. lost send -> uncertain notice", await appears(chat.getByText("이전 전송의 결과를 확인하지 못했습니다")));
  check("R12. turn completed on server meanwhile", await appears(chat.getByText("모의 응답: 중복 방지 질문"), 15000));
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.waitForTimeout(3500);
  await page.reload();
  await chat.getByText("모의 응답: 중복 방지 질문").first().waitFor();
  check("R12. exactly one user message after retry", (await userMessages("중복 방지 질문").count()) === 1, String(await userMessages("중복 방지 질문").count()));
  const sends = (await mockCalls()).filter((c) => c.method === "POST" && c.path.endsWith("/messages")).slice(-2);
  check("R12. retry reused same Idempotency-Key", sends.length === 2 && sends[0].idempotencyKey === sends[1].idempotencyKey);
  await page.unroute("**/api/research/sessions/*/messages");
}
{
  // R12b. 응답 유실 후 페이지 재접속: 입력과 key가 복구되어 같은 요청으로 재전송
  await dropFirstMessagePost();
  await chat.getByLabel("메시지").fill("재접속 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("이전 전송의 결과를 확인하지 못했습니다").waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  await appears(chat.getByText("모의 응답: 재접속 질문"), 15000);
  check("R12b. pending text restored after reload", (await chat.getByLabel("메시지").inputValue()) === "재접속 질문");
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.waitForTimeout(3500);
  await page.reload();
  await chat.getByText("모의 응답: 재접속 질문").first().waitFor();
  check("R12b. no duplicate turn after reconnect", (await userMessages("재접속 질문").count()) === 1, String(await userMessages("재접속 질문").count()));
  const store = await pendingSends();
  check("R12b. pending send cleared after confirmation", store === "{}", store);
}

// R12c~e. 인증 오류는 이전 전송의 미처리를 증명하지 않는다: 보관 유지 → 같은 계정 재시도는 replay
// 결과 미확인 변경 요청 보관분 전체(대화창 상태·작성 중 입력 제외). 작성 중 입력은 별도로 남으므로 섞지 않는다.
const pendingStore = () =>
  page.evaluate(() =>
    JSON.stringify(Object.fromEntries(Object.entries(sessionStorage).filter(([k]) => !k.endsWith(":chat-ui") && !k.includes(":chat-draft:"))))
  );
const fullStore = () => page.evaluate(() => JSON.stringify({ ...sessionStorage }));
async function makeUnconfirmedSend(text) {
  await dropFirstMessagePost();
  await chat.getByLabel("메시지").fill(text);
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("이전 전송의 결과를 확인하지 못했습니다").waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await chat.getByText(`모의 응답: ${text}`).first().waitFor({ timeout: 15000 });
}
async function confirmSingleTurn(label, text) {
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.waitForTimeout(3500);
  await page.reload();
  await chat.getByText(`모의 응답: ${text}`).first().waitFor();
  check(`${label}: exactly one user message`, (await userMessages(text).count()) === 1, String(await userMessages(text).count()));
}
{
  // R12c. 재시도가 CSRF 403으로 거절돼도 보관 유지
  await makeUnconfirmedSend("CSRF 질문");
  await setMock("csrf=reject");
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  check("R12c. csrf_invalid shown", await appears(chat.getByText("요청을 확인하지 못했습니다")));
  check("R12c. unconfirmed send kept after 403", (await pendingStore()).includes("CSRF 질문"));
  check("R12c. still offers same-request retry", await appears(chat.getByRole("button", { name: "다시 보내기" })));
  await setMock("csrf=normal");
  await confirmSingleTurn("R12c. retry after csrf recovery", "CSRF 질문");
}
{
  // R12d. 재시도가 401(session 만료)이어도 보관 유지 → 같은 계정 재로그인 후 같은 key로 replay
  await makeUnconfirmedSend("만료 질문");
  await setMock("expire=1");
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  check("R12d. expired session -> login required", await appears(chat.getByText("로그인이 필요합니다")));
  check("R12d. unconfirmed send kept after 401", (await pendingStore()).includes("만료 질문"));
  await setMock("reuse=1");
  await login(page);
  await page.goto(sessionUrl);
  await chat.getByLabel("메시지").waitFor();
  check("R12d. same account restores pending text", (await chat.getByLabel("메시지").inputValue()) === "만료 질문");
  await confirmSingleTurn("R12d. retry after re-login", "만료 질문");
}
{
  // R12e. 명시적 로그아웃은 미확인 전송(입력 포함)을 지운다
  await makeUnconfirmedSend("로그아웃 질문");
  await page.locator("header").getByRole("button", { name: "로그아웃", exact: true }).first().click();
  await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
  check("R12e. logout clears unconfirmed sends, drafts and chat state", (await fullStore()) === "{}", await fullStore());
  await login(page); // reuse=1: 같은 계정으로 이후 단계 계속
  await setMock("reuse=0");
}

// R22. #9 공통 대화창(모의 API): 모든 페이지 진입점, 열고 닫기·포커스, 페이지 이동 뒤 대화·입력 유지,
//      페이지 범위 송신·표시·거부 도구, 실행 중 이동, 재전송 범위 고정, 자원 없음 404, 단일 polling, 모바일, 비로그인.
//      실제 모델·도구·서버 권한 검증이 아니다(서버 거부는 back 검사와 실제 시험에서 따로 확인).
{
  await setMock("codex=fixture&window=&remaining=");
  const dock = page.getByRole("button", { name: "Codex 대화창 열기" });
  const header = page.locator("header");
  for (const path of ["/", "/posts/", "/status/", "/public/", "/research/?tab=materials"]) {
    await page.goto(U(path));
    if (await chat.isVisible()) await chat.getByRole("button", { name: "대화창 닫기" }).click();
    const box = (await appears(dock)) ? await dock.boundingBox() : null;
    check(`R22. dock button on ${path} (>=44px)`, !!box && box.width >= 44 && box.height >= 44, JSON.stringify(box));
  }
  await dock.click();
  await chat.getByRole("heading", { name: "Codex 대화" }).waitFor();
  check("R22. open moves focus into panel", await page.evaluate(() => document.activeElement?.id === "chat-panel-title"));
  check("R22. desktop panel is non-modal", (await chat.getAttribute("role")) === "complementary" && (await chat.getAttribute("aria-modal")) === null);
  check("R22. desktop page padded so panel does not cover content", await page.evaluate(() => getComputedStyle(document.body).paddingRight === "416px"));
  await page.keyboard.press("Escape");
  await dock.waitFor();
  check("R22. Esc closes and returns focus to button", await page.evaluate(() => document.activeElement?.getAttribute("aria-label") === "Codex 대화창 열기"));
  check("R22. padding removed after close", await page.evaluate(() => getComputedStyle(document.body).paddingRight === "0px"));

  // 대화 선택·입력 → 닫기 → 클라이언트 이동 → 다시 열기 / 새로고침
  await dock.click();
  await chat.getByRole("link", { name: /세션 1/ }).click();
  await chat.getByLabel("메시지").waitFor();
  check("R22. list click opens conversation in panel without navigation", page.url().includes("/research/") && !page.url().includes("/research/session/"));
  await chat.getByLabel("메시지").fill("이동 중 초안");
  await chat.getByRole("button", { name: "대화창 닫기" }).click();
  await header.getByRole("link", { name: "글 목록" }).first().click();
  await page.waitForURL(/\/posts\/$/);
  await dock.click();
  check("R22. draft kept across close + client navigation", (await chat.getByLabel("메시지").inputValue()) === "이동 중 초안");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  check("R22. open state and draft kept after reload", (await chat.getByLabel("메시지").inputValue()) === "이동 중 초안");

  const bodies = [];
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    bodies.push(route.request().postDataJSON());
    return route.continue();
  });
  const lastTool = () => chat.locator("li", { hasText: "research_list" }).last();

  // general 페이지: #14부터 소유자 도구 전부(화면은 참고일 뿐)
  check("R22. general page hint shown", await appears(chat.getByText("보고 있는 화면: 일반")));
  check("R22. owner wording: no per-page tool limits", await appears(chat.getByText("연결 관계와 보관도 처리합니다", { exact: false })));
  check("R22. web search saving wording", await appears(chat.getByText("출처 주소와 수집 시각을 붙여 자료로 저장합니다", { exact: false })));
  check("R22. shell wording without fixed limit number (#23)", await appears(chat.getByText("명령은 한 번에 하나씩 시간 상한 안에서 실행됩니다", { exact: false })) && !/30\s*초/.test(await chat.innerText()));
  check("R22. memory wording: earlier conversation continues", await appears(chat.getByText("이전 대화 내용은 이 대화에 계속 이어집니다.")));
  await chat.getByLabel("메시지").fill("일반 페이지 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("모의 응답: 일반 페이지 질문").waitFor({ timeout: 15000 });
  check("R22. general page sends kind general", JSON.stringify(bodies.at(-1)?.page) === '{"kind":"general"}', JSON.stringify(bodies.at(-1)));
  check("R22. general: draft cleared after success", (await chat.getByLabel("메시지").inputValue()) === "");
  check("R22. general: owner tool completed", await appears(lastTool().getByText("completed")));

  // 자료 목록(작업 공간): research_list 허용
  await page.goto(U("/research/?tab=materials"));
  await chat.getByLabel("메시지").waitFor();
  check("R22. workspace hint shown", await appears(chat.getByText("보고 있는 화면: 내 자료")));
  await chat.getByLabel("메시지").fill("자료 목록 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("모의 응답: 자료 목록 질문").waitFor({ timeout: 15000 });
  check("R22. workspace sends material_workspace", JSON.stringify(bodies.at(-1)?.page) === '{"kind":"material_workspace"}', JSON.stringify(bodies.at(-1)?.page));
  check("R22. workspace: allowed tool completed", await appears(lastTool().getByText("completed")));

  // 자료 상세: 보낸 뒤 바로 다른 페이지로 이동해도 접수 범위는 그대로
  const materialId = new URL(materialAUrl).searchParams.get("id");
  await page.goto(materialAUrl);
  await chat.getByLabel("메시지").waitFor();
  check("R22. material page hint shown", await appears(chat.getByText("보고 있는 화면: 이 자료")));
  await chat.getByLabel("메시지").fill("자료 페이지 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("Codex가 응답하는 중입니다.").waitFor();
  await header.getByRole("link", { name: "홈" }).first().click();
  await page.waitForURL(U("/"));
  await chat.getByText("모의 응답: 자료 페이지 질문").waitFor({ timeout: 15000 });
  check("R22. material page sends material + id", bodies.at(-1)?.page?.kind === "material" && bodies.at(-1)?.page?.resourceId === materialId, JSON.stringify(bodies.at(-1)?.page));
  check("R22. navigation during run: sent page stays material", await appears(chat.locator("li[data-role=user]", { hasText: "자료 페이지 질문" }).getByText(/보낸 화면: 이 자료/)));
  check("R22. next hint follows current page", await appears(chat.getByText("보고 있는 화면: 일반")));
  check("R22. material page: list tool not limited by page", await appears(lastTool().getByText("completed")));

  // 공개 글: 유일한 도구 경계. 소유자 도구 호출은 서버가 거부한다.
  const pub22 = await api.send("POST", "/research/documents", { title: "범위 공개 글", content: "공개 본문" });
  const pv22 = await api.get(`/research/documents/${pub22.body.id}/publication-preview`);
  const pubd22 = await api.send("POST", `/research/documents/${pub22.body.id}/publications`, {
    versionId: pv22.versionId,
    expectedVersion: pv22.expectedVersion,
    previewToken: pv22.previewToken,
  });
  check("R22. setup: publication", pubd22.status === 201, String(pubd22.status));
  await page.goto(U(`/public/document/?id=${pub22.body.id}`));
  await chat.getByLabel("메시지").waitFor();
  check("R22. public document scope shown", await appears(chat.getByText("다음 질문 범위: 이 공개 글")));
  check("R22. public wording: read only, no web search or shell", await appears(chat.getByText("웹 검색·명령 실행이나 저장·수정은 하지 않습니다", { exact: false })));
  await chat.getByLabel("메시지").fill("공개 글 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("모의 응답: 공개 글 질문").waitFor({ timeout: 15000 });
  check("R22. public: sent scope badge", await appears(chat.locator("li[data-role=user]", { hasText: "공개 글 질문" }).getByText(/보낸 범위: 이 공개 글/)));
  check("R22. public: owner tool rejected", await appears(lastTool().getByText("거부됨")));
  check("R22. public: rejection wording", await appears(lastTool().getByText("허용되지 않는 도구라 서버가 실행하지 않았습니다", { exact: false })));

  // R22s. #23 shell 표시(api-contracts/issue23-shell-display.md): 공개 글에서는 거부, 소유자 화면에서는 결과별 안내
  const shellItem = (kind) => chat.locator("li", { hasText: `fixture-${kind} --run` }).last();
  const ask = async (text) => {
    await chat.getByLabel("메시지").fill(text);
    await chat.getByRole("button", { name: "보내기" }).click();
    await chat.getByText(`모의 응답: ${text}`).waitFor({ timeout: 15000 });
  };
  await ask("/fixture/shell-ok 공개");
  check("R22s. public: shell shown as command run", await appears(shellItem("ok").getByText("명령 실행")));
  check("R22s. public: shell rejected with scope wording", (await appears(shellItem("ok").getByText("거부됨"))) && (await appears(shellItem("ok").getByText("명령을 실행할 수 없어 서버가 실행하지 않았습니다", { exact: false }))));
  await page.goto(U("/"));
  await chat.getByLabel("메시지").waitFor();
  await ask("/fixture/shell-ok 홈");
  check("R22s. ok: completed label, no warning", (await appears(shellItem("ok").getByText("완료", { exact: true }))) && (await shellItem("ok").locator("p.text-amber-800").count()) === 0);
  await shellItem("ok").getByRole("button", { name: /명령 실행/ }).click();
  check(
    "R22s. ok: command, cwd, exit code and stdout shown inert",
    (await appears(shellItem("ok").locator("pre", { hasText: "fixture-ok --run" }))) &&
      (await appears(shellItem("ok").getByText("작업 공간 notes"))) &&
      (await appears(shellItem("ok").getByText("종료 코드 0"))) &&
      (await appears(shellItem("ok").locator("pre", { hasText: "shell fixture output" })))
  );
  await ask("/fixture/shell-fail");
  check("R22s. fail: exit code explained", (await appears(shellItem("fail").getByText("실패", { exact: true }))) && (await appears(shellItem("fail").getByText("종료 코드 2로 끝났습니다", { exact: false }))));
  await shellItem("fail").getByRole("button", { name: /명령 실행/ }).click();
  check("R22s. fail: stderr shown", await appears(shellItem("fail").locator("pre", { hasText: "No such file or directory" })));
  await ask("/fixture/shell-timeout");
  check(
    "R22s. timeout: label, limit from result, truncation",
    (await appears(shellItem("timeout").getByText("시간 초과", { exact: true }))) &&
      (await appears(shellItem("timeout").getByText("시간 상한(30초)을 넘어 명령을 멈췄습니다", { exact: false }))) &&
      (await appears(shellItem("timeout").getByText("저장 상한까지만", { exact: false })))
  );
  await ask("/fixture/shell-busy");
  check("R22s. busy: rejected, one at a time", (await appears(shellItem("busy").getByText("거부됨"))) && (await appears(shellItem("busy").getByText("한 번에 하나씩", { exact: false }))));
  await ask("/fixture/shell-cancel");
  check("R22s. cancel: no resource-limit guess", (await appears(shellItem("cancel").getByText("중간에 취소되었습니다", { exact: false }))) && !/메모리|CPU|자원/.test(await shellItem("cancel").innerText()));
  await page.unroute("**/api/research/sessions/*/messages");

  // 기존 대화 링크: 대화창에서 열고 입력창·polling은 하나
  const sid = new URL(sessionUrl).searchParams.get("id");
  await page.goto(sessionUrl);
  await chat.getByLabel("메시지").waitFor();
  check(
    "R22. legacy session link opens it in panel, single composer",
    (await page.locator('textarea[aria-label="메시지"]').count()) === 1 && (await appears(main.getByText("이 대화를 대화창에서 열었습니다")))
  );
  let polls = 0;
  const onReq = (r) => {
    if (r.method() === "GET" && new URL(r.url()).pathname.endsWith(`/research/sessions/${sid}`)) polls++;
  };
  page.on("request", onReq);
  await chat.getByLabel("메시지").fill("폴링 확인 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("모의 응답: 폴링 확인 질문").waitFor({ timeout: 15000 });
  page.off("request", onReq);
  check("R22. single poller for the running conversation", polls >= 1 && polls <= 4, String(polls));

  // 결과 불명 재전송: 다른 페이지로 옮겨도 처음 key·body(page 포함)를 그대로 보낸다
  await page.goto(materialAUrl);
  await chat.getByLabel("메시지").waitFor();
  const sent = [];
  let dropped = false;
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    sent.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    if (!dropped) {
      dropped = true;
      await route.fetch();
      return route.abort();
    }
    return route.continue();
  });
  await chat.getByLabel("메시지").fill("범위 고정 재전송");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("이전 전송의 결과를 확인하지 못했습니다").waitFor();
  await chat.getByText("모의 응답: 범위 고정 재전송").first().waitFor({ timeout: 15000 });
  await header.getByRole("link", { name: "상태" }).first().click();
  await page.waitForURL(/\/status\/$/);
  check("R22. retry notice: original page is used", await appears(chat.getByText("처음 보낸 화면(이 자료) 기준으로 처리됩니다", { exact: false })));
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  check(
    "R22. retry after navigation reuses original key and body",
    sent.length === 2 && sent[0].key === sent[1].key && JSON.stringify(sent[0].body) === JSON.stringify(sent[1].body) && sent[1].body.page?.kind === "material",
    JSON.stringify(sent.map((x) => x.body.page))
  );
  await page.unroute("**/api/research/sessions/*/messages");

  // 확인할 수 없는 자원(없음·타인·보관): 접수 전 404, general로 낮추지 않음, 입력 유지
  await page.goto(U(`/research/material/?id=${crypto.randomUUID()}`));
  await chat.getByLabel("메시지").waitFor();
  await chat.getByLabel("메시지").fill("없는 자료 질문");
  await chat.getByRole("button", { name: "보내기" }).click();
  check("R22. unknown resource -> not found before admission", await appears(chat.getByText("메시지를 보내지 못했습니다.")));
  check("R22. input kept after 404", (await chat.getByLabel("메시지").inputValue()) === "없는 자료 질문");
  check("R22. 404 send not recorded as a message", (await chat.locator("li[data-role=user]", { hasText: "없는 자료 질문" }).count()) === 0);
  await chat.getByLabel("메시지").fill("");

  // 작은 화면: 전체 화면 dialog, 뒤 페이지 조작 불가, 44px 이상, 키보드(보이는 높이)에 맞춤
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(U("/posts/"));
  await chat.getByLabel("메시지").waitFor();
  check("R22. mobile panel is modal dialog", (await chat.getAttribute("role")) === "dialog" && (await chat.getAttribute("aria-modal")) === "true");
  const pb = await chat.boundingBox();
  check("R22. mobile panel fills viewport", !!pb && Math.round(pb.width) === 390 && Math.round(pb.height) === 844, JSON.stringify(pb));
  check("R22. page behind is inert on mobile", await page.evaluate(() => document.querySelector("body > main")?.hasAttribute("inert") === true));
  const small = [];
  for (const name of ["대화창 닫기", "보내기", "← 대화 목록"]) {
    const b = await chat.getByRole("button", { name, exact: true }).boundingBox();
    if (!b || b.height < 44) small.push(`${name}:${b && Math.round(b.height)}`);
  }
  check("R22. mobile panel buttons >= 44px tall", small.length === 0, small.join(", "));
  await page.setViewportSize({ width: 390, height: 500 });
  await page.waitForTimeout(300);
  const shrunk = await chat.boundingBox();
  check("R22. panel follows visible height (keyboard)", !!shrunk && Math.round(shrunk.height) === 500, JSON.stringify(shrunk));
  check("R22. composer reachable in shrunk panel", await chat.getByLabel("메시지").isVisible());
  await page.setViewportSize({ width: 390, height: 844 });
  await chat.getByRole("button", { name: "대화창 닫기" }).click();
  check("R22. inert removed after close", await page.evaluate(() => !document.querySelector("body > main")?.hasAttribute("inert")));
  await page.setViewportSize({ width: 1280, height: 800 });

  // 비로그인: 버튼은 보이고 대화는 로그인 안내
  const anon = await newPage();
  await anon.page.goto(U("/posts/"));
  const anonDock = anon.page.getByRole("button", { name: "Codex 대화창 열기" });
  check("R22. dock shown to logged-out visitors", await appears(anonDock));
  await anonDock.click();
  check(
    "R22. logged-out panel asks to log in, no composer",
    (await appears(anon.page.locator("#chat-panel").getByText("로그인이 필요합니다"))) && (await anon.page.locator("#chat-panel textarea").count()) === 0
  );
  await anon.context.close();
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

// R14. 같은 브라우저에서 다른 계정으로 바뀌면 이전 계정의 개인 화면·미저장 입력을 버린다(리뷰 blocker)
{
  // 대화창에 A의 대화와 작성 중 입력을 열어 둔 채로 바꾼다.
  await page.goto(sessionUrl);
  await chat.getByLabel("메시지").fill("A의 대화창 초안");
  await page.goto(docUrl);
  await main.getByRole("button", { name: "내용 수정" }).click();
  await main.getByLabel("본문").fill("A의 미저장 초안");
  // 같은 browser context에서 로그인을 다시 시작해 cookie를 새 계정(B)으로 바꾼다(로그아웃 없이).
  const tab2 = await context.newPage();
  await tab2.goto(U("/api/auth/github/start"));
  await tab2.waitForURL(U("/"));
  await tab2.close();
  // A의 CSRF → 403 → me 재조회 → B. #22부터는 초안 자동 저장이 먼저 403을 받아 편집기가 이미 닫혔을 수 있다.
  const save14 = main.getByRole("button", { name: "새 버전으로 저장" });
  if (await save14.isVisible()) await save14.click({ timeout: 5000 }).catch(() => {});
  check("R14. switched account sees not found", await appears(main.getByText("항목을 찾을 수 없습니다")));
  check("R14. previous account draft discarded", (await main.locator("textarea").count()) === 0 && !(await main.innerText()).includes("A의 미저장 초안"));
  check("R14. previous account title not shown", (await main.getByRole("heading", { name: "문서 D", exact: true }).count()) === 0);
  const panelText = (await chat.count()) ? await chat.innerText() : "";
  check(
    "R14. chat panel drops previous account conversation and draft",
    !panelText.includes("세션 1") && !panelText.includes("A의 대화창 초안") && (await page.locator('textarea[aria-label="메시지"]').count()) === 0,
    panelText.slice(0, 120)
  );
}

// R15. 일반 생성 요청: 응답 유실 → CSRF 403 → 정상 재시도, 같은 key·1건 생성(리뷰 major)
let docEUrl;
{
  const before15 = await counts();
  await page.goto(U("/research/document/new/"));
  await main.getByLabel("제목").fill("CSRF 재시도 문서");
  await main.getByLabel("본문").fill("원래 본문");
  let dropped15 = false;
  await page.route(COMMIT, async (route) => {
    if (!dropped15) {
      dropped15 = true;
      await route.fetch();
      return route.abort();
    }
    return route.continue();
  });
  await main.getByRole("button", { name: "문서 저장" }).click();
  await main.getByText("서버에 연결할 수 없습니다").waitFor();
  await setMock("csrf=reject");
  await main.getByRole("button", { name: "문서 저장" }).click();
  check("R15. csrf rejection shown", await appears(main.getByText("요청을 확인하지 못했습니다")));
  await setMock("csrf=normal");
  await main.getByRole("button", { name: "문서 저장" }).click();
  await page.waitForURL(/\/research\/document\/\?id=/);
  await page.unroute(COMMIT);
  docEUrl = page.url();
  const posts15 = (await mockCalls()).filter(isCommit).slice(-3);
  check("R15. same key across lost/403/retry", posts15.length === 3 && new Set(posts15.map((c) => c.idempotencyKey)).size === 1, JSON.stringify(posts15.map((c) => c.idempotencyKey)));
  check("R15. exactly one document created", (await counts()).documents === before15.documents + 1);
}

// R16. #22 편집 중 다른 곳에서 문서가 저장되면 초안 자동 저장이 멈춘다. 최신 문서 다시 불러오기가 일시 실패해도 입력 유지(리뷰 major),
//      다시 불러온 뒤에는 현재 버전과 비교해야 저장된다(비교 없이 덮어쓰지 않음).
{
  const id = new URL(docEUrl).searchParams.get("id");
  await main.getByRole("button", { name: "내용 수정" }).click();
  await main.getByLabel("본문").fill("B 초안");
  const cur = await api.get(`/research/documents/${id}`);
  await api.send("PATCH", `/research/documents/${id}`, { title: cur.title, content: "외부 수정", expectedVersion: cur.version });
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  check("R16. autosave stopped: document changed", await appears(main.getByText("자동 저장을 멈췄습니다. 문서가 그사이 바뀌었습니다", { exact: false })));
  await page.route(`**/api/research/documents/${id}`, (route) => (route.request().method() === "GET" ? route.abort() : route.continue()));
  await main.getByRole("button", { name: "최신 문서를 불러와 계속" }).click();
  check("R16. reload failure shown", await appears(main.getByText("최신 내용을 불러오지 못했습니다")));
  check("R16. draft kept after reload failure", (await main.getByLabel("본문").inputValue()) === "B 초안");
  await page.unroute(`**/api/research/documents/${id}`);
  await main.getByRole("button", { name: "최신 문서를 불러와 계속" }).click();
  await main.getByText("초안 자동 저장됨").waitFor();
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  check(
    "R16. compare required before overwriting the newer version",
    (await appears(main.getByText("에서 내 초안으로 바뀌는 내용입니다", { exact: false }))) && (await appears(main.locator('[data-diff="del"]', { hasText: "외부 수정" })))
  );
  await main.getByRole("button", { name: "비교했고 이 내용으로 저장" }).click();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  check("R16. save after compare succeeds with draft", await appears(main.locator(".prose").getByText("B 초안")));
}

// R17. 생성(commit) 응답 유실 → 401 → 재로그인. #22: 입력은 계정별 서버 초안이라 브라우저에 본문을 남기지 않고,
//      다른 계정은 그 초안 주소로도 내용을 볼 수 없으며, 같은 계정은 이미 반영된 저장을 문서로 확인한다(중복 없음).
{
  const accountX = (await api.get("/auth/me")).user.accountId;
  const before17 = await counts();
  await page.goto(U("/research/document/new/"));
  await main.getByLabel("제목").fill("재인증 문서");
  await main.getByLabel("본문").fill("재인증 본문");
  let dropped17 = false;
  await page.route(COMMIT, async (route) => {
    if (!dropped17) {
      dropped17 = true;
      await route.fetch();
      return route.abort();
    }
    return route.continue();
  });
  await main.getByRole("button", { name: "문서 저장" }).click();
  await main.getByText("서버에 연결할 수 없습니다").waitFor();
  await page.unroute(COMMIT);
  const kept17 = await page.waitForURL(/\?draft=/, { timeout: 8000 }).then(() => true, () => false);
  const draftUrl = page.url();
  check("R17. draft address kept", kept17, draftUrl);
  await setMock("expire=1");
  await main.getByRole("button", { name: "문서 저장" }).click();
  check("R17. 401 -> login required", await appears(main.getByText("로그인이 필요합니다")));
  check("R17. no document body kept in browser storage", !(await pendingStore()).includes("재인증"), await pendingStore());
  // 같은 탭의 다른 계정(Y): 새 문서는 빈 칸, X의 초안 주소로 와도 내용 없음
  await setMock("reuse=0&as=");
  await login(page);
  await page.goto(U("/research/document/new/"));
  await main.getByLabel("제목").waitFor();
  check("R17. other account sees empty form", (await main.getByLabel("제목").inputValue()) === "" && !(await main.innerText()).includes("재인증 문서"));
  await page.goto(draftUrl);
  await main.getByLabel("제목").waitFor();
  check(
    "R17. other account cannot open X's draft",
    (await appears(main.getByText("이어 쓸 초안을 찾지 못해", { exact: false }))) && (await main.getByLabel("제목").inputValue()) === "" && !(await main.innerText()).includes("재인증 본문")
  );
  // 원래 계정(X)으로 재로그인 → 초안 주소 → 이미 반영된 저장이면 그 문서로 간다(1건만)
  await setMock(`expire=1&as=${accountX}`);
  await login(page);
  await page.goto(draftUrl);
  await page.waitForURL(/\/research\/document\/\?id=/);
  check("R17. same account lands on the saved document", await appears(main.getByRole("heading", { name: "재인증 문서" })));
  check("R17. exactly one document created", (await counts()).documents === before17.documents + 1, `${before17.documents} -> ${(await counts()).documents}`);
  await setMock("as=");
}

// R18. 공개 문서 페이지의 개인 대화 생성 폼: 복구된 개인 제목은 계정에 묶여, 같은 탭 계정 전환 시 버린다(리뷰 F3-1)
{
  await setMock("reuse=0&as=");
  const pubDoc = await api.send("POST", "/research/documents", { title: "공개용 문서", content: "공개 본문" });
  const pv = await api.get(`/research/documents/${pubDoc.body.id}/publication-preview`);
  const pub = await api.send("POST", `/research/documents/${pubDoc.body.id}/publications`, {
    versionId: pv.versionId,
    expectedVersion: pv.expectedVersion,
    previewToken: pv.previewToken,
  });
  check("setup. fixture publication", pub.status === 201, String(pub.status));
  const pubUrl = U(`/public/document/?id=${pubDoc.body.id}`);
  await page.goto(pubUrl);
  await main.getByLabel("새 대화 제목").fill("A 개인 대화 제목");
  let dropped18 = false;
  await page.route("**/api/research/sessions", async (route) => {
    if (route.request().method() === "POST" && !dropped18) {
      dropped18 = true;
      await route.fetch();
      return route.abort();
    }
    return route.continue();
  });
  await main.getByRole("button", { name: "새 대화" }).click();
  await main.getByText("서버에 연결할 수 없습니다").waitFor();
  await page.unroute("**/api/research/sessions");
  await page.reload();
  await main.getByLabel("새 대화 제목").waitFor();
  check("R18. same account restores private title", (await main.getByLabel("새 대화 제목").inputValue()) === "A 개인 대화 제목");
  // 같은 browser context에서 다른 계정 cookie로 교체 → 저장 시 CSRF 403 → me 재조회
  const tab2 = await context.newPage();
  await tab2.goto(U("/api/auth/github/start"));
  await tab2.waitForURL(U("/"));
  await tab2.close();
  await main.getByRole("button", { name: "새 대화" }).click();
  await page.waitForFunction(
    () => !(document.querySelector('input[aria-label="새 대화 제목"]')?.value ?? "").includes("A 개인 대화 제목"),
    null,
    { timeout: 10000 }
  ).catch(() => {});
  check("R18. other account does not see previous private title", !(await main.innerText()).includes("A 개인 대화 제목") && (await main.getByLabel("새 대화 제목").inputValue()) !== "A 개인 대화 제목", await main.getByLabel("새 대화 제목").inputValue());
  check("R18. public document itself still shown", await appears(main.getByRole("heading", { name: "공개용 문서" })));
}

// R19. 로그아웃 전에 시작된 생성(commit) 요청이 로그아웃 뒤 늦게 실패해도 보관소에 다시 쓰지 않는다(리뷰 F3-2)
{
  await page.goto(U("/research/document/new/"));
  await main.getByLabel("제목").fill("로그아웃 경합 문서");
  await main.getByLabel("본문").fill("로그아웃 경합 본문");
  let release19;
  let accepted19;
  const gate19 = new Promise((r) => (release19 = r));
  const seen19 = new Promise((r) => (accepted19 = r));
  await page.route(COMMIT, async (route) => {
    await route.fetch(); // 서버 반영
    accepted19();
    await gate19;
    return route.abort(); // 브라우저는 로그아웃 뒤에 실패를 받는다
  });
  void main.getByRole("button", { name: "문서 저장" }).click();
  await seen19;
  await page.locator("header").getByRole("button", { name: "로그아웃", exact: true }).first().click();
  await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().waitFor();
  check("R19. store empty right after logout", !(await pendingStore()).includes("로그아웃 경합"));
  release19();
  await page.waitForTimeout(1500);
  check("R19. late failure after logout does not rewrite store", !(await pendingStore()).includes("로그아웃 경합"), await pendingStore());
  await page.unroute(COMMIT);
}

// R20. 확정 정책: 승인됐지만 편집자·관리자가 아닌 계정 — 메뉴 없음, 서버 403 안내, 공개 글 질문 폼 대신 안내
{
  await setMock("nextLogin=approved-commenter&reuse=0&as=");
  const c = await newPage();
  await login(c.page);
  const cm = c.page.locator("main");
  check("R20. commenter has no 내 연구 nav", (await c.page.locator("header").getByRole("link", { name: "내 연구" }).count()) === 0);
  await c.page.goto(U("/research/"));
  check("R20. commenter gets forbidden guidance", await appears(cm.getByText("관리자가 승인한 편집자·관리자만 이용할 수 있습니다")));
  const pubList = await (await c.page.request.get(U("/api/public/documents"))).json();
  if (pubList.items?.length) {
    await c.page.goto(U(`/public/document/?id=${pubList.items[0].documentId}`));
    check("R20. commenter can read public document", await appears(cm.getByRole("heading").first()));
    check("R20. commenter sees question restriction, no form", (await appears(cm.getByText("Codex 질문은 관리자가 승인한 편집자·관리자만"))) && (await cm.getByLabel("새 대화 제목").count()) === 0);
  } else {
    check("R20. public fixture available", false, "no public document");
  }
  await c.page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  check(
    "R20. commenter chat panel shows restriction, no composer",
    (await appears(c.page.locator("#chat-panel").getByText("Codex 질문은 관리자가 승인한 편집자·관리자만"))) && (await c.page.locator("#chat-panel textarea").count()) === 0
  );
  await c.context.close();
  await setMock("nextLogin=approved");
}

// R21. 관리자 화면: admin이 승인된 일반 회원을 편집자로 지정(사유 필수) → 대상 session 폐기 → 재로그인 시 편집자 메뉴,
//      본인·관리자 계정 변경 불가 표시, 다른 곳 변경 뒤 적용은 409 안내, 비관리자는 안내만
{
  await setMock("nextLogin=approved-commenter&reuse=0&as=");
  const target = await newPage();
  await login(target.page);
  const tMe = await (await target.page.request.get(U("/api/auth/me"))).json();
  await setMock("nextLogin=admin&reuse=0&as=");
  const adm = await newPage();
  await login(adm.page);
  check("R21. admin sees 관리 nav", await appears(adm.page.locator("header").getByRole("link", { name: "관리" })));
  await adm.page.goto(U("/admin/"));
  const am = adm.page.locator("main");
  const rowOf = (login) => am.getByRole("listitem").filter({ hasText: new RegExp(`${login}\\b`) });
  const row = rowOf(tMe.user.login);
  await row.first().waitFor();
  check("R21. own row cannot be changed", await appears(am.getByRole("listitem").filter({ hasText: "(나)" }).getByText("본인 계정은 바꿀 수 없습니다")));
  await row.getByRole("button", { name: /^편집자로 지정/ }).click();
  await row.getByLabel("변경 사유").fill("M3 편집자 지정 확인");
  await row.getByRole("button", { name: "적용" }).click();
  check("R21. grant applied with re-login notice", await appears(row.getByText("대상 사용자는 다시 로그인해야 합니다")));
  check("R21. row shows editor", (await row.innerText()).includes("편집자") && (await row.innerText()).includes("승인됨"));
  check("R21. target session revoked", (await target.page.request.get(U("/api/auth/me"))).status() === 401);
  await setMock(`as=${tMe.user.accountId}`);
  await login(target.page);
  check("R21. target is editor after re-login", await appears(target.page.locator("header").getByRole("link", { name: "내 연구" })));
  // 다른 곳에서 먼저 변경 → 화면의 이전 version으로 적용 시 409
  const admApi = apiClient(adm.page);
  const listNow = await admApi.get("/admin/accounts?limit=100");
  const cur = listNow.items.find((a) => a.accountId === tMe.user.accountId);
  const ext = await admApi.send("PUT", `/admin/accounts/${tMe.user.accountId}/membership`, {
    role: "editor",
    isApproved: true,
    reason: "다른 곳에서 먼저 변경",
    expectedVersion: cur.version,
  });
  check("setup. external membership change 200", ext.status === 200, String(ext.status));
  await row.getByRole("button", { name: /^편집자 해제/ }).click();
  await row.getByLabel("변경 사유").fill("오래된 화면에서 적용");
  await row.getByRole("button", { name: "적용" }).click();
  check("R21. stale version -> conflict guidance", await appears(row.getByText("다른 곳에서 먼저 변경되었습니다")));
  // 비관리자(편집자)는 관리 화면에서 안내만. 위 외부 변경도 대상 session을 폐기했으므로 다시 로그인한다.
  await login(target.page);
  await target.page.goto(U("/admin/"));
  check("R21. non-admin sees admin-only notice", await appears(target.page.locator("main").getByText("승인된 관리자만 이용할 수 있습니다")));
  await target.context.close();
  await adm.context.close();
  await setMock("as=&nextLogin=approved");
}

// R23. 리뷰(2789cab) 반례 회귀: 늦게 끝난 전송이 새 초안을 지우지 않음 / 계정 전환 뒤 이전 계정의 늦은 생성 완료가
//      새 계정 대화창 상태를 바꾸지 않음 / 모바일 모달의 Tab 순환과 포커스가 밖에 있어도 Escape 닫기
{
  // 앞 단계의 계정 상태와 무관하게, 승인된 편집자 계정의 새 browser context에서 확인한다.
  await setMock("nextLogin=approved&reuse=0&as=");
  const r = await newPage();
  await login(r.page);
  const rp = r.page;
  const rm = rp.locator("main");
  const rc = rp.locator("#chat-panel");
  const rapi = apiClient(rp);
  await setMock("codex=fixture&window=&remaining=");
  // R23a. 전송 성공 응답 보류 → 목록으로 나갔다 같은 대화 재진입 → 실행 종료 뒤 새 초안 → 이전 응답 전달 → 재진입해도 새 초안 유지
  const created = await rapi.send("POST", "/research/sessions", { title: "지연 응답 대화" });
  await rp.goto(U(`/research/session/?id=${created.body.id}`));
  await rc.getByLabel("메시지").waitFor();
  let releaseSend;
  const sendGate = new Promise((r) => (releaseSend = r));
  await rp.route("**/api/research/sessions/*/messages", async (route) => {
    const res = await route.fetch();
    await sendGate;
    return route.fulfill({ response: res }).catch(() => {});
  });
  await rc.getByLabel("메시지").fill("지연 성공 질문");
  await rc.getByRole("button", { name: "보내기" }).click();
  await rc.getByRole("button", { name: "← 대화 목록" }).click();
  await rc.getByRole("link", { name: /지연 응답 대화/ }).click();
  await rc.getByText("모의 응답: 지연 성공 질문").waitFor({ timeout: 15000 });
  await rc.getByLabel("메시지").and(rc.locator(":enabled")).waitFor({ timeout: 15000 });
  await rc.getByLabel("메시지").fill("새로 쓴 초안");
  releaseSend();
  await rp.waitForTimeout(800);
  await rp.unroute("**/api/research/sessions/*/messages");
  await rc.getByRole("button", { name: "← 대화 목록" }).click();
  await rc.getByRole("link", { name: /지연 응답 대화/ }).click();
  await rc.getByLabel("메시지").waitFor();
  check("R23a. late success of a left composer keeps the newer draft", (await rc.getByLabel("메시지").inputValue()) === "새로 쓴 초안", await rc.getByLabel("메시지").inputValue());
  await rc.getByLabel("메시지").fill("");

  // R23d. (리뷰 798095e) 결과 미확인 전송 A를 둔 채 새 초안 B: 새로고침·목록 재진입 뒤에도 B를 복원하고 A의 key·body는 보관,
  //       자동 재전송 없음, 비운 초안은 빈 입력으로 복원, "이전 질문 불러오기"는 A를 같은 key·body로, 새 초안은 새 key로 보낸다.
  {
    const s4 = await rapi.send("POST", "/research/sessions", { title: "미확인 초안 대화" });
    await rp.goto(U(`/research/session/?id=${s4.body.id}`));
    await rc.getByLabel("메시지").waitFor();
    const posts = [];
    let dropped = false;
    await rp.route("**/api/research/sessions/*/messages", async (route) => {
      posts.push({ key: route.request().headers()["idempotency-key"], body: route.request().postData() });
      if (!dropped) {
        dropped = true;
        await route.fetch();
        return route.abort().catch(() => {});
      }
      return route.continue().catch(() => {});
    });
    const pendingA = () =>
      rp.evaluate(() =>
        Object.entries(sessionStorage)
          .filter(([k]) => k.includes(":session-send:"))
          .map(([, v]) => v)
          .join("")
      );
    const input = () => rc.getByLabel("메시지").inputValue();
    await rc.getByLabel("메시지").fill("미확인 질문 A");
    await rc.getByRole("button", { name: "보내기" }).click();
    await rc.getByText("이전 전송의 결과를 확인하지 못했습니다").waitFor();
    await rc.getByText("모의 응답: 미확인 질문 A").waitFor({ timeout: 15000 });
    await rc.getByLabel("메시지").and(rc.locator(":enabled")).waitFor({ timeout: 15000 });
    await rc.getByLabel("메시지").fill("새 초안 B");
    await rp.reload();
    await rc.getByLabel("메시지").waitFor();
    check("R23d. reload restores newer draft, not the uncertain request", (await input()) === "새 초안 B", await input());
    check("R23d. uncertain request kept after reload", (await pendingA()).includes("미확인 질문 A"));
    check("R23d. notice offers loading the previous question", await appears(rc.getByRole("button", { name: "이전 질문 불러오기" })));
    await rc.getByRole("button", { name: "← 대화 목록" }).click();
    await rc.getByRole("link", { name: /미확인 초안 대화/ }).click();
    await rc.getByLabel("메시지").waitFor();
    check("R23d. list re-entry restores newer draft", (await input()) === "새 초안 B", await input());
    await rc.getByLabel("메시지").fill("");
    await rp.reload();
    await rc.getByLabel("메시지").waitFor();
    check("R23d. explicitly emptied draft restored as empty", (await input()) === "", await input());
    check("R23d. no automatic send", posts.length === 1, String(posts.length));
    await rc.getByRole("button", { name: "이전 질문 불러오기" }).click();
    check("R23d. previous question loaded on request", (await input()) === "미확인 질문 A");
    await rc.getByRole("button", { name: "다시 보내기" }).click();
    await rc.getByRole("button", { name: "보내기", exact: true }).waitFor();
    check(
      "R23d. previous question resent with original key and body",
      posts.length === 2 && posts[0].key === posts[1].key && posts[0].body === posts[1].body,
      JSON.stringify(posts.map((x) => x.key.slice(0, 8)))
    );
    check("R23d. still one user message A", (await rc.locator("li[data-role=user]", { hasText: "미확인 질문 A" }).count()) === 1);
    check("R23d. uncertain request cleared after confirmation", !(await pendingA()).includes("미확인 질문 A"));
    await rc.getByLabel("메시지").fill("새 초안 B");
    await rc.getByRole("button", { name: "보내기" }).click();
    await rc.getByText("모의 응답: 새 초안 B").waitFor({ timeout: 15000 });
    check("R23d. new draft sent as a new request (new key)", posts.length === 3 && posts[2].key !== posts[0].key);
    await rp.unroute("**/api/research/sessions/*/messages");
    await rc.getByLabel("메시지").and(rc.locator(":enabled")).waitFor({ timeout: 15000 });
  }

  // R23b. 계정 X의 대화 생성 201 보류 → 같은 탭이 계정 Y로 바뀜 → X 응답 전달: Y의 대화창 선택·화면은 그대로
  const before = (await rapi.get("/auth/me")).user.accountId;
  await rp.goto(U("/research/"));
  // 대화창은 마지막 대화를 기억하므로 목록으로 돌린 뒤 닫는다(다시 열면 새 대화 폼이 보이게).
  await rc.getByRole("button", { name: "← 대화 목록" }).click();
  await rc.getByRole("button", { name: "대화창 닫기" }).click();
  let releaseCreate;
  const createGate = new Promise((r) => (releaseCreate = r));
  let held = false;
  let createdStatus = 0;
  await rp.route("**/api/research/sessions", async (route) => {
    if (route.request().method() === "POST" && !held) {
      held = true;
      const res = await route.fetch();
      createdStatus = res.status();
      await createGate;
      return route.fulfill({ response: res }).catch(() => {});
    }
    return route.continue();
  });
  // #44: 새 대화 폼은 대화창에만 있다. 생성 응답을 붙잡아 둔 채 대화창을 닫는다(아래 흐름은 닫힌 대화창에서 시작한다)
  await rp.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await rc.getByLabel("새 대화 제목").fill("지연 생성 대화");
  await rc.getByRole("button", { name: "새 대화" }).click();
  await rc.getByRole("button", { name: "대화창 닫기" }).click();
  const tab = await r.context.newPage();
  await tab.goto(U("/api/auth/github/start"));
  await tab.waitForURL(U("/"));
  await tab.close();
  // 이 탭에서 이전 계정 CSRF로 보낸 요청이 403 → me 재조회로 새 계정이 된다(새로고침 없이).
  await rp.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await rc.getByLabel("새 대화 제목").fill("전환 유도");
  await rc.getByRole("button", { name: "새 대화" }).click();
  // 새 계정의 대화창 상태는 닫힘에서 시작하므로, 전환이 화면에 반영되면 대화창이 내려간다.
  await rc.waitFor({ state: "hidden", timeout: 10000 });
  const after = (await rapi.get("/auth/me")).user.accountId;
  check("R23b. setup: same tab switched to another account", !!after && after !== before);
  await rp.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await rc.getByLabel("새 대화 제목").waitFor();
  releaseCreate();
  await rp.waitForTimeout(1000);
  await rp.unroute("**/api/research/sessions");
  const ui = await rp.evaluate((acc) => JSON.parse(sessionStorage.getItem(`research:pending:${acc}:chat-ui`) ?? "null"), after);
  check("R23b. setup: previous account create completed (201)", createdStatus === 201, String(createdStatus));
  check(
    "R23b. late create of previous account does not select it in new account's panel",
    (ui === null || ui.sessionId === null) && (await rc.getByText("항목을 찾을 수 없습니다").count()) === 0 && (await rc.getByRole("heading", { name: "지연 생성 대화" }).count()) === 0,
    JSON.stringify(ui)
  );

  // R23c. 모바일 모달: Tab/Shift+Tab은 대화창 안에서만, 포커스가 밖으로 나가도 Escape로 닫고 버튼으로 복귀
  await rp.setViewportSize({ width: 390, height: 844 });
  await rp.goto(U("/posts/"));
  // 저장된 열림 상태는 로그인 확인 뒤에 반영되므로 화면이 자리 잡은 뒤에 연다.
  await rp.waitForLoadState("networkidle");
  await rp.waitForTimeout(500);
  if (!(await rc.isVisible())) await rp.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await rc.getByRole("heading", { name: "Codex 대화" }).waitFor();
  const inside = () => rp.evaluate(() => !!document.activeElement?.closest("#chat-panel"));
  let escaped = 0;
  for (let i = 0; i < 25; i++) {
    await rp.keyboard.press("Tab");
    if (!(await inside())) escaped++;
  }
  for (let i = 0; i < 25; i++) {
    await rp.keyboard.press("Shift+Tab");
    if (!(await inside())) escaped++;
  }
  check("R23c. mobile Tab/Shift+Tab cycle stays inside panel", escaped === 0, `escaped ${escaped}`);
  await rp.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
  await rp.keyboard.press("Tab");
  check("R23c. Tab from outside returns focus into panel", await inside());
  await rp.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
  await rp.keyboard.press("Escape");
  check("R23c. Escape closes with focus outside panel", await appears(rp.getByRole("button", { name: "Codex 대화창 열기" })));
  check("R23c. focus returns to dock button", await rp.evaluate(() => document.activeElement?.getAttribute("aria-label") === "Codex 대화창 열기"));
  await r.context.close();
}

// W1. #22 문서 편집기: 분할 미리보기(저장 화면과 같은 SafeMarkdown 규칙)와 버전 비교(GitHub 스타일 줄 단위)
{
  // 앞 블록이 계정을 바꾸고 로그아웃하므로 새 창에서 로그인한다.
  const wctx = await newPage();
  await login(wctx.page);
  const page = wctx.page;
  const main = page.locator("main");
  const api = apiClient(page);
  const same = Array.from({ length: 12 }, (_, k) => `같은 줄 ${k + 1}`).join("\n");
  const w = await api.send("POST", "/research/documents", { title: "편집기 문서", content: `첫 줄\n바꿀 문장 하나\n${same}\n끝 줄\n` });
  check("W1. setup: document created", w.status === 201, String(w.status));
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(U(`/research/document/?id=${w.body.id}`));
  await main.getByRole("button", { name: "내용 수정" }).click();
  const body = main.getByLabel("본문");
  const preview = main.getByRole("region", { name: "미리보기" });
  check("W1. split view is default", (await main.getByRole("button", { name: "나란히 보기" }).getAttribute("aria-pressed")) === "true");
  check("W1. preview shows current body", await appears(preview.getByText("바꿀 문장 하나")));
  const original = await body.inputValue();
  await body.fill(`# 미리보기 제목\n\n<script>window.__w1 = 1</script>\n\n![그림](https://example.com/w1.png)\n\n**굵게**`);
  check("W1. preview follows input (heading rendered)", await appears(preview.getByRole("heading", { name: "미리보기 제목" })));
  check(
    "W1. preview keeps safe rules: no raw HTML, no external image",
    (await preview.locator("script, img").count()) === 0 && (await appears(preview.getByText("[이미지: 그림]"))) && (await page.evaluate(() => window.__w1)) === undefined
  );
  const tb = await body.boundingBox();
  const pb = await preview.boundingBox();
  check("W1. wide screen: side by side", !!tb && !!pb && pb.x > tb.x + tb.width - 1 && Math.abs(pb.y - tb.y) < 4, JSON.stringify({ tb, pb }));
  await page.setViewportSize({ width: 375, height: 800 });
  const tb2 = await body.boundingBox();
  const pb2 = await preview.boundingBox();
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("W1. narrow screen: stacked, no horizontal scroll", !!tb2 && !!pb2 && pb2.y >= tb2.y + tb2.height - 1 && over <= 0, JSON.stringify({ tb2, pb2, over }));
  await page.setViewportSize({ width: 1280, height: 800 });
  await main.getByRole("button", { name: "미리보기만" }).click();
  check("W1. preview only: editor hidden, input kept", !(await body.isVisible()) && (await preview.isVisible()) && (await body.inputValue()).startsWith("# 미리보기 제목"));
  await main.getByRole("button", { name: "작성만" }).click();
  check("W1. write only: preview hidden", (await preview.count()) === 0 && (await body.isVisible()));

  // 두 번째 버전 저장 → 버전 비교
  await body.fill(original.replace("바꿀 문장 하나", "바뀐 문장 하나").replace("끝 줄", "끝 줄\n새로 쓴 줄"));
  await main.getByLabel("제목", { exact: true }).fill("편집기 문서 2");
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  await main.getByRole("button", { name: "버전 비교", exact: true }).click();
  const cmp = main.getByRole("region", { name: "버전 비교" });
  check("W1. compare: summary +2 −1", await appears(cmp.getByText("+2줄")) && (await appears(cmp.getByText("−1줄"))));
  check("W1. compare: removed and added lines", (await appears(cmp.locator('[data-diff="del"]', { hasText: "바꿀 문장 하나" }))) && (await appears(cmp.locator('[data-diff="add"]', { hasText: "새로 쓴 줄" }))));
  check("W1. compare: changed word highlighted", (await cmp.locator("ins", { hasText: "바뀐" }).count()) === 1 && (await cmp.locator("del", { hasText: "바꿀" }).count()) === 1);
  check("W1. compare: title change shown", await appears(cmp.locator("ins", { hasText: "편집기 문서 2" })));
  const fold = cmp.getByRole("button", { name: /같은 줄 \d+개 펼치기/ });
  check("W1. compare: unchanged lines folded", (await fold.count()) === 1 && (await cmp.getByText("같은 줄 6", { exact: true }).count()) === 0);
  await fold.click();
  check("W1. compare: fold expands", await appears(cmp.getByText("같은 줄 6", { exact: true })));
  await cmp.getByRole("button", { name: "방향 바꾸기" }).click();
  check("W1. compare: reversed direction", await appears(cmp.getByText("+1줄")) && (await appears(cmp.getByText("−2줄"))));
  await cmp.getByRole("button", { name: "비교 닫기" }).click();
  await main.getByRole("button", { name: /v2\s/ }).click();
  await main.getByRole("button", { name: "이전 버전과 비교" }).click();
  check("W1. per-version compare opens panel with focus", await appears(cmp.getByText("+2줄")) && (await page.evaluate(() => document.activeElement?.getAttribute("aria-label"))) === "버전 비교");
  await page.setViewportSize({ width: 375, height: 800 });
  const over2 = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("W1. compare fits 375px", over2 <= 0, `overflow ${over2}px`);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.context().close();
}

// W2. #22 서버 초안 자동 저장·복구·탭 간 충돌·버리기, Codex 제안 검토(diff)·수락·버리기, 이전 버전으로 되돌리기
{
  const wctx = await newPage();
  await login(wctx.page);
  const page = wctx.page;
  const main = page.locator("main");
  const chat = page.locator("#chat-panel");
  const api = apiClient(page);
  await setMock("codex=fixture&window=&remaining=");
  const doc = await api.send("POST", "/research/documents", { title: "자동 저장 문서", content: "원래 본문\n" });
  const id = doc.body.id;
  const draftOf = async () => (await api.get(`/research/documents/${id}/draft`)).draft;
  await page.goto(U(`/research/document/?id=${id}`));
  await main.getByRole("button", { name: "내용 수정" }).click();
  await main.getByLabel("본문").fill("자동 저장된 초안");
  check("W2. autosave: saved status shown", await appears(main.getByText("초안 자동 저장됨"), 8000));
  const d1 = await draftOf();
  const head = await api.get(`/research/documents/${id}`);
  check("W2. autosave creates a draft, not a version", d1?.content === "자동 저장된 초안" && head.version === 1 && head.content === "원래 본문\n", JSON.stringify({ d1, v: head.version }));
  await page.reload();
  check("W2. reload: draft notice, saved version still shown", (await appears(main.getByText("저장하지 않은 초안이 있습니다", { exact: false }))) && (await appears(main.locator(".prose").getByText("원래 본문"))));
  await main.getByRole("button", { name: "초안 이어서 편집" }).click();
  check("W2. resume restores draft", (await main.getByLabel("본문").inputValue()) === "자동 저장된 초안");

  // 다른 탭이 같은 초안을 먼저 바꿈 → 조용히 덮어쓰지 않고 고르게 한다
  const other = await draftOf();
  await api.send("PUT", `/research/document-drafts/${other.id}`, { title: other.title, content: "다른 탭 초안", expectedDraftRevision: other.revision });
  await main.getByLabel("본문").fill("내 입력");
  check("W2. conflict: other tab change detected", await appears(main.getByText("다른 탭이나 기기에서 이 문서의 초안이 바뀌었습니다", { exact: false }), 8000));
  check("W2. conflict: server draft not overwritten yet", (await draftOf()).content === "다른 탭 초안");
  await main.getByRole("button", { name: "차이 보기" }).click();
  check("W2. conflict: diff between server draft and my input", await appears(main.locator('[data-diff="del"]', { hasText: "다른 탭 초안" })));
  await main.getByRole("button", { name: /지금 입력으로 계속/ }).click();
  await main.getByText("초안 자동 저장됨").waitFor();
  check("W2. conflict: keep mine saves over server draft", (await draftOf()).content === "내 입력");
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  const v2 = await api.get(`/research/documents/${id}`);
  check("W2. commit: one new version, draft closed", v2.content === "내 입력" && (await draftOf()) === null && (await appears(main.locator(".prose").getByText("내 입력"))), String(v2.version));

  // 초안 버리기: 저장된 문서로 돌아가고 버전은 늘지 않는다
  await main.getByRole("button", { name: "내용 수정" }).click();
  await main.getByLabel("본문").fill("버릴 초안");
  await main.getByText("초안 자동 저장됨").waitFor();
  await main.getByRole("button", { name: "초안 버리기…" }).click();
  await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  check(
    "W2. discard: back to saved document, no version, no draft",
    (await appears(main.locator(".prose").getByText("내 입력"))) && (await draftOf()) === null && (await api.get(`/research/documents/${id}`)).version === v2.version
  );

  // Codex 제안: 대화에서 만들어도 문서는 바뀌지 않는다 → 대화의 '제안 검토' 링크 → diff → 수락
  const sess = await api.send("POST", "/research/sessions", { title: "편집 대화" });
  check("W2. setup: session", sess.status === 201, String(sess.status));
  await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await chat.getByRole("link", { name: /편집 대화/ }).click();
  await chat.getByLabel("메시지").fill("/fixture/propose 문장 하나 더해줘");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("모의 응답: /fixture/propose 문장 하나 더해줘").waitFor({ timeout: 15000 });
  check("W2. proposal: chat explains document not changed", await appears(chat.getByText("수락하기 전에는 문서가 바뀌지 않습니다", { exact: false })));
  check("W2. proposal: document unchanged", (await api.get(`/research/documents/${id}`)).version === v2.version);
  await chat.getByRole("link", { name: "제안 검토" }).click();
  const proposalsBox = main.locator("#proposals");
  check("W2. proposal: diff vs current document", await appears(proposalsBox.locator('[data-diff="add"]', { hasText: "Codex가 덧붙인 문장" })));
  check("W2. proposal: link opens that proposal", (await proposalsBox.getByRole("button", { name: /Codex 제안/ }).first().getAttribute("aria-expanded")) === "true");
  await proposalsBox.getByRole("button", { name: "제안 수락" }).click();
  await proposalsBox.getByRole("button", { name: "수락 확인" }).click();
  check("W2. proposal: accepted becomes new version", await appears(main.locator(".prose").getByText("Codex가 덧붙인 문장")));
  const v3 = await api.get(`/research/documents/${id}`);
  check("W2. proposal: exactly one version added", v3.latestVersion.number === v2.latestVersion.number + 1, `${v2.latestVersion.number} -> ${v3.latestVersion.number}`);
  check("W2. proposal: moved to history", await appears(proposalsBox.getByRole("button", { name: /지난 제안 1건/ })));

  // 두 번째 제안은 버린다: 문서·버전 변화 없음
  await chat.getByLabel("메시지").fill("/fixture/propose 또 고쳐줘");
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText("모의 응답: /fixture/propose 또 고쳐줘").waitFor({ timeout: 15000 });
  await chat.getByRole("link", { name: "제안 검토" }).last().click();
  await proposalsBox.getByRole("button", { name: "제안 버리기" }).click();
  check("W2. proposal discard: no pending left", await appears(proposalsBox.getByText("검토할 제안이 없습니다")));
  check("W2. proposal discard: document unchanged", (await api.get(`/research/documents/${id}`)).version === v3.version);

  // 이전 버전으로 되돌리기 = 그 내용을 새 버전으로 저장(원래 버전은 그대로)
  await main.getByRole("button", { name: /^v1\s/ }).click();
  await main.getByRole("button", { name: "이 버전으로 되돌리기(편집기에 불러오기)" }).click();
  const restored = await page
    .waitForFunction(() => document.querySelector("main textarea")?.value === "원래 본문\n", null, { timeout: 8000 })
    .then(() => true)
    .catch(() => false);
  check("W2. restore: editor loaded with v1", restored, JSON.stringify(await main.getByLabel("본문").inputValue()));
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  const v4 = await api.get(`/research/documents/${id}`);
  check("W2. restore: saved as a new version", v4.content === "원래 본문\n" && v4.latestVersion.number === v3.latestVersion.number + 1);
  await page.context().close();
}

// W4. #22 리뷰 F2: 비교 계산 전체(줄 비교 + 단어 강조)에 시간 예산이 걸리고, 아주 긴 본문도 화면을 오래 막지 않는다.
{
  const wctx = await newPage();
  await login(wctx.page);
  const page = wctx.page;
  const main = page.locator("main");
  const api = apiClient(page);
  const timeCompare = async (before, after, title) => {
    const d = await api.send("POST", "/research/documents", { title, content: before });
    const p = await api.send("PATCH", `/research/documents/${d.body.id}`, { title, content: after, expectedVersion: d.body.version });
    await page.goto(U(`/research/document/?id=${d.body.id}`));
    await main.getByRole("button", { name: "버전 비교", exact: true }).waitFor();
    const t0 = Date.now();
    await main.getByRole("button", { name: "버전 비교", exact: true }).click();
    const cmp = main.getByRole("region", { name: "버전 비교" });
    await cmp.locator('[data-diff], p[role="status"]').first().waitFor({ timeout: 30000 });
    const elapsed = Date.now() - t0;
    // 계산이 끝난 뒤 화면이 바로 응답하는지(긴 작업이 남아 있지 않은지)
    const t1 = Date.now();
    await page.evaluate(() => new Promise((r) => setTimeout(r, 0)));
    return { status: p.status, elapsed, idle: Date.now() - t1, cmp };
  };
  // 리뷰 재현 입력: 1,998자 줄 10개가 전부 다른 단어(이전에는 단어 비교만으로 30초 이상)
  const a10 = Array(10).fill("a ".repeat(999)).join("\n");
  const z10 = Array(10).fill("z ".repeat(999)).join("\n");
  const r1 = await timeCompare(a10, z10, "긴 줄 비교");
  check("W4. long changed lines: compare finishes quickly", r1.status === 200 && r1.elapsed < 5000 && r1.idle < 500, JSON.stringify({ elapsed: r1.elapsed, idle: r1.idle }));
  check("W4. long changed lines: line result kept", await appears(r1.cmp.getByText("+10줄")));
  // 200,000자 가까운 본문이 전부 바뀐 경우: 비교하거나 비교 불가로 안내하고, 그리는 줄 수는 상한까지만
  const line = (c, k) => `${c}${k} `.repeat(16).slice(0, 99);
  const big1 = Array.from({ length: 1990 }, (_, k) => line("가", k)).join("\n");
  const big2 = Array.from({ length: 1990 }, (_, k) => line("나", k)).join("\n");
  const r2 = await timeCompare(big1, big2, "아주 긴 비교");
  const rendered = await r2.cmp.locator("[data-diff]").count();
  check("W4. 200k body: bounded time and rows", r2.status === 200 && r2.elapsed < 8000 && rendered <= 1000, JSON.stringify({ elapsed: r2.elapsed, idle: r2.idle, rendered, len: big1.length }));
  // 비교는 되지만 바뀐 줄이 많으면 처음 1000줄만 그리고 나머지는 '더 보기'로 그린다.
  const base = Array.from({ length: 50 }, (_, k) => `기존 줄 ${k}`).join("\n");
  const grown = `${base}\n${Array.from({ length: 1500 }, (_, k) => `추가 줄 ${k}`).join("\n")}`;
  const r3 = await timeCompare(base, grown, "많은 추가 줄");
  const first = await r3.cmp.locator("[data-diff]").count();
  const more = r3.cmp.getByRole("button", { name: /다음 1000줄 더 보기/ });
  check("W4. many rows: first 1000 rendered, rest on demand", r3.elapsed < 8000 && first === 1000 && (await appears(more)), JSON.stringify({ elapsed: r3.elapsed, first }));
  await more.click();
  check("W4. many rows: show more renders the rest", (await r3.cmp.locator("[data-diff]").count()) > 1000 && (await appears(r3.cmp.getByText("+1500줄"))));
  // 리뷰 F3: 아주 많은 줄 삭제·교체(200k자 이하)에서도 계산이 실패하지 않고, 실패하더라도 비교 영역 밖 화면은 그대로다.
  const intact = async (r) =>
    (await page.getByText("문제가 발생했습니다").count()) === 0 &&
    (await main.getByRole("heading", { name: "버전 기록" }).isVisible()) &&
    (await r.cmp.locator('[data-diff], p[role="status"]').count()) > 0;
  const r4 = await timeCompare(`# Title\n${"\n".repeat(150000)}Text`, "# Title\nText", "빈 줄 대량 삭제");
  check("W4. 150k blank lines deleted: no error screen, bounded", r4.status === 200 && (await intact(r4)) && r4.elapsed < 8000, JSON.stringify({ elapsed: r4.elapsed }));
  const shortA = Array.from({ length: 20000 }, (_, k) => `a${k}`).join("\n");
  const shortB = Array.from({ length: 20000 }, (_, k) => `b${k}`).join("\n");
  const r5 = await timeCompare(shortA, shortB, "짧은 줄 대량 교체");
  check(
    "W4. 20k short lines replaced: no error screen, bounded rows",
    r5.status === 200 && (await intact(r5)) && r5.elapsed < 8000 && (await r5.cmp.locator("[data-diff]").count()) <= 1000,
    JSON.stringify({ elapsed: r5.elapsed, len: shortA.length })
  );
  await page.context().close();
}

// W5. #22 리뷰 F1: 저장·닫기·버리기가 끝나기 전까지 입력을 잠가, 늦은 응답으로 편집기가 닫히며 새 입력을 잃지 않는다.
{
  const wctx = await newPage();
  await login(wctx.page);
  const page = wctx.page;
  const main = page.locator("main");
  const api = apiClient(page);
  const doc = await api.send("POST", "/research/documents", { title: "잠금 문서", content: "처음\n" });
  const id = doc.body.id;
  const draftOf = async () => (await api.get(`/research/documents/${id}/draft`)).draft;
  // 서버는 처리하고 응답만 붙잡는다.
  const hold = async (pattern, method) => {
    let release;
    let seen;
    const gate = new Promise((r) => (release = r));
    const arrived = new Promise((r) => (seen = r));
    await page.route(pattern, async (route) => {
      if (route.request().method() !== method) return route.continue();
      const response = await route.fetch();
      seen();
      await gate;
      return route.fulfill({ response });
    });
    return { release, arrived, done: () => page.unroute(pattern) };
  };
  const locked = async () => !(await main.getByLabel("본문").isEditable()) && !(await main.getByLabel("제목", { exact: true }).isEditable());
  await page.goto(U(`/research/document/?id=${id}`));

  // 저장(commit) 응답 보류 중
  await main.getByRole("button", { name: "내용 수정" }).click();
  await main.getByLabel("본문").fill("A 입력");
  const c = await hold("**/api/research/document-drafts/*/commit", "POST");
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  await c.arrived;
  check("W5. commit pending: inputs locked", (await locked()) && (await appears(main.getByText("저장하는 중에는 입력을 고칠 수 없습니다"))));
  const typed = await main.getByLabel("본문").fill("B 입력", { timeout: 1500 }).then(() => true).catch(() => false);
  check("W5. commit pending: typing refused", !typed && (await main.getByLabel("본문").inputValue()) === "A 입력");
  c.release();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  await c.done();
  check("W5. commit result: head A, no draft", (await api.get(`/research/documents/${id}`)).content === "A 입력" && (await draftOf()) === null);

  // 닫기(남은 입력 저장) 응답 보류 중
  await main.getByRole("button", { name: "내용 수정" }).click();
  const k = await hold("**/api/research/document-drafts", "POST");
  await main.getByLabel("본문").fill("닫기 전 입력");
  await main.getByRole("button", { name: "편집 닫기(초안 보관)" }).click();
  await k.arrived;
  check("W5. close pending: inputs locked", await locked());
  k.release();
  await main.getByRole("button", { name: "초안 이어서 편집" }).waitFor();
  await k.done();
  check("W5. close result: draft holds last input", (await draftOf())?.content === "닫기 전 입력");

  // 버리기 응답 보류 중
  await main.getByRole("button", { name: "초안 이어서 편집" }).click();
  const x = await hold("**/api/research/document-drafts/*", "DELETE");
  await main.getByRole("button", { name: "초안 버리기…" }).click();
  await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
  await x.arrived;
  check("W5. discard pending: inputs locked", await locked());
  x.release();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  await x.done();
  check("W5. discard result: no draft, head unchanged", (await draftOf()) === null && (await api.get(`/research/documents/${id}`)).content === "A 입력");
  await page.context().close();
}

// W3. #22 새 문서 서버 초안: 자동 저장 → 주소(?draft=)로 새로고침 복구 → 다른 새 문서 화면의 초안 목록 → 버리기 / 저장
{
  const wctx = await newPage();
  await login(wctx.page);
  const page = wctx.page;
  const main = page.locator("main");
  const before = await counts();
  await page.goto(U("/research/document/new/"));
  await main.getByLabel("제목").fill("새 초안 문서");
  await main.getByLabel("본문").fill("새 초안 본문");
  check("W3. autosave: saved status", await appears(main.getByText("초안 자동 저장됨"), 8000));
  check("W3. draft address in URL", await page.waitForURL(/\?draft=/, { timeout: 8000 }).then(() => true, () => false), page.url());
  check("W3. no document created by autosave", (await counts()).documents === before.documents);
  const draftUrl = page.url();
  await page.reload();
  await main.getByLabel("제목").waitFor();
  const restored = await page
    .waitForFunction(() => document.querySelector("main textarea")?.value === "새 초안 본문", null, { timeout: 8000 })
    .then(() => true)
    .catch(() => false);
  check("W3. reload restores draft", restored && (await main.getByLabel("제목").inputValue()) === "새 초안 문서");
  await page.goto(U("/research/document/new/"));
  check("W3. fresh new page lists unfinished draft", await appears(main.getByRole("link", { name: "새 초안 문서" })));
  await main.getByRole("link", { name: "새 초안 문서" }).click();
  await page.waitForURL(draftUrl);
  await main.getByRole("button", { name: "초안 버리기…" }).click();
  await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
  // 안내·빈 편집기는 주소 교체(router.replace)보다 먼저 보일 수 있어 주소는 따라올 때까지 기다린다(최대 5초).
  const discardedShown = await appears(main.getByText("초안을 버렸습니다"));
  const addressReset = await page.waitForURL((u) => !u.search.includes("draft="), { timeout: 5000 }).then(() => true).catch(() => false);
  check("W3. discard: blank editor, address reset", discardedShown && (await main.getByLabel("제목").inputValue()) === "" && addressReset, page.url());
  check("W3. discarded draft not listed", (await main.getByRole("link", { name: "새 초안 문서" }).count()) === 0);
  await main.getByLabel("제목").fill("저장할 새 문서");
  await main.getByLabel("본문").fill("저장할 본문");
  await main.getByRole("button", { name: "문서 저장" }).click();
  await page.waitForURL(/\/research\/document\/\?id=/);
  check("W3. save creates exactly one document", (await counts()).documents === before.documents + 1 && (await appears(main.getByRole("heading", { name: "저장할 새 문서" }))));
  await page.context().close();
}

// W6. #22 리뷰 F4: 새 문서 초안 A→B→A 왕복에서도 주소·입력·저장 대상이 같다. 전환 뒤 늦은 자동 저장 응답이 주소를 되돌리지 않는다.
{
  const wctx = await newPage();
  await login(wctx.page);
  const page = wctx.page;
  const main = page.locator("main");
  const api = apiClient(page);
  const body = () => main.getByLabel("본문");
  const bodyIs = (text) =>
    page.waitForFunction((t) => document.querySelector("main textarea")?.value === t, text, { timeout: 8000 }).then(() => true, () => false);
  const idOf = () => new URL(page.url()).searchParams.get("draft");
  const makeDraft = async (title, text) => {
    await page.goto(U("/research/document/new/"));
    await main.getByLabel("제목").fill(title);
    await body().fill(text);
    await page.waitForURL(/\?draft=/, { timeout: 8000 });
    await main.getByText("초안 자동 저장됨").waitFor();
    return idOf();
  };
  const a = await makeDraft("초안 A", "Body A");
  const b = await makeDraft("초안 B", "Body B");
  // B 화면에서 목록의 A → A 화면에서 목록의 B → 다시 A
  await main.getByRole("link", { name: "초안 A" }).click();
  check("W6. B→A: address and body are A", (await page.waitForURL(new RegExp(`draft=${a}`)).then(() => true, () => false)) && (await bodyIs("Body A")));
  await main.getByRole("link", { name: "초안 B" }).click();
  check("W6. A→B: address and body are B", (await page.waitForURL(new RegExp(`draft=${b}`)).then(() => true, () => false)) && (await bodyIs("Body B")));
  await main.getByRole("link", { name: "초안 A" }).click();
  check("W6. B→A again: address and body are A", (await page.waitForURL(new RegExp(`draft=${a}`)).then(() => true, () => false)) && (await bodyIs("Body A")));
  await body().fill("Edit intended for A");
  await main.getByText("초안 자동 저장됨").waitFor();
  const da = await api.get(`/research/document-drafts/${a}`);
  const db = await api.get(`/research/document-drafts/${b}`);
  check("W6. edit saved to A only", da.content === "Edit intended for A" && db.content === "Body B" && db.revision === 1, JSON.stringify({ a: da.content, b: db.content, rb: db.revision }));

  // A에서 입력 → A 저장 응답 보류 중 B로 전환 → 응답 해제: 주소는 B 그대로, A에는 마지막 입력이 저장됨
  let release;
  let seen;
  const gate = new Promise((r) => (release = r));
  const arrived = new Promise((r) => (seen = r));
  const pattern = `**/api/research/document-drafts/${a}`;
  await page.route(pattern, async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    const response = await route.fetch();
    seen();
    await gate;
    return route.fulfill({ response });
  });
  await body().fill("Late A");
  await arrived;
  await main.getByRole("link", { name: "초안 B" }).click();
  await page.waitForURL(new RegExp(`draft=${b}`));
  release();
  await page.waitForTimeout(1500);
  await page.unroute(pattern);
  check("W6. late A save does not move the address back", idOf() === b && (await bodyIs("Body B")), page.url());
  check("W6. late A save landed on A", (await api.get(`/research/document-drafts/${a}`)).content === "Late A");

  // 리뷰 F4 잔여: 다른 초안 조회가 끝나기 전에 뒤로 가기 → 내렸던 편집기 상태를 되살리지 않고 서버에서 다시 읽는다.
  const holdGet = async (id) => {
    let release;
    let seen;
    const gate = new Promise((r) => (release = r));
    const arrived = new Promise((r) => (seen = r));
    const pat = `**/api/research/document-drafts/${id}`;
    await page.route(pat, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      seen();
      await gate;
      // 뒤로 가기로 요청이 이미 취소됐을 수 있다.
      return route.continue().catch(() => {});
    });
    return { release, arrived, done: () => page.unroute(pat) };
  };
  // (1) 이 화면에서 만든 C(편집기에는 서버 초안 객체가 없던 경우)
  const c = await makeDraft("초안 C", "Body C");
  const g1 = await holdGet(a);
  await main.getByRole("link", { name: "초안 A" }).click();
  await g1.arrived;
  await page.goBack();
  await page.waitForURL(new RegExp(`draft=${c}`));
  check("W6. back during load: created draft C reloaded (not blank)", (await bodyIs("Body C")) && (await main.getByLabel("제목").inputValue()) === "초안 C");
  g1.release();
  await g1.done();
  await body().fill("Body C edited");
  await main.getByText("초안 자동 저장됨").waitFor();
  check("W6. back during load: edits go to C, no new draft id", idOf() === c && (await api.get(`/research/document-drafts/${c}`)).content === "Body C edited", page.url());
  // (2) 불러왔던 A로 돌아올 때 오래된 스냅샷이 아니라 최신 내용
  await main.getByRole("link", { name: "초안 A" }).click();
  await page.waitForURL(new RegExp(`draft=${a}`));
  await bodyIs("Late A");
  await api.send("PUT", `/research/document-drafts/${a}`, { title: "초안 A", content: "A from elsewhere", expectedDraftRevision: (await api.get(`/research/document-drafts/${a}`)).revision });
  const g2 = await holdGet(b);
  await main.getByRole("link", { name: "초안 B" }).click();
  await g2.arrived;
  await page.goBack();
  await page.waitForURL(new RegExp(`draft=${a}`));
  check("W6. back during load: loaded draft A shows latest server content", await bodyIs("A from elsewhere"));
  g2.release();
  await g2.done();
  await page.waitForTimeout(1000);
  check("W6. back during load: address and body stay A after late B response", idOf() === a && (await bodyIs("A from elsewhere")), page.url());
  await page.context().close();
}

// W7. #22 리뷰 F5: 새 문서 초안 목록은 다음 페이지도 찾는다(30개 초과, 첫 페이지가 기존 문서 초안뿐인 경우).
{
  const wctx = await newPage();
  await login(wctx.page);
  const page = wctx.page;
  const main = page.locator("main");
  const api = apiClient(page);
  const oldest = await api.send("POST", "/research/document-drafts", { title: "가장 오래된 새 초안", content: "x" });
  for (let k = 0; k < 31; k++) {
    const d = await api.send("POST", "/research/documents", { title: `기존 문서 ${k}`, content: "본문" });
    await api.send("POST", "/research/document-drafts", {
      title: `기존 문서 초안 ${k}`,
      content: "y",
      documentId: d.body.id,
      baseDocumentVersion: d.body.version,
      baseVersionId: d.body.latestVersion.id,
    });
  }
  check("W7. setup: first API page has only existing-document drafts", oldest.status === 201 && (await api.get("/research/document-drafts")).items.every((d) => d.documentId !== null));
  await page.goto(U("/research/document/new/"));
  check("W7. new-document draft found beyond the first page", await appears(main.getByRole("link", { name: "가장 오래된 새 초안" }), 10000));
  for (let k = 0; k < 31; k++) await api.send("POST", "/research/document-drafts", { title: `새 초안 ${k}`, content: "z" });
  await page.goto(U("/research/document/new/"));
  await main.getByRole("link", { name: "새 초안 30" }).waitFor();
  const more = main.getByRole("button", { name: "초안 더 보기" });
  check("W7. more than 30: show-more offered", await appears(more));
  while (await more.isVisible().catch(() => false)) {
    await more.click();
    await page.waitForTimeout(300);
  }
  check("W7. oldest draft reachable via show-more", await appears(main.getByRole("link", { name: "가장 오래된 새 초안" })));
  check("W7. only new-document drafts listed", (await main.getByRole("link", { name: /기존 문서 초안/ }).count()) === 0);
  await page.context().close();
}

// R10. 다른 사용자: 타인 ID는 404로 숨김
{
  const other = await newPage();
  await login(other.page);
  const om = other.page.locator("main");
  await other.page.goto(materialAUrl);
  check("R10. foreign material -> not found", await appears(om.getByText("항목을 찾을 수 없습니다")));
  await other.page.goto(sessionUrl);
  check("R10. foreign session -> not found", await appears(other.page.locator("#chat-panel").getByText("항목을 찾을 수 없습니다")));
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
{
  // 남아도 되는 것은 계정별 대화창 상태(열림·선택 대화)뿐이다. 인증 정보·CSRF nonce는 저장하지 않는다.
  const stored = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }));
  const keys = Object.keys(stored.session);
  const only = keys.every((k) => /^research:pending:[0-9a-f-]{36}:chat-ui$/.test(k));
  const text = JSON.stringify(stored);
  check(
    "R11. only per-account chat UI state in storage (no token/CSRF)",
    Object.keys(stored.local).length === 0 && only && !/csrf|token|cookie/i.test(text),
    text
  );
}

check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
check("no external image/tracker requests", ![...externalHosts].some((h) => h.includes("tracker") || h.includes("example.com")), [...externalHosts].join(", "));

await context.close();
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
