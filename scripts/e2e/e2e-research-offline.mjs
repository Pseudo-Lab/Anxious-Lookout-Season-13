// 실제 API + 실제 adapter + offline protocol fixture(backend/compose.m3-offline.yml)에서 세션 UI를 브라우저로 확인한다.
// 모델/Codex provider/credential 없음(verification=fixture, 응답 "Offline fixture answer"). UI 모의 제어(__mock) 미사용.
// 조율(live-offline.sh): <name>.id → 승인 → <name>.ok, 승인 후 <name>.account(accountId) → runner 구성 → offline.ready,
// 재시작 요청 restart.request → restart.done.
import { chromium } from "playwright";
import fs from "node:fs/promises";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:18100";
const SHARED = process.env.SHARED ?? "/shared";
const U = (p) => `${ORIGIN}${p}`;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
async function appears(locator, timeout = 20000) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}
async function waitFile(name, timeoutMs = 300_000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      await fs.access(`${SHARED}/${name}`);
      return;
    } catch {
      if (Date.now() > deadline) throw new Error(`timeout waiting for ${name}`);
      await new Promise((r) => setTimeout(r, 500));
    }
  }
}

const browser = await chromium.launch();
const consoleErrors = [];
async function newPage() {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text());
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
async function approvedAccount(name, page) {
  await signIn(page);
  const me = await (await page.request.get(U("/api/auth/me"))).json();
  await fs.writeFile(`${SHARED}/${name}.id`, me.user.githubId);
  await waitFile(`${name}.ok`);
  await signIn(page); // 승인은 기존 session을 폐기한다
  const after = await (await page.request.get(U("/api/auth/me"))).json();
  await fs.writeFile(`${SHARED}/${name}.account`, after.user.accountId);
  return after.user;
}
function apiClient(page) {
  return {
    async get(path) {
      const r = await page.request.get(U(`/api${path}`));
      return { status: r.status(), body: await r.json().catch(() => null) };
    },
  };
}

const A = await newPage();
const B = await newPage();
const userA = await approvedAccount("A", A.page);
const userB = await approvedAccount("B", B.page);
check("setup. two approved fixture accounts", userA.isApproved && userB.isApproved && userA.accountId !== userB.accountId);
await waitFile("offline.ready");

const page = A.page;
const main = page.locator("main");
const api = apiClient(page);
const fixtureDocs = async (p = api) =>
  (await p.get("/research/documents?limit=100")).body.items.filter((d) => d.title === "Fixture document").length;
const userMsgs = (m, text) => m.locator("li.bg-indigo-50", { hasText: text });

async function newSession(p, title) {
  await p.goto(U("/research/?tab=sessions"));
  const m = p.locator("main");
  await m.getByLabel("새 대화 제목").fill(title);
  await m.getByRole("button", { name: "새 대화" }).click();
  await p.waitForURL(/\/research\/session\/\?id=/);
  await m.getByLabel("메시지").waitFor();
  return p.url();
}
async function send(m, text) {
  await m.getByLabel("메시지").fill(text);
  await m.getByRole("button", { name: "보내기" }).click();
}
async function settled(m) {
  // running 안내가 사라지고 입력창이 다시 활성화될 때까지
  await m.getByLabel("메시지").and(m.locator(":enabled")).waitFor({ timeout: 30000 });
}

// O1. 상태: fixture 연결을 실제 모델처럼 표시하지 않는다
await page.goto(U("/research/?tab=sessions"));
check("O1. codex status shown as fixture (not real)", await appears(main.getByText("테스트용 모의 Codex")));
check("O1. server-fixed model shown display-only", await appears(main.getByText("모델: gpt-6.1-sol (서버에서 고정)")));

// O2. 일반 메시지: 실제 adapter → 실제 storage tool callback → 전체 도구 입력·출력과 원본
const s1 = await newSession(page, "오프라인 세션 1");
const docs0 = await fixtureDocs();
await send(main, "자료를 정리해줘");
check("O2. running shown", await appears(main.getByText("Codex가 응답하는 중입니다."), 10000));
check("O2. offline answer shown", await appears(main.getByText("Offline fixture answer")));
await settled(main);
const toolBtn = main.getByRole("button", { name: /research_document_save/ });
check("O2. tool call listed", await appears(toolBtn));
await toolBtn.first().click();
check("O2. full tool input shown inert", await appears(main.locator("pre", { hasText: "Complete fixture tool input" })));
await main.getByRole("button", { name: "저장된 원본 기록 보기" }).first().click();
check("O2. stored raw item shown", await appears(main.locator("pre", { hasText: "research_document_save" })));
check("O2. tool saved owned document via real API", (await fixtureDocs()) === docs0 + 1, `${docs0} -> ${await fixtureDocs()}`);

// O3. 미기록 실패 입력: failed + platform 표시, 후속 명시 입력 성공 후에도 보존
await send(main, "/fixture/unrecorded");
const unrec = userMsgs(main, "/fixture/unrecorded");
check("O3. unrecorded input labeled 미기록", await appears(unrec.getByText("미기록 입력")));
check("O3. failed state shown", await appears(main.getByText("마지막 요청이 실패했습니다")));
await unrec.getByRole("button", { name: "보존된 입력 기록" }).click();
check("O3. raw is platformInput, not Codex original", await appears(unrec.locator("pre", { hasText: "platformInput" })) && (await unrec.getByText("Codex 원본 아님").count()) === 1);
await send(main, "후속 질문");
check("O3. explicit follow-up succeeds", await appears(userMsgs(main, "후속 질문")) && (await appears(main.getByText("Offline fixture answer").nth(1))));
await settled(main);
check("O3. failed input still preserved", (await userMsgs(main, "/fixture/unrecorded").count()) === 1);

// O4. 명확 거절: 202 후 failed/codex_rejected, 새 명시 입력 가능
await send(main, "/fixture/reject");
check("O4. codex_rejected shown", await appears(main.getByText("Codex가 이 요청을 받지 않았습니다")));
await send(main, "거절 뒤 질문");
check("O4. new explicit message after rejection succeeds", await appears(main.getByText("Offline fixture answer").nth(2)));
await settled(main);

// O5. 응답 유실 → turn 완료 → 같은 key·본문 재전송: 실제 202 replay, 도구 문서 1건
const postTimings = [];
async function dropFirstSend(p) {
  let dropped = false;
  const keys = [];
  await p.route("**/api/research/sessions/*/messages", async (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (!dropped) {
      dropped = true;
      const t0 = Date.now();
      const res = await route.fetch();
      postTimings.push({ status: res.status(), ms: Date.now() - t0 });
      // 브라우저 쪽 요청이 그 사이 timeout으로 취소됐으면 이미 처리된 route다.
      return route.abort().catch(() => {});
    }
    return route.continue().catch(() => {});
  });
  return keys;
}
{
  const before = await fixtureDocs();
  const keys = await dropFirstSend(page);
  await send(main, "재전송 질문");
  check("O5. lost send -> uncertain notice", await appears(main.getByRole("button", { name: "다시 보내기" })));
  await appears(main.getByText("Offline fixture answer").nth(3));
  await settled(main);
  await main.getByRole("button", { name: "다시 보내기" }).click();
  await main.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await page.reload();
  await main.getByLabel("메시지").waitFor();
  check("O5. replay kept same key", keys.length === 2 && keys[0] === keys[1], JSON.stringify(keys));
  check("O5. one user message", (await userMsgs(main, "재전송 질문").count()) === 1);
  check("O5. one tool document (no second dispatch)", (await fixtureDocs()) === before + 1, `${before} -> ${await fixtureDocs()}`);
}

// O6. 응답 유실 → 새로고침(재접속) → 입력·key 복구 → 재전송: 중복 없음
{
  const before = await fixtureDocs();
  await dropFirstSend(page);
  await send(main, "새로고침 질문");
  // 알림은 요청 중에도 보이므로, 실패 처리가 끝난("다시 보내기") 뒤에 route를 해제한다.
  await main.getByRole("button", { name: "다시 보내기" }).waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await page.reload();
  await main.getByLabel("메시지").waitFor();
  check("O6. text restored after reload", (await main.getByLabel("메시지").inputValue()) === "새로고침 질문");
  await appears(userMsgs(main, "새로고침 질문"));
  await settled(main);
  await main.getByRole("button", { name: "다시 보내기" }).click();
  await main.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.reload();
  await main.getByLabel("메시지").waitFor();
  check("O6. one user message after reconnect replay", (await userMsgs(main, "새로고침 질문").count()) === 1);
  check("O6. one tool document", (await fixtureDocs()) === before + 1, `${before} -> ${await fixtureDocs()}`);
}

// O7. 같은 계정 다른 세션 busy(hold 중) / B runner 독립
{
  const s2Page = await A.context.newPage();
  s2Page.on("dialog", (d) => void d.accept());
  const s2 = await newSession(s2Page, "오프라인 세션 2");
  const m2 = s2Page.locator("main");
  const bUrl = await newSession(B.page, "B 세션");
  const bm = B.page.locator("main");
  await page.goto(s1);
  await main.getByLabel("메시지").waitFor();
  await send(main, "/fixture/hold");
  await main.getByText("Codex가 응답하는 중입니다.").waitFor();
  await send(m2, "hold 중 다른 세션");
  await send(bm, "B 질문");
  check("O7. same-account other session busy -> rejected", await appears(m2.getByText("Codex가 이 요청을 받지 않았습니다")));
  check("O7. account B independent during A hold", await appears(bm.getByText("Offline fixture answer")));
  check("O7. held turn completes", await appears(main.getByText("Offline fixture answer").nth(5), 30000));
  await settled(main);
  await s2Page.close();
  check("O7. s2 url recorded", !!s2 && !!bUrl);
}

// O8. API·두 adapter 재시작 뒤: 기록 유지, 이전 미확인 요청 replay 무중복, 명시적 이어하기
{
  const before = await fixtureDocs();
  await dropFirstSend(page);
  await send(main, "재시작 전 질문");
  await main.getByRole("button", { name: "다시 보내기" }).waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await appears(main.getByText("Offline fixture answer").nth(6));
  await settled(main);
  await fs.writeFile(`${SHARED}/restart.request`, "1");
  await waitFile("restart.done");
  await page.reload();
  await main.getByLabel("메시지").waitFor();
  check("O8. history retained after restart", (await userMsgs(main, "자료를 정리해줘").count()) === 1 && (await main.getByText("Offline fixture answer").count()) >= 7);
  check("O8. pending tuple restored", (await main.getByLabel("메시지").inputValue()) === "재시작 전 질문", await main.getByLabel("메시지").inputValue());
  await main.getByRole("button", { name: "다시 보내기" }).click();
  await main.getByRole("button", { name: "보내기", exact: true }).waitFor();
  check("O8. old tuple replay creates no new document", (await fixtureDocs()) === before + 1, `${before} -> ${await fixtureDocs()}`);
  await send(main, "재시작 후 이어하기");
  const continued = await appears(main.getByText("Offline fixture answer").nth(7), 30000);
  check(
    "O8. explicit continue after restart",
    continued,
    continued ? "" : (await main.innerText()).slice(-300).replace(/\n+/g, " / ")
  );
  await settled(main);
  check("O8. continue created exactly one more document", (await fixtureDocs()) === before + 2);
}

// O10. #7 모델 우회/불일치(model/rerouted): 고정 모델 성공으로 표시하지 않고 codex_model_unavailable, provider 원문 비노출, 후속 명시 입력 가능
{
  const before = await fixtureDocs();
  await page.goto(s1);
  await main.getByLabel("메시지").waitFor();
  await send(main, "/fixture/rerouted");
  check("O10. rerouted -> fixed-model failure shown", await appears(main.getByText("고정된 모델을 사용할 수 없어")));
  const t = await main.innerText();
  check("O10. wrong model / provider reason not shown", !t.includes("fixture-wrong-model") && !t.includes("highRiskCyberActivity"));
  check("O10. rerouted input kept in conversation", (await userMsgs(main, "/fixture/rerouted").count()) === 1);
  await settled(main);
  await send(main, "우회 뒤 질문");
  check("O10. explicit follow-up after reroute succeeds", await appears(userMsgs(main, "우회 뒤 질문")) && (await appears(main.getByText("Offline fixture answer").nth(8), 30000)));
  await settled(main);
  const after = await fixtureDocs();
  check("O10. rerouted turn did not add a tool write; follow-up added one", after === before + 1, `${before} -> ${after}`);
}

// O9. 계정 격리: B는 A 세션·도구 문서 404, B 목록에 A 세션 없음
{
  const bm = B.page.locator("main");
  await B.page.goto(s1);
  check("O9. B cannot open A session", await appears(bm.getByText("항목을 찾을 수 없습니다")));
  const bDocs = await fixtureDocs(apiClient(B.page));
  check("O9. B has exactly its own tool document", bDocs === 1, String(bDocs));
  await B.page.goto(U("/research/?tab=sessions"));
  check("O9. B list lacks A sessions", (await appears(bm.getByText("B 세션"))) && (await bm.getByText("오프라인 세션 1").count()) === 0);
}

console.log(`INFO dropped message POST timings: ${JSON.stringify(postTimings)}`);
check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
