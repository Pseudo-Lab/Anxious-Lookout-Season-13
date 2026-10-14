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
// #10 feature-off(CODEX_USER_RUNNERS_ENABLE 기본 false): 새 web은 준비 POST를 보내지 않고 기존 상태 조회로 동작해야 한다.
const runnerPosts = [];
async function newPage() {
  const context = await browser.newContext();
  context.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname.endsWith("/research/codex/runner")) runnerPosts.push(r.url());
  });
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
// #9 공통 대화창. 대화는 모든 페이지의 대화창에서 하고, 질문 범위는 보낼 때 보고 있는 페이지로 정해진다.
// fixture native의 기본 도구는 research_document_save라 성공 왕복은 문서 작업 공간(내 연구 문서 탭)에서 보낸다.
const DOCS = U("/research/?tab=documents");
const chatOf = (p) => p.locator("#chat-panel");
const chatUi = (p) =>
  p.evaluate(() => {
    const k = Object.keys(sessionStorage).find((key) => key.endsWith(":chat-ui"));
    return k ? JSON.parse(sessionStorage.getItem(k)) : null;
  });

// 차단된 계정 runner에 대한 후속 전송: 화면은 상태를 다시 읽어 보내기를 잠그고, 서버 선접수 거절은 API로 직접 판정한다.
async function blockedFollowUp(p, client, sessionUrl, text) {
  const m = chatOf(p);
  await m.getByLabel("메시지").fill(text);
  const locked = (await appears(m.getByText("보내기가 잠겨 있습니다"))) && (await m.getByRole("button", { name: "보내기" }).isDisabled());
  const sid = new URL(sessionUrl).searchParams.get("id");
  const detail = await client.get(`/research/sessions/${sid}`);
  const post = await client.send("POST", `/research/sessions/${sid}/messages`, { text, expectedVersion: detail.body.version });
  const after = await client.get(`/research/sessions/${sid}`);
  return { locked, post, unchanged: after.body.items.length === detail.body.items.length && after.body.version === detail.body.version };
}

const A = await newPage();
const B = await newPage();
const userA = await approvedAccount("A", A.page);
const userB = await approvedAccount("B", B.page);
check("setup. two approved fixture accounts", userA.isApproved && userB.isApproved && userA.accountId !== userB.accountId);
check("setup. feature-off API: codexRunner is false (not absent)", userA.codexRunner === false && userB.codexRunner === false, `${userA.codexRunner} ${userB.codexRunner}`);
await waitFile("offline.ready");

const page = A.page;
const main = page.locator("main");
const chat = chatOf(page);
const api = apiClient(page);
const fixtureDocs = async (p = api) =>
  (await p.get("/research/documents?limit=100")).body.items.filter((d) => d.title === "Fixture document").length;
const userMsgs = (m, text) => m.locator("li[data-role=user]", { hasText: text });

// 새 대화는 페이지 이동 없이 대화창에 열린다. 그 뒤 질문할 페이지(기본: 문서 작업 공간)로 옮긴다. 반환값은 기존 대화 링크.
async function newSession(p, title, at = DOCS) {
  await p.goto(U("/research/?tab=sessions"));
  const m = p.locator("main");
  await m.getByLabel("새 대화 제목").fill(title);
  await m.getByRole("button", { name: "새 대화" }).click();
  await chatOf(p).getByRole("heading", { name: title }).waitFor();
  const url = U(`/research/session/?id=${(await chatUi(p)).sessionId}`);
  await p.goto(at);
  await chatOf(p).getByLabel("메시지").waitFor();
  return url;
}
// 기존 대화 링크로 대화창에 연 뒤 질문할 페이지로 옮긴다.
async function openAt(p, sessionUrl, at = DOCS) {
  await p.goto(sessionUrl);
  await chatOf(p).getByLabel("메시지").waitFor();
  await p.goto(at);
  await chatOf(p).getByLabel("메시지").waitFor();
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
await send(chat, "자료를 정리해줘");
check("O2. running shown", await appears(chat.getByText("Codex가 응답하는 중입니다."), 10000));
check("O2. offline answer shown", await appears(chat.getByText("Offline fixture answer")));
await settled(chat);
const toolBtn = chat.getByRole("button", { name: /research_document_save/ });
check("O2. tool call listed", await appears(toolBtn));
await toolBtn.first().click();
check("O2. full tool input shown inert", await appears(chat.locator("pre", { hasText: "Complete fixture tool input" })));
await chat.getByRole("button", { name: "저장된 원본 기록 보기" }).first().click();
check("O2. stored raw item shown", await appears(chat.locator("pre", { hasText: "research_document_save" })));
check("O2. tool saved owned document via real API", (await fixtureDocs()) === docs0 + 1, `${docs0} -> ${await fixtureDocs()}`);

// O3. 미기록 실패 입력: failed + platform 표시, 후속 명시 입력 성공 후에도 보존
await send(chat, "/fixture/unrecorded");
const unrec = userMsgs(chat, "/fixture/unrecorded");
check("O3. unrecorded input labeled 미기록", await appears(unrec.getByText("미기록 입력")));
check("O3. failed state shown", await appears(chat.getByText("마지막 요청이 실패했습니다")));
await unrec.getByRole("button", { name: "보존된 입력 기록" }).click();
check("O3. raw is platformInput, not Codex original", await appears(unrec.locator("pre", { hasText: "platformInput" })) && (await unrec.getByText("Codex 원본 아님").count()) === 1);
await send(chat, "후속 질문");
check("O3. explicit follow-up succeeds", await appears(userMsgs(chat, "후속 질문")) && (await appears(chat.getByText("Offline fixture answer").nth(1))));
await settled(chat);
check("O3. failed input still preserved", (await userMsgs(chat, "/fixture/unrecorded").count()) === 1);

// O4. 명확 거절: 202 후 failed/codex_rejected, 새 명시 입력 가능
await send(chat, "/fixture/reject");
check("O4. codex_rejected shown", await appears(chat.getByText("Codex가 이 요청을 받지 않았습니다")));
await send(chat, "거절 뒤 질문");
check("O4. new explicit message after rejection succeeds", await appears(chat.getByText("Offline fixture answer").nth(2)));
await settled(chat);

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
  await send(chat, "재전송 질문");
  check("O5. lost send -> uncertain notice", await appears(chat.getByRole("button", { name: "다시 보내기" })));
  await appears(chat.getByText("Offline fixture answer").nth(3));
  await settled(chat);
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  check("O5. replay kept same key", keys.length === 2 && keys[0] === keys[1], JSON.stringify(keys));
  check("O5. one user message", (await userMsgs(chat, "재전송 질문").count()) === 1);
  check("O5. one tool document (no second dispatch)", (await fixtureDocs()) === before + 1, `${before} -> ${await fixtureDocs()}`);
}

// O6. 응답 유실 → 새로고침(재접속) → 입력·key 복구 → 재전송: 중복 없음
{
  const before = await fixtureDocs();
  await dropFirstSend(page);
  await send(chat, "새로고침 질문");
  // 알림은 요청 중에도 보이므로, 실패 처리가 끝난("다시 보내기") 뒤에 route를 해제한다.
  await chat.getByRole("button", { name: "다시 보내기" }).waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  check("O6. text restored after reload", (await chat.getByLabel("메시지").inputValue()) === "새로고침 질문");
  await appears(userMsgs(chat, "새로고침 질문"));
  await settled(chat);
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  check("O6. one user message after reconnect replay", (await userMsgs(chat, "새로고침 질문").count()) === 1);
  check("O6. one tool document", (await fixtureDocs()) === before + 1, `${before} -> ${await fixtureDocs()}`);
}

// O7. 같은 계정 다른 세션 busy(hold 중) / B runner 독립
{
  const s2Page = await A.context.newPage();
  s2Page.on("dialog", (d) => void d.accept());
  const s2 = await newSession(s2Page, "오프라인 세션 2");
  const m2 = chatOf(s2Page);
  const bUrl = await newSession(B.page, "B 세션");
  const bm = chatOf(B.page);
  await openAt(page, s1);
  await send(chat, "/fixture/hold");
  await chat.getByText("Codex가 응답하는 중입니다.").waitFor();
  await send(m2, "hold 중 다른 세션");
  await send(bm, "B 질문");
  check("O7. same-account other session busy -> rejected", await appears(m2.getByText("Codex가 이 요청을 받지 않았습니다")));
  check("O7. account B independent during A hold", await appears(bm.getByText("Offline fixture answer")));
  check("O7. held turn completes", await appears(chat.getByText("Offline fixture answer").nth(5), 30000));
  await settled(chat);
  await s2Page.close();
  check("O7. s2 url recorded", !!s2 && !!bUrl);
}

// O8. API·두 adapter 재시작 뒤: 기록 유지, 이전 미확인 요청 replay 무중복, 명시적 이어하기
{
  const before = await fixtureDocs();
  await dropFirstSend(page);
  await send(chat, "재시작 전 질문");
  await chat.getByRole("button", { name: "다시 보내기" }).waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  await appears(chat.getByText("Offline fixture answer").nth(6));
  await settled(chat);
  await fs.writeFile(`${SHARED}/restart.request`, "1");
  await waitFile("restart.done");
  await page.reload();
  await chat.getByLabel("메시지").waitFor();
  check("O8. history retained after restart", (await userMsgs(chat, "자료를 정리해줘").count()) === 1 && (await chat.getByText("Offline fixture answer").count()) >= 7);
  check("O8. pending tuple restored", (await chat.getByLabel("메시지").inputValue()) === "재시작 전 질문", await chat.getByLabel("메시지").inputValue());
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  check("O8. old tuple replay creates no new document", (await fixtureDocs()) === before + 1, `${before} -> ${await fixtureDocs()}`);
  await send(chat, "재시작 후 이어하기");
  const continued = await appears(chat.getByText("Offline fixture answer").nth(7), 30000);
  check(
    "O8. explicit continue after restart",
    continued,
    continued ? "" : (await chat.innerText()).slice(-300).replace(/\n+/g, " / ")
  );
  await settled(chat);
  check("O8. continue created exactly one more document", (await fixtureDocs()) === before + 2);
}

// I. #9 통합(실제 API + 실제 runner + offline fixture): 요청별 페이지 범위를 서버가 고정·강제하고 화면이 그대로 표시한다.
//    fixture native는 매 turn research_document_save(또는 /fixture/page-context에서 research_page_context)를 호출한다.
{
  const s3 = await newSession(page, "범위 세션", U("/"));
  const header = page.locator("header");
  const bodies = [];
  const record = async (route) => {
    bodies.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    return route.continue().catch(() => {});
  };
  await page.route("**/api/research/sessions/*/messages", record);
  const answers = () => chat.getByText("Offline fixture answer");
  const lastTool = (name = "research_document_save") => chat.locator("li", { hasText: name }).last();
  let docs = await fixtureDocs();

  // I1. general(홈): 도구 없는 질문·답변. 실제 callback 거부가 rejected로 투영되고 문서는 생기지 않는다.
  check("I1. home shows general scope", await appears(chat.getByText("다음 질문 범위: 일반 질문")));
  await send(chat, "일반 페이지 질문");
  check("I1. general: answer shown", await appears(answers().nth(0)));
  await settled(chat);
  check("I1. general: body page {kind:general}", JSON.stringify(bodies.at(-1)?.body.page) === '{"kind":"general"}', JSON.stringify(bodies.at(-1)?.body));
  check("I1. general: accepted scope badge from server", await appears(userMsgs(chat, "일반 페이지 질문").getByText("보낸 범위: 일반 질문")));
  check(
    "I1. general: real tool callback refused -> rejected shown",
    (await appears(lastTool().getByText("거부됨"))) && (await appears(lastTool().getByText("허용되지 않는 도구라 서버가 실행하지 않았습니다")))
  );
  check("I1. general: no document written", (await fixtureDocs()) === docs, `${docs} -> ${await fixtureDocs()}`);

  // I2. 문서 작업 공간: 같은 대화에서 다음 질문만 저장 허용
  await page.goto(DOCS);
  await chat.getByLabel("메시지").waitFor();
  check("I2. documents tab shows document_workspace scope", await appears(chat.getByText("다음 질문 범위: 내 문서")));
  await send(chat, "문서 목록 질문");
  check("I2. workspace: answer shown", await appears(answers().nth(1)));
  await settled(chat);
  check("I2. workspace: body page", JSON.stringify(bodies.at(-1)?.body.page) === '{"kind":"document_workspace"}', JSON.stringify(bodies.at(-1)?.body.page));
  check("I2. workspace: scope badge", await appears(userMsgs(chat, "문서 목록 질문").getByText("보낸 범위: 내 문서")));
  check("I2. workspace: tool completed", await appears(lastTool().getByText("completed")));
  check("I2. workspace: exactly one document written", (await fixtureDocs()) === docs + 1, `${docs} -> ${await fixtureDocs()}`);
  check("I2. earlier general turn kept in same conversation", (await userMsgs(chat, "일반 페이지 질문").count()) === 1);
  docs = await fixtureDocs();

  // I3. 실행 중 페이지 이동: 접수된 요청 범위(문서 작업 공간)는 바뀌지 않는다
  await send(chat, "/fixture/hold");
  await chat.getByText("Codex가 응답하는 중입니다.").waitFor();
  await header.getByRole("link", { name: "홈" }).first().click();
  await page.waitForURL(U("/"));
  check("I3. held answer arrives after navigation", await appears(answers().nth(2), 30000));
  await settled(chat);
  check("I3. navigation during run: tool still completed", await appears(lastTool().getByText("completed")));
  check("I3. navigation during run: document written once", (await fixtureDocs()) === docs + 1, `${docs} -> ${await fixtureDocs()}`);
  check("I3. sent scope stays 내 문서", await appears(userMsgs(chat, "/fixture/hold").getByText("보낸 범위: 내 문서")));
  check("I3. next scope follows current page", await appears(chat.getByText("다음 질문 범위: 일반 질문")));
  docs = await fixtureDocs();

  // I4. 결과 불명 → 다른 페이지로 이동 → 다시 보내기: 처음 key·body(page 포함) 그대로, 실제 replay, 409 없음, 중복 없음
  await page.goto(DOCS);
  await chat.getByLabel("메시지").waitFor();
  await page.unroute("**/api/research/sessions/*/messages");
  const sent = [];
  let dropped = false;
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    sent.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() });
    if (!dropped) {
      dropped = true;
      await route.fetch();
      return route.abort().catch(() => {});
    }
    return route.continue().catch(() => {});
  });
  await send(chat, "범위 고정 재전송");
  await chat.getByRole("button", { name: "다시 보내기" }).waitFor();
  await appears(answers().nth(3));
  await settled(chat);
  await header.getByRole("link", { name: "상태" }).first().click();
  await page.waitForURL(/\/status\/$/);
  check("I4. retry notice names original scope", await appears(chat.getByText("처음 보낸 범위(내 문서)로 처리됩니다", { exact: false })));
  await chat.getByRole("button", { name: "다시 보내기" }).click();
  await chat.getByRole("button", { name: "보내기", exact: true }).waitFor();
  check(
    "I4. retry from other page reused key and body",
    sent.length === 2 && sent[0].key === sent[1].key && JSON.stringify(sent[0].body) === JSON.stringify(sent[1].body) && sent[1].body.page?.kind === "document_workspace",
    JSON.stringify(sent.map((x) => x.body.page))
  );
  check("I4. replay accepted (no conflict error)", (await chat.getByText("메시지를 보내지 못했습니다").count()) === 0);
  check("I4. one user message", (await userMsgs(chat, "범위 고정 재전송").count()) === 1);
  check("I4. no second dispatch (one document)", (await fixtureDocs()) === docs + 1, `${docs} -> ${await fixtureDocs()}`);
  await page.unroute("**/api/research/sessions/*/messages");
  await page.route("**/api/research/sessions/*/messages", record);
  docs = await fixtureDocs();

  // I5. 없는 문서 상세에서 질문: 접수 전 404, general로 낮추지 않음, 입력 유지
  await page.goto(U(`/research/document/?id=${crypto.randomUUID()}`));
  await chat.getByLabel("메시지").waitFor();
  check("I5. detail page shows document scope", await appears(chat.getByText("다음 질문 범위: 이 문서")));
  await send(chat, "없는 문서 질문");
  check("I5. unknown resource -> refused before admission", await appears(chat.getByText("메시지를 보내지 못했습니다.")));
  check("I5. input kept", (await chat.getByLabel("메시지").inputValue()) === "없는 문서 질문");
  check("I5. nothing added", (await userMsgs(chat, "없는 문서 질문").count()) === 0 && (await fixtureDocs()) === docs);
  await chat.getByLabel("메시지").fill("");

  // I6. 공개 글 상세: research_page_context만 허용. 같은 호출을 홈에서 보내면 거부.
  const pubDoc = await api.send("POST", "/research/documents", { title: "범위 공개 글", content: "공개 범위 본문" });
  const pv = pubDoc.status === 201 ? await api.get(`/research/documents/${pubDoc.body.id}/publication-preview`) : null;
  const pub = pv?.status === 200
    ? await api.send("POST", `/research/documents/${pubDoc.body.id}/publications`, {
        versionId: pv.body.versionId,
        expectedVersion: pv.body.expectedVersion,
        previewToken: pv.body.previewToken,
      })
    : null;
  check("I6. setup: fixture publication", pub?.status === 201, `${pubDoc.status}/${pv?.status}/${pub?.status} ${pub?.body?.error?.code ?? ""}`);
  if (pub?.status === 201) {
    await page.goto(U(`/public/document/?id=${pubDoc.body.id}`));
    await chat.getByLabel("메시지").waitFor();
    check("I6. public document scope shown", await appears(chat.getByText("다음 질문 범위: 이 공개 글")));
    await send(chat, "/fixture/page-context");
    check("I6. public: answer shown", await appears(answers().nth(4)));
    await settled(chat);
    check(
      "I6. public: body page with id",
      bodies.at(-1)?.body.page?.kind === "public_document" && bodies.at(-1)?.body.page?.resourceId === pubDoc.body.id,
      JSON.stringify(bodies.at(-1)?.body.page)
    );
    check("I6. public: page context tool completed", await appears(lastTool("research_page_context").getByText("completed")));
    check("I6. public: badge carries server title", await appears(userMsgs(chat, "/fixture/page-context").first().getByText(/보낸 범위: 이 공개 글 · 범위 공개 글/)));
    await page.goto(U("/"));
    await chat.getByLabel("메시지").waitFor();
    await send(chat, "/fixture/page-context");
    check("I6. home: answer shown", await appears(answers().nth(5)));
    await settled(chat);
    check("I6. home: same page-context call rejected (no historical context authority)", await appears(lastTool("research_page_context").getByText("거부됨")));
  }

  // I7. 기존 대화 링크: 대화창에서 열리고 입력창은 하나, 새로고침 뒤에도 이력 유지
  await page.goto(s3);
  await chat.getByLabel("메시지").waitFor();
  check(
    "I7. legacy link opens conversation in panel with single composer",
    (await page.locator('textarea[aria-label="메시지"]').count()) === 1 && (await appears(main.getByText("이 대화를 대화창에서 열었습니다")))
  );
  check("I7. history across scopes retained", (await userMsgs(chat, "일반 페이지 질문").count()) === 1 && (await userMsgs(chat, "문서 목록 질문").count()) === 1);
  await page.unroute("**/api/research/sessions/*/messages");
  await page.goto(DOCS);
  await chat.getByLabel("메시지").waitFor();
}

// O10. #7 모델 우회/불일치(model/rerouted): codex_model_unavailable, 해당 turn의 assistant 결과는 projection에서 제외되고
//      남는 user/tool 항목은 modelMismatch 배지, 도구 쓰기 없음, provider 원문 비노출, 계정 runner 차단은 지속(후속 입력 선접수 거절, 입력 유지)
{
  const before = await fixtureDocs();
  await openAt(page, s1);
  const answersBefore = await chat.getByText("Offline fixture answer").count();
  await send(chat, "/fixture/rerouted");
  check("O10. rerouted -> fixed-model failure shown", await appears(chat.getByText("고정된 모델을 사용할 수 없어")));
  await settled(chat);
  const t = await chat.innerText();
  check("O10. wrong model / provider reason not shown", !t.includes("fixture-wrong-model") && !t.includes("highRiskCyberActivity"));
  check("O10. rerouted turn items badged not-normal", await appears(chat.getByText("정상 응답 아님")));
  check("O10. rerouted assistant result excluded", (await chat.getByText("Offline fixture answer").count()) === answersBefore, `${answersBefore} -> ${await chat.getByText("Offline fixture answer").count()}`);
  const docsAfterReroute = await fixtureDocs();
  check("O10. rerouted turn added no tool write", docsAfterReroute === before, `${before} -> ${docsAfterReroute}`);
  // 후속 POST 응답 자체로 판정한다(화면의 응답 개수로 추정하지 않는다).
  const fu = await blockedFollowUp(page, api, s1, "우회 뒤 질문");
  check("O10. account runner stays blocked: send locked by status", fu.locked);
  check("O10. follow-up POST refused before admission (503 codex_model_unavailable)", fu.post.status === 503 && fu.post.body?.error?.code === "codex_model_unavailable", `${fu.post.status} ${fu.post.body?.error?.code}`);
  check("O10. follow-up not added to conversation", fu.unchanged && (await userMsgs(chat, "우회 뒤 질문").count()) === 0);
  check("O10. no new tool write after follow-up", (await fixtureDocs()) === before);
  check("O10. follow-up input kept, no auto resend", (await chat.getByLabel("메시지").inputValue()) === "우회 뒤 질문" && (await chat.getByRole("button", { name: "다시 보내기" }).count()) === 0);
  check("O10. no new-conversation workaround suggested", !(await chat.innerText()).includes("새 대화를 시작"));
  await chat.getByLabel("메시지").fill("");
}

// O9. 계정 격리: B는 A 세션·도구 문서 404, B 목록에 A 세션 없음
{
  const bm = B.page.locator("main");
  await B.page.goto(s1);
  check("O9. B cannot open A session (panel 404)", await appears(chatOf(B.page).getByText("항목을 찾을 수 없습니다")));
  const bDocs = await fixtureDocs(apiClient(B.page));
  check("O9. B has exactly its own tool document", bDocs === 1, String(bDocs));
  await B.page.goto(U("/research/?tab=sessions"));
  check("O9. B list lacks A sessions", (await appears(bm.getByText("B 세션"))) && (await bm.getByText("오프라인 세션 1").count()) === 0);
}

console.log(`INFO dropped message POST timings: ${JSON.stringify(postTimings)}`);
// O11. #7 C4 조기 모델 불일치(turn/start 응답 전, turn ID 미확정) — 계정 B의 독립 runner에서:
//      assistant 제외, 입력은 미기록/불일치 배지로만 남고, 이전 정상 응답 보존, 도구 쓰기 0, 이후 B runner도 차단(후속 POST 503)
{
  const bm = chatOf(B.page);
  const bApi = apiClient(B.page);
  const bDocsBefore = await fixtureDocs(bApi);
  // 대화 목록 클릭은 페이지를 옮기지 않고 대화창에 연다
  await B.page.goto(U("/research/?tab=sessions"));
  await B.page.locator("main").getByRole("link", { name: "B 세션" }).click();
  await bm.getByRole("heading", { name: "B 세션" }).waitFor();
  check("O11. list click opens conversation in panel (no navigation)", B.page.url().includes("tab=sessions"));
  const bSessionUrl = U(`/research/session/?id=${(await chatUi(B.page)).sessionId}`);
  await B.page.goto(DOCS);
  await bm.getByLabel("메시지").waitFor();
  const bAnswersBefore = await bm.getByText("Offline fixture answer").count();
  await send(bm, "/fixture/early-started-missing-mismatch");
  check("O11. early mismatch -> fixed-model failure shown", await appears(bm.getByText("고정된 모델을 사용할 수 없어")));
  await settled(bm);
  const early = bm.locator("li[data-role=user]", { hasText: "/fixture/early-started-missing-mismatch" });
  const labels = await early.allInnerTexts();
  check(
    "O11. early-mismatch input shown only as unrecorded and/or not-normal",
    labels.length >= 1 && labels.every((t) => t.includes("미기록 입력") || t.includes("정상 응답 아님")),
    `items=${labels.length}`
  );
  check("O11. no assistant result for early-mismatch turn; prior answer kept", (await bm.getByText("Offline fixture answer").count()) === bAnswersBefore && bAnswersBefore >= 1);
  check("O11. no wrong model text shown", !(await bm.innerText()).includes("fixture-wrong-model"));
  check("O11. early-mismatch turn added no tool write", (await fixtureDocs(bApi)) === bDocsBefore);
  const fu = await blockedFollowUp(B.page, bApi, bSessionUrl, "B 차단 뒤 질문");
  check("O11. B send locked by status after early mismatch", fu.locked);
  check("O11. B runner blocked after early mismatch (503 codex_model_unavailable)", fu.post.status === 503 && fu.post.body?.error?.code === "codex_model_unavailable", `${fu.post.status} ${fu.post.body?.error?.code}`);
  check("O11. refused follow-up added nothing", fu.unchanged);
  check("O11. follow-up input kept", (await bm.getByLabel("메시지").inputValue()) === "B 차단 뒤 질문");
}

// I9. 같은 브라우저 계정 전환: 대화창에 A 대화·입력을 열어 둔 채 다른 GitHub 계정으로 로그인하면 A 상태를 버린다.
{
  await page.goto(s1);
  await chat.getByLabel("메시지").fill("A의 대화창 초안");
  await A.context.clearCookies({ name: "m3_fixture_identity" });
  const tab = await A.context.newPage();
  await tab.goto(U("/api/auth/github/start"));
  await tab.waitForURL(U("/"));
  const me = await (await tab.request.get(U("/api/auth/me"))).json();
  await tab.close();
  check("I9. setup: switched to a different account", me.user?.accountId && me.user.accountId !== userA.accountId);
  await page.goto(U("/"));
  await page.locator("header").getByRole("button", { name: "로그아웃" }).first().waitFor();
  const panelText = (await chat.count()) ? await chat.innerText() : "";
  check(
    "I9. switched account: no previous conversation or draft in panel",
    !panelText.includes("오프라인 세션 1") && !panelText.includes("A의 대화창 초안") && (await page.locator('textarea[aria-label="메시지"]').count()) === 0,
    panelText.slice(0, 120)
  );
  // 바뀐 계정은 새(미승인) fixture 계정이라 대화창은 권한 안내만 보이고 A 대화를 조회하지 않는다.
  await page.goto(s1);
  const gated = await appears(chat.getByText(/항목을 찾을 수 없습니다|관리자가 승인한 편집자·관리자만/));
  check(
    "I9. switched account: A's conversation link shows no A content (not found or permission notice)",
    gated && !(await chat.innerText()).includes("오프라인 세션 1") && (await page.locator('textarea[aria-label="메시지"]').count()) === 0
  );
}

check("feature-off: no runner preparation POST from any tab", runnerPosts.length === 0, String(runnerPosts.length));
check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
