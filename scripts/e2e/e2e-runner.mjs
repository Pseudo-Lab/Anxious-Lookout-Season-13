// #10 사용자별 실행 환경 화면 검증(모의 API, api-contracts/issue10-user-runners-interface-v1.md).
// 실제 controller·Pod 생성·중복 방지·격리 검증이 아니다(모의 서버의 계정별 합침은 backend 증거가 아님).
// - 진입 준비 POST: 탭 진입·로그인·새로고침·계정 전환·자격 false→true 때만 1회, 이동·대화창·조회·표시 변화·시간 경과로는 없음
// - 상태: 환경 활성화 중/사용 대기/연결 확인/정리 중/쉬는 중/준비 전/확인 필요, 일시 상태와 안내 간격이 있는 운영자 토큰 오류만
//   열린 대화창·보이는 탭에서 다시 조회(#16 api-contracts/issue16-multi-user-runners-draft.md)
// - 전송 버튼만 잠금, 초안 입력 가능·유지, 자동 전송 없음, 접수 전 거절 코드는 미확인 보관 없음
// - 이전 API(404/405)·403·500·codexRunner false·자격 전환·계정 전환의 늦은 응답·연구 권한 상실
// - #89 대화 반응성(N21): 즉시 말풍선·생각 중·말풍선 연속성·자동 스크롤·이용 가능 상태 재조회
// - #89 Enter 보내기(N22): Enter 보내기·Shift+Enter 줄바꿈·IME 조합 중 Enter 미전송·연타 한 번 전송
// - #89 접수 뒤 첫 조회 실패 복구(N23): 보내는 중 유지·조회만 재시도·완료 기록 복구
// - #89 완료 전환 바닥 추적(N24): 생각 중 → 같은 수의 긴 답변, 직접 펼친 도구 상세는 위치 유지
// - #90 스트림(N25, 모의): polling 멈춤·실시간 답변·remount 없음·중지·알림/모르는 항목·숨김 닫기/after 재연결·플래그 꺼짐 fallback
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    check(`${name} (step error)`, false, String(e?.message ?? e).split("\n")[0]);
  }
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
const mockCalls = () => fetch(`${ORIGIN}/__mock/calls`).then((r) => r.json());
const prepares = async () => (await mockCalls()).filter((c) => c.method === "POST" && c.path === "/research/codex/runner");
const statusReads = async () => (await mockCalls()).filter((c) => c.method === "GET" && c.path === "/research/codex/status").length;
const messagePosts = async () => (await mockCalls()).filter((c) => c.method === "POST" && /\/research\/sessions\/[^/]+\/messages$/.test(c.path)).length;
const meReads = async () => (await mockCalls()).filter((c) => c.method === "GET" && c.path === "/auth/me").length;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SHOTS = process.env.SHOTS_DIR;
const TAG = (process.env.EXPECT_SHA ?? "local").slice(0, 7);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const browser = await chromium.launch();
const consoleErrors = [];
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
async function track(p) {
  p.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text());
  });
  p.on("dialog", (d) => void d.accept());
  // 탭 표시 상태를 바꿀 수 있게 한다(visibilityState 재정의 + 이벤트).
  await p.addInitScript(() => {
    let hidden = false;
    Object.defineProperty(document, "visibilityState", { get: () => (hidden ? "hidden" : "visible"), configurable: true });
    Object.defineProperty(document, "hidden", { get: () => hidden, configurable: true });
    window.__setHidden = (h) => {
      hidden = h;
      document.dispatchEvent(new Event("visibilitychange"));
    };
  });
  return p;
}
const page = await track(await context.newPage());
const chat = page.locator("#chat-panel");
const setHidden = (h) => page.evaluate((v) => window.__setHidden(v), h);

async function shot(name) {
  if (!SHOTS) return;
  await page.mouse.move(5, 5);
  await page.screenshot({ path: `${SHOTS}/${TAG}-runner-${name}.png` }).catch(() => {});
}

async function login(p = page) {
  await p.goto(U("/"));
  await p.locator("header").first().getByRole("link", { name: "GitHub 로그인" }).first().click();
  await p.locator("header").first().getByText("mock-user").first().waitFor();
}
async function me(p = page) {
  return (await (await p.request.get(U("/api/auth/me"))).json()).user;
}
async function createSession(title) {
  const csrf = (await (await page.request.get(U("/api/auth/me"))).json()).csrfToken;
  const r = await page.request.post(U("/api/research/sessions"), {
    data: { title },
    headers: { Origin: ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
  });
  return (await r.json()).id;
}
async function settle() {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(600);
}
async function openPanel() {
  await settle();
  if (!(await chat.isVisible())) await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await chat.waitFor();
}
async function closePanel() {
  if (await chat.isVisible()) await chat.getByRole("button", { name: "대화창 닫기" }).click();
  await chat.waitFor({ state: "hidden" });
}
// 대화 목록의 새 대화 만들기를 CSRF 거절로 실패시켜 이 탭이 me를 다시 조회하게 한다(새로고침 없음).
async function refreshMeInTab() {
  await openPanel();
  const back = chat.getByRole("button", { name: "← 대화 목록" });
  if (await back.isVisible().catch(() => false)) await back.click();
  await chat.getByLabel("새 대화 제목").fill("me 갱신 유도");
  // 이 생성 요청 하나만 CSRF 거절로 돌려준다(서버 전체를 거절 모드로 두면 뒤따르는 준비 요청까지 거절된다).
  await page.route("**/api/research/sessions", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: { code: "csrf_invalid", message: "Invalid CSRF token" } }) })
      : route.continue()
  );
  await chat.getByRole("button", { name: "새 대화" }).click();
  await page.waitForTimeout(1200);
  await page.unroute("**/api/research/sessions");
}
const sendBtn = () => chat.getByRole("button", { name: /^(보내기|다시 보내기)$/ });
const pendingSends = () =>
  page.evaluate(() => Object.keys(sessionStorage).filter((k) => k.includes(":session-send:")).length);

await setMock(
  "me=normal&health=normal&logout=normal&nextLogin=approved&reuse=0&as=&access=approved&publish=allowed&codex=fixture&csrf=normal&window=&remaining=&turnMs=2500&pendingInput=0&stream=off" +
    "&codexRunner=true&runner=not_prepared&prepare=ok&readyMs=3000&hint=&runnerAll="
);

// N1. 진입 준비는 로그인 완료 때 1회. 이동·대화창·탭 표시 변화·시간 경과로는 보내지 않고, 새로고침은 새 진입이다.
let sid;
await step("N1", async () => {
  const before = (await prepares()).length;
  await login();
  await settle();
  const after = await prepares();
  check("N1. login completion sends one preparation POST", after.length === before + 1, `${before} -> ${after.length}`);
  const p = after.at(-1);
  check("N1. POST carries UUID key, Origin and CSRF", !!p && UUID_RE.test(p.idempotencyKey ?? "") && p.origin === ORIGIN && !!p.csrf, JSON.stringify(p));
  sid = await createSession("준비 대화");
  await page.evaluate(() => document.querySelector('header a[href$="/posts/"]')?.click());
  await page.waitForURL(/\/posts\//);
  await openPanel();
  await closePanel();
  await setHidden(true);
  await setHidden(false);
  await sleep(4500);
  check("N1. navigation/panel/visibility/time send no POST", (await prepares()).length === before + 1, String((await prepares()).length));
  await page.reload();
  await settle();
  const reloaded = await prepares();
  check("N1. reload is a new entry (one more POST, new key)", reloaded.length === before + 2 && reloaded.at(-1).idempotencyKey !== p.idempotencyKey);
  await sleep(3500); // 준비 완료(readyMs)
});

// N2. 환경 활성화 중: 입력은 가능, 보내기만 잠금, 열린 대화창·보이는 탭에서만 다시 조회, 준비되면 자동 전송 없이 보내기 가능
await step("N2", async () => {
  await setMock("prepare=hold&runnerAll=");
  await page.goto(U(`/research/session/?id=${sid}`));
  await settle();
  await openPanel();
  check("N2. provisioning shown as 환경 활성화 중", await appears(chat.getByText("환경 활성화 중").first()));
  check("N2. progress indicator", (await chat.locator(".motion-safe\\:animate-spin").count()) > 0);
  await chat.getByLabel("메시지").fill("준비 중에 쓴 초안");
  check("N2. draft editable while preparing", (await chat.getByLabel("메시지").inputValue()) === "준비 중에 쓴 초안");
  check("N2. only send button locked", (await sendBtn().isDisabled()) && (await chat.getByLabel("메시지").isEnabled()));
  check("N2. composer notice: no auto send", await appears(chat.getByText(/환경 준비가 확인되면 보낼 수 있습니다/)));
  await shot("provisioning-1280");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  await shot("provisioning-390");
  await page.setViewportSize({ width: 1280, height: 800 });
  // 일시 상태 다시 조회: 2초 안내 → 6초 동안 2회 이상
  let r0 = await statusReads();
  await sleep(6000);
  let r1 = await statusReads();
  check("N2. polls provisioning while panel open (2s hint)", r1 - r0 >= 2 && r1 - r0 <= 5, `${r1 - r0}`);
  await closePanel();
  r0 = await statusReads();
  await sleep(5000);
  r1 = await statusReads();
  check("N2. no polling while panel closed", r1 === r0, `${r1 - r0}`);
  await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await chat.waitFor();
  await setHidden(true);
  await sleep(500);
  r0 = await statusReads();
  await sleep(5000);
  r1 = await statusReads();
  check("N2. no polling while tab hidden", r1 === r0, `${r1 - r0}`);
  await setHidden(false);
  await sleep(800);
  check("N2. one read when tab visible again", (await statusReads()) >= r1 + 1);
  const posts0 = await messagePosts();
  await setMock("runnerAll=ready");
  check("N2. becomes sendable when ready", await appears(chat.locator("button:not([disabled])", { hasText: /^보내기$/ }), 8000));
  check("N2. draft kept, nothing sent automatically", (await chat.getByLabel("메시지").inputValue()) === "준비 중에 쓴 초안" && (await messagePosts()) === posts0);
  check("N2. service mode shows no trial window fields", (await chat.getByText(/이용 가능 기한|남은 요청 횟수/).count()) === 0);
  await chat.getByLabel("메시지").fill("");
});

// N3. 사용 대기: 15초 안내 간격, 다른 제목, 전송 잠금
await step("N3", async () => {
  await setMock("runnerAll=capacity_wait");
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  check("N3. capacity wait shown distinct from preparing", await appears(chat.getByText("사용 대기 중").first()));
  check("N3. copy says server continues the recorded request", await appears(chat.getByText(/서버가 접수된 준비 요청을 이어서 처리합니다/)));
  await shot("capacity-wait");
  const r0 = await statusReads();
  await sleep(8000);
  const r1 = await statusReads();
  check("N3. capacity polling respects 15s hint", r1 - r0 <= 1, `${r1 - r0}`);
});

// N4. 쉬는 중(유휴 회수): 보내면 접수 전 거절(초안 유지·미확인 보관 없음), "환경 다시 켜기"는 새 key의 준비 요청, 자동 전송 없음
await step("N4", async () => {
  await setMock("runnerAll=ready&prepare=ok&readyMs=3000");
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  await chat.getByLabel("메시지").waitFor();
  await page.waitForTimeout(800);
  await setMock("runnerAll=idle_stopped");
  await chat.getByLabel("메시지").fill("쉬는 중에 보낸 질문");
  const posts0 = await messagePosts();
  const prep0 = await prepares();
  await sendBtn().click();
  check("N4. preflight refusal message", await appears(chat.getByText(/환경이 꺼져 있거나 정리 중이라 보내지 않았습니다/)));
  check("N4. definite refusal: draft kept, no pending send", (await chat.getByLabel("메시지").inputValue()) === "쉬는 중에 보낸 질문" && (await pendingSends()) === 0);
  check("N4. idle state + resume button", await appears(chat.getByText("환경이 쉬는 중").first()) && (await chat.getByRole("button", { name: "환경 다시 켜기" }).count()) > 0);
  await shot("idle-stopped-refused");
  await chat.getByRole("button", { name: "환경 다시 켜기" }).first().click();
  const prep1 = await prepares();
  check("N4. resume sends one new-key POST", prep1.length === prep0.length + 1 && !prep0.some((p) => p.idempotencyKey === prep1.at(-1).idempotencyKey));
  check("N4. resume shows 환경 활성화 중", await appears(chat.getByText("환경 활성화 중").first()));
  check("N4. ready again without auto send", await appears(chat.locator("button:not([disabled])", { hasText: /^보내기$/ }), 10000));
  check("N4. only the refused message was posted", (await messagePosts()) === posts0 + 1 && (await chat.getByLabel("메시지").inputValue()) === "쉬는 중에 보낸 질문");
  await chat.getByLabel("메시지").fill("");
});

// N5. 확인 필요(#16 runner_failed): 다시 시도 버튼 없음, 진입 준비는 409 뒤 상태 조회를 그대로 표시하고 반복하지 않음,
// 운영자 확인 뒤(쉬는 중) 다시 켜기. 준비 전 → 대화 준비, 모르는 reason은 일반 안내·잠금, 7개 접수 전 거절 문구
await step("N5", async () => {
  await setMock("runnerAll=runner_failed&prepare=ok&readyMs=1500");
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  check("N5. failed state: 확인이 필요합니다", await appears(chat.getByText("확인이 필요합니다").first()));
  check("N5. failed offers no prepare/retry/resume button", (await chat.getByRole("button", { name: /^(대화 준비|다시 시도|환경 다시 켜기)$/ }).count()) === 0);
  await shot("runner-failed");
  const prep0 = (await prepares()).length;
  const reads0 = await statusReads();
  await page.goto(U(`/research/session/?id=${sid}`));
  await openPanel();
  check("N5. entry during failed: one POST answered 409 runner_reconciliation_required", await appears(chat.getByText("확인이 필요합니다").first()) && (await prepares()).length === prep0 + 1);
  check("N5. 409 followed by a status read", (await statusReads()) > reads0);
  await sleep(4000);
  check("N5. no automatic retry after 409", (await prepares()).length === prep0 + 1, String((await prepares()).length - prep0));
  // 운영자 확인이 끝나면(쉬는 중) 화면을 다시 볼 때 다시 켜기를 고를 수 있다.
  await setMock("runnerAll=idle_stopped");
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  check("N5. after reconciliation: idle + resume button", await appears(chat.getByRole("button", { name: "환경 다시 켜기" })));
  await setMock("runnerAll=not_prepared&prepare=ok");
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  check("N5. not prepared + 대화 준비 button", await appears(chat.getByRole("button", { name: "대화 준비" })));
  await shot("not-prepared");
  await setMock("runnerAll=mystery_state");
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  check("N5. unknown reason -> generic copy", await appears(chat.getByText(/Codex를 사용할 수 없습니다/)));
  await chat.getByLabel("메시지").fill("모르는 상태 질문");
  check("N5. unknown reason locks send, input kept", (await sendBtn().isDisabled()) && (await chat.getByLabel("메시지").inputValue()) === "모르는 상태 질문");
  // 접수 전 거절 6개: 상태 표시는 사용 가능, 서버만 거절하는 경합(상태가 늦은 경우)
  for (const [st, text] of [
    ["not_prepared", /대화 환경이 아직 준비되지 않아 보내지 않았습니다/],
    ["provisioning", /환경 활성화 중이라 보내지 않았습니다/],
    ["capacity_wait", /사용 대기 중이라 보내지 않았습니다/],
    ["runner_unreachable", /환경 연결을 확인하는 중이라 보내지 않았습니다/],
    ["runner_failed", /환경 확인이 필요해 보내지 않았습니다/],
    ["stopping", /환경이 꺼져 있거나 정리 중이라 보내지 않았습니다/],
  ]) {
    await setMock("runnerAll=ready");
    await chat.getByRole("button", { name: "← 대화 목록" }).click();
    await chat.getByRole("link", { name: /준비 대화/ }).click();
    await chat.locator("button:not([disabled])", { hasText: /^보내기$/ }).waitFor({ timeout: 8000 });
    await setMock(`runnerAll=${st}`);
    await chat.getByLabel("메시지").fill(`${st} 경합 질문`);
    await sendBtn().click();
    check(`N5. ${st}: definite refusal, draft kept, no pending`, (await appears(chat.getByText(text))) && (await chat.getByLabel("메시지").inputValue()) === `${st} 경합 질문` && (await pendingSends()) === 0);
  }
  await chat.getByLabel("메시지").fill("");
});

// N6. 안내 간격: 범위 밖·문자열 안내는 기본 간격(바쁜 반복 없음), 연결 확인은 간격을 늘림
await step("N6", async () => {
  for (const hint of ["0", "120", "x"]) {
    await setMock(`runnerAll=provisioning&hint=${hint}`);
    await chat.getByRole("button", { name: "← 대화 목록" }).click();
    await chat.getByText("환경 활성화 중").first().waitFor();
    const r0 = await statusReads();
    await sleep(5000);
    const n = (await statusReads()) - r0;
    check(`N6. invalid hint ${hint}: bounded fallback polling`, n >= 1 && n <= 4, String(n));
    await chat.getByRole("link", { name: /준비 대화/ }).click();
  }
  await setMock("hint=&runnerAll=runner_unreachable");
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  check("N6. unreachable shown", await appears(chat.getByText("환경 연결 확인 중").first()));
  // 생성 결과 확인 중에도 쓰이는 상태: 짧게 끝난다·새 환경을 만든다·복구됐다고 안내하지 않고, 준비 버튼도 없다.
  {
    const text = await chat.innerText();
    check("N6. unreachable makes no recovery/new-environment claim", !/잠시|켜지면|새 환경|복구/.test(text), text.replace(/\s+/g, " ").slice(0, 160));
    check("N6. unreachable offers no prepare/retry button", (await chat.getByRole("button", { name: /^(대화 준비|다시 시도|환경 다시 켜기)$/ }).count()) === 0);
  }
  const r0 = await statusReads();
  await sleep(16000);
  const n = (await statusReads()) - r0;
  check("N6. unreachable backs off (5s, then longer)", n >= 1 && n <= 3, String(n));
});

// N7. 실행 중 환경 유실 확정 실패, 30초 이상 실행 중 중립 안내(재전송 없음)
await step("N7", async () => {
  await setMock("runnerAll=ready&codex=runner_lost");
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  // 빈 입력은 보내기가 잠기므로 먼저 쓴 뒤 사용 가능 상태를 기다린다.
  await chat.getByLabel("메시지").fill("유실될 질문");
  await chat.locator("button:not([disabled])", { hasText: /^보내기$/ }).waitFor({ timeout: 8000 });
  const posts0 = await messagePosts();
  await sendBtn().click();
  check("N7. runner lost failure copy", await appears(chat.getByText(/실행 중 환경이 중단되어 이 요청을 마치지 못했습니다/), 10000));
  check("N7. no automatic resend", (await messagePosts()) === posts0 + 1);
  await setMock("codex=fixture&turnMs=34000");
  await chat.getByLabel("메시지").fill("오래 걸리는 질문");
  await chat.locator("button:not([disabled])", { hasText: /^보내기$/ }).waitFor({ timeout: 8000 });
  await sendBtn().click();
  check("N7. running notice first", await appears(chat.getByText("Codex가 응답하는 중입니다.", { exact: false })));
  check("N7. neutral copy after 30s", await appears(chat.getByText(/응답을 기다리거나 실행 결과를 확인 중입니다/), 36000));
  check("N7. still one message post (no resend)", (await messagePosts()) === posts0 + 2);
  await chat.getByText("모의 응답: 오래 걸리는 질문").waitFor({ timeout: 15000 });
  check("N7. neutral copy cleared after completion", (await chat.getByText(/응답을 기다리거나 실행 결과를 확인 중입니다/).count()) === 0);
  await setMock("turnMs=2500");
});

await setMock("codex=fixture&turnMs=2500");

// N19. 정리 중(#16 stopping): 진행 표시·버튼 없음·5초 안내 간격 조회. 진입 준비는 거절되어 저장되지 않으며(자동 재요청 없음),
// 정리가 끝나 쉬는 중이 되면 조회로 바뀌고 다시 켜기는 사용자가 누른다.
await step("N19", async () => {
  await setMock("runnerAll=stopping&prepare=ok&hint=");
  const prep0 = (await prepares()).length;
  await page.goto(U(`/research/session/?id=${sid}`));
  await openPanel();
  check("N19. stopping shown as 환경 정리 중 with progress", (await appears(chat.getByText("환경 정리 중").first())) && (await chat.locator(".motion-safe\\:animate-spin").count()) > 0);
  check("N19. stopping offers no button", (await chat.getByRole("button", { name: /^(대화 준비|다시 시도|환경 다시 켜기)$/ }).count()) === 0);
  {
    const text = await chat.innerText();
    check("N19. stopping makes no automatic restart claim", !/자동으로 (다시 )?켜|곧 켜/.test(text), text.replace(/\s+/g, " ").slice(0, 160));
  }
  await shot("stopping");
  check("N19. entry POST refused once (503), not repeated", (await prepares()).length === prep0 + 1);
  const r0 = await statusReads();
  await sleep(11000);
  const n = (await statusReads()) - r0;
  check("N19. polls stopping with 5s hint", n >= 1 && n <= 3, String(n));
  check("N19. still one POST (rejected POST is not a saved intent)", (await prepares()).length === prep0 + 1);
  // 상태가 보이는 동안은 보내기만 잠긴다(서버 거절 codex_runner_stopped 문구는 N5 경합 케이스에서 확인).
  await chat.getByLabel("메시지").fill("정리 중에 쓴 초안");
  check("N19. stopping: only send locked, draft editable", (await sendBtn().isDisabled()) && (await chat.getByLabel("메시지").inputValue()) === "정리 중에 쓴 초안");
  await setMock("runnerAll=idle_stopped");
  check("N19. stop verified → idle + resume button via polling", await appears(chat.getByRole("button", { name: "환경 다시 켜기" }), 9000));
  check("N19. no POST until the user resumes", (await prepares()).length === prep0 + 1);
  await chat.getByLabel("메시지").fill("");
});

// N20. 운영자 토큰 오류(#16): 안내 간격이 있으면 오류 안내를 유지한 채 열린 대화창·보이는 탭에서만 읽기 전용 조회(준비 요청 없음),
// 운영자가 고치면 조회로 반영. 안내가 없는 이전 응답은 종료 상태(다시 조회 없음).
await step("N20", async () => {
  await setMock("runnerAll=auth_expired&credHint=1&hint=");
  await page.goto(U(`/research/session/?id=${sid}`));
  await openPanel();
  const prep0 = (await prepares()).length;
  check("N20. auth_expired: operator copy + periodic recheck note",
    (await appears(chat.getByText(/서버의 Codex 인증이 만료되어/).first())) && (await appears(chat.getByText(/상태를 주기적으로 다시 확인합니다/).first())));
  check("N20. credential error offers no button", (await chat.getByRole("button", { name: /^(대화 준비|다시 시도|환경 다시 켜기)$/ }).count()) === 0);
  await shot("auth-expired-polling");
  let r0 = await statusReads();
  await sleep(10000);
  check("N20. 30s hint: no read within 10s", (await statusReads()) === r0, String((await statusReads()) - r0));
  // 간격을 짧게 바꿔 다시 조회·늘림·숨김/닫힘 중단을 확인한다.
  await setMock("hint=3");
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  await chat.getByText(/서버의 Codex 인증이 만료되어/).first().waitFor();
  r0 = await statusReads();
  await sleep(10000);
  let n = (await statusReads()) - r0;
  check("N20. polls with hint, backing off (bounded)", n >= 1 && n <= 3, String(n));
  await setHidden(true);
  await sleep(500);
  r0 = await statusReads();
  await sleep(8000);
  check("N20. no polling while tab hidden", (await statusReads()) === r0);
  await setHidden(false);
  await sleep(500);
  await closePanel();
  await sleep(500);
  r0 = await statusReads();
  await sleep(8000);
  check("N20. no polling while panel closed", (await statusReads()) === r0);
  await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await chat.waitFor();
  check("N20. polling sends no prepare POST", (await prepares()).length === prep0);
  await setMock("runnerAll=ready");
  check("N20. operator repair reflected by polling", await appears(chat.getByText("Codex에 연결되어 있습니다", { exact: false }).or(chat.getByText(/테스트용 모의 Codex/)), 70000));
  // 안내 없는 이전 응답: 오류 안내만, 다시 조회 없음
  await setMock("runnerAll=auth_revoked&credHint=0&hint=");
  await page.goto(U(`/research/session/?id=${sid}`));
  await openPanel();
  check("N20. legacy auth_revoked without hint: terminal copy, no recheck note",
    (await appears(chat.getByText(/더 이상 유효하지 않습니다/).first())) && (await chat.getByText(/상태를 주기적으로 다시 확인합니다/).count()) === 0);
  r0 = await statusReads();
  await sleep(8000);
  check("N20. legacy without hint: no polling", (await statusReads()) === r0);
  await setMock("runnerAll=ready&credHint=1");
});

// N21. 리뷰(e525e5a) 반례: 상태 조회가 늦게 끝나는 동안 탭 표시 전환·대화창 다시 열기를 반복해도 같은 세대의 조회는 겹치지 않는다
// (동시 최대 1개). 진행 중에 다시 요청되면 끝난 뒤 한 번만 더 조회해 최신 상태를 반영한다.
await step("N21", async () => {
  await setMock("runnerAll=auth_expired&credHint=1&hint=");
  await page.goto(U(`/research/session/?id=${sid}`));
  await openPanel();
  await chat.getByText(/서버의 Codex 인증이 만료되어/).first().waitFor();
  let open = 0;
  let maxOpen = 0;
  let sent = 0;
  const isStatus = (r) => r.method() === "GET" && new URL(r.url()).pathname.endsWith("/research/codex/status");
  const onReq = (r) => {
    if (!isStatus(r)) return;
    sent += 1;
    open += 1;
    maxOpen = Math.max(maxOpen, open);
  };
  const onDone = (r) => {
    if (isStatus(r)) open -= 1;
  };
  page.on("request", onReq);
  page.on("requestfinished", onDone);
  page.on("requestfailed", onDone);
  await page.route("**/api/research/codex/status", async (route) => {
    await sleep(2500);
    await route.continue();
  });
  try {
    for (let i = 0; i < 3; i++) {
      await setHidden(true);
      await page.waitForTimeout(100);
      await setHidden(false);
      await page.waitForTimeout(250);
    }
    await closePanel();
    await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
    await chat.waitFor();
    // 진행 중에 바뀐 상태: 보류된 조회 뒤 한 번 더 조회해 반영해야 한다.
    await setMock("runnerAll=ready");
    await sleep(7000);
    check("N21. delayed status GETs never overlap (max 1 in flight)", maxOpen === 1, `max=${maxOpen} sent=${sent}`);
    check("N21. repeated requests coalesced into one trailing read", sent >= 2 && sent <= 3, String(sent));
    check("N21. trailing read reflects the newer state", await appears(chat.getByText(/테스트용 모의 Codex/).or(chat.getByText("Codex에 연결되어 있습니다", { exact: false })), 8000));
  } finally {
    await page.unroute("**/api/research/codex/status");
    page.off("request", onReq);
    page.off("requestfinished", onDone);
    page.off("requestfailed", onDone);
  }
});

// N8. 이전 API(준비 경로 없음·codexRunner 없음): 진입당 1회 404 뒤 기존 상태 조회, 405/403/500도 반복 없음
await step("N8", async () => {
  for (const [label, q] of [
    ["old API 404", "codexRunner=&runner=&prepare=ok"],
    ["405", "codexRunner=true&runner=not_prepared&prepare=405"],
    ["403", "codexRunner=true&runner=not_prepared&prepare=403"],
    ["500", "codexRunner=true&runner=not_prepared&prepare=500"],
  ]) {
    await setMock(q + "&runnerAll=");
    const p0 = (await prepares()).length;
    await page.reload();
    await settle();
    const m0 = await meReads();
    await sleep(4000);
    const n = (await prepares()).length - p0;
    check(`N8. ${label}: one attempt per entry, no repeat`, n === 1, String(n));
    check(`N8. ${label}: no me refresh loop`, (await meReads()) - m0 <= 1, String((await meReads()) - m0));
  }
  await setMock("codexRunner=&runner=&prepare=ok&runnerAll=");
  await page.reload();
  await openPanel();
  check("N8. old API: legacy status shown", await appears(chat.getByText("테스트용 모의 Codex에 연결되어 있습니다")));
});

// N8b. 403 진입 응답: me를 다시 확인하지만 같은 자격이면 다시 보내지 않는다(반복 없음)
await step("N8b", async () => {
  await setMock("codexRunner=true&runner=not_prepared&prepare=403&runnerAll=");
  const m0 = await meReads();
  const p0 = (await prepares()).length;
  await page.reload();
  await settle();
  await sleep(3000);
  const me1 = (await meReads()) - m0;
  check("N8b. 403 entry: me re-evaluated (load + one refresh), no loop", me1 >= 2 && me1 <= 3 && (await prepares()).length === p0 + 1, `me ${me1}, post ${(await prepares()).length - p0}`);
  await setMock("prepare=ok");
});

// N9. 새 API feature off(codexRunner:false, 기존 시험 모드): POST 없음, 대화창·시험 기한 표시 유지
await step("N9", async () => {
  await setMock("codexRunner=false&runner=&window=600");
  const p0 = (await prepares()).length;
  await page.reload();
  await openPanel();
  await sleep(2000);
  check("N9. present false: no POST", (await prepares()).length === p0);
  check("N9. legacy panel + trial expiry kept", await appears(chat.getByText(/이용 가능 기한/)));
});

// N10. 같은 계정 자격 false→true(새로고침 없음): 1회만, 같은 true 재조회는 반복 없음 / true→false: 준비 흐름의 늦은 응답 폐기 + 기존 상태 다시 조회
await step("N10", async () => {
  await setMock("codexRunner=true&runner=not_prepared&prepare=hold&runnerAll=&window=");
  const p0 = (await prepares()).length;
  await refreshMeInTab();
  await sleep(1500);
  check("N10. false->true sends exactly one POST", (await prepares()).length === p0 + 1, String((await prepares()).length - p0));
  await refreshMeInTab();
  await sleep(1500);
  check("N10. repeated true me refresh: no repeat", (await prepares()).length === p0 + 1);
  check("N10. preparing shown", await appears(chat.getByText("환경 활성화 중").first()));
  // true→false(feature off, 연구 권한 유지): 보류한 준비 응답은 버리고 기존 시험 상태를 다시 보인다
  let release;
  const gate = new Promise((r) => (release = r));
  await page.route("**/api/research/codex/runner", async (route) => {
    const res = await route.fetch();
    await gate;
    return route.fulfill({ response: res }).catch(() => {});
  });
  await setMock("runnerAll=");
  await page.reload();
  await settle();
  await setMock("codexRunner=false&runner=&window=600");
  await refreshMeInTab();
  release();
  await sleep(1500);
  await page.unroute("**/api/research/codex/runner");
  check("N10. true->false: late preparation result discarded", (await chat.getByText("환경 활성화 중").count()) === 0);
  check("N10. true->false: legacy trial status queried and shown", await appears(chat.getByText(/이용 가능 기한/)));
  await setMock("window=");
});

// N11. 계정 전환(같은 탭): A의 늦은 준비 응답은 B에 반영하지 않고, B의 진입 POST는 1회
await step("N11", async () => {
  await setMock("codexRunner=true&runner=not_prepared&prepare=ok&readyMs=60000&runnerAll=");
  const A = (await me()).accountId;
  // A는 이미 준비됨(200 ready)으로 만든 뒤, 새로고침 진입 POST의 응답을 붙잡는다
  await setMock("runnerAll=ready");
  let release;
  const gate = new Promise((r) => (release = r));
  let held = 0;
  await page.route("**/api/research/codex/runner", async (route) => {
    if (held++ === 0) {
      const res = await route.fetch();
      await gate;
      return route.fulfill({ response: res }).catch(() => {});
    }
    return route.continue();
  });
  // 대화 링크 페이지(/research/session/?id=)는 로그인한 계정에서 그 대화를 다시 열므로 일반 페이지에서 시작한다.
  await page.goto(U("/posts/"));
  await settle();
  const tab = await context.newPage();
  await tab.goto(U("/api/auth/github/start"));
  await tab.waitForURL(U("/"));
  await tab.close();
  const p0 = (await prepares()).length;
  await refreshMeInTab();
  await sleep(1500);
  const B = (await me()).accountId;
  check("N11. setup: tab switched A -> B", A !== B);
  release();
  await sleep(1500);
  await page.unroute("**/api/research/codex/runner");
  // 새 계정의 대화창 상태는 닫힘에서 시작한다.
  await openPanel();
  check("N11. B's entry POST once", (await prepares()).length === p0 + 1, String((await prepares()).length - p0));
  check(
    "N11. A's late ready response not applied to B (B preparing)",
    await appears(chat.getByText("환경 활성화 중").first()),
    (await chat.innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 160)
  );
  check("N11. B not shown as ready from A's response", (await chat.getByText("테스트용 모의 Codex에 연결되어 있습니다").count()) === 0);
  await setMock(`as=${A}`);
  await (async () => {
    const t = await context.newPage();
    await t.goto(U("/api/auth/github/start"));
    await t.waitForURL(U("/"));
    await t.close();
  })();
  await setMock("as=&readyMs=3000");
  await page.reload();
  await settle();
});

// N12. 두 탭: 각 탭의 진입이 1회씩(합침은 서버 책임)
await step("N12", async () => {
  // 직전 단계(새로고침 진입)의 늦은 POST가 섞이지 않게 탭별로 센다.
  const isPrepare = (r) => r.method() === "POST" && new URL(r.url()).pathname.endsWith("/research/codex/runner");
  const fromFirst = [];
  const fromSecond = [];
  const onFirst = (r) => isPrepare(r) && fromFirst.push(r.url());
  page.on("request", onFirst);
  const p0 = (await prepares()).length;
  const second = await track(await context.newPage());
  second.on("request", (r) => isPrepare(r) && fromSecond.push(r.url()));
  await second.goto(U("/posts/"));
  await second.waitForLoadState("networkidle");
  await second.waitForTimeout(800);
  page.off("request", onFirst);
  const delta = (await prepares()).length - p0;
  const detail = `second=${fromSecond.length} first=${fromFirst.length} server=${delta}`;
  check("N12. second tab entry sends one POST", fromSecond.length === 1, detail);
  check("N12. first tab sends no POST meanwhile", fromFirst.length === 0, detail);
  await second.close();
});

// N16. 리뷰(c6b3432) 반례: 같은 코드라도 정확한 503이 아니면(500/502) 결과 불명 — 원 key·body 보관, "보내지 않았습니다" 없음.
//      정확한 503은 확정 거절(N5). 결과 불명 뒤 같은 내용 다시 보내기는 같은 key.
await step("N16", async () => {
  await setMock("runnerAll=ready&codex=fixture&turnMs=2500");
  await chat.getByRole("button", { name: "← 대화 목록" }).click().catch(() => {});
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  for (const status of [500, 502]) {
    await chat.getByLabel("메시지").fill(`${status} 불명 질문`);
    await chat.locator("button:not([disabled])", { hasText: /^보내기$/ }).waitFor({ timeout: 8000 });
    const keys = [];
    await page.route("**/api/research/sessions/*/messages", (route) => {
      keys.push(route.request().headers()["idempotency-key"]);
      return route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ error: { code: "codex_provisioning", message: "x" } }) });
    });
    await sendBtn().click();
    check(`N16. ${status}+codex_provisioning: uncertain notice`, await appears(chat.getByText(/이전 전송의 결과를 확인하지 못했습니다/)));
    check(`N16. ${status}: no "not sent" confirmation`, (await chat.getByText(/보내지 않았습니다/).count()) === 0);
    check(`N16. ${status}: original request kept`, (await pendingSends()) === 1 && (await chat.getByLabel("메시지").inputValue()) === `${status} 불명 질문`);
    await page.unroute("**/api/research/sessions/*/messages");
    // 같은 내용 다시 보내기는 같은 key(실제 서버가 처리)
    await chat.getByRole("button", { name: "다시 보내기" }).click();
    await chat.getByText(`모의 응답: ${status} 불명 질문`).waitFor({ timeout: 15000 });
    const last = (await mockCalls()).filter((c) => c.method === "POST" && /\/messages$/.test(c.path)).at(-1);
    check(`N16. ${status}: retry reuses the original key, then settles`, last?.idempotencyKey === keys[0] && (await pendingSends()) === 0, `${last?.idempotencyKey} vs ${keys[0]}`);
    await chat.getByLabel("메시지").and(chat.locator(":enabled")).waitFor({ timeout: 15000 });
  }
});

// N17. 리뷰 반례: 상태 조회 확정 거절(403)은 보내기 잠금(초안·기록 유지), 일시 조회 실패(네트워크)는 마지막 상태 유지·잠그지 않음
await step("N17", async () => {
  await chat.getByLabel("메시지").fill("403 상태 질문");
  await chat.locator("button:not([disabled])", { hasText: /^보내기$/ }).waitFor({ timeout: 8000 });
  await page.route("**/api/research/codex/status", (route) => route.abort());
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  await page.waitForTimeout(1200);
  check("N17. transient status read failure does not lock send", !(await sendBtn().isDisabled()));
  await page.unroute("**/api/research/codex/status");
  await page.route("**/api/research/codex/status", (route) =>
    route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: { code: "forbidden", message: "x" } }) })
  );
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  check("N17. status 403: error shown", await appears(chat.getByText(/Codex 상태를 확인하지 못했습니다/)));
  check("N17. status 403: send locked, draft kept", (await sendBtn().isDisabled()) && (await chat.getByLabel("메시지").inputValue()) === "403 상태 질문");
  check("N17. status 403: history still visible", await appears(chat.getByText("모의 응답: 500 불명 질문")));
  await page.unroute("**/api/research/codex/status");
  await chat.getByLabel("메시지").fill("");
});

// N18. 리뷰 반례: 상속 속성 이름의 reason은 모르는 reason — 일반 안내·전송 잠금·자동 다시 조회 없음(바쁜 반복 없음)
await step("N18", async () => {
  for (const reason of ["__proto__", "constructor", "toString", "hasOwnProperty"]) {
    await setMock(`runnerAll=${reason}&hint=`);
    await chat.getByRole("button", { name: "← 대화 목록" }).click().catch(() => {});
    await chat.getByRole("link", { name: /준비 대화/ }).click();
    await chat.getByLabel("메시지").fill(`${reason} 질문`);
    check(`N18. ${reason}: generic copy`, await appears(chat.getByText(/Codex를 사용할 수 없습니다/)));
    check(`N18. ${reason}: send locked`, await sendBtn().isDisabled());
    const r0 = await statusReads();
    await sleep(5000);
    const n = (await statusReads()) - r0;
    check(`N18. ${reason}: no automatic polling`, n === 0, String(n));
  }
  await setMock("runnerAll=ready");
  await chat.getByLabel("메시지").fill("");
});

// N21. #89 대화 반응성(모의 API): 응답 지연 중 즉시 말풍선·생각 중, 정상 실행 중 경고 없음, 입력 잠금 깜빡임 없음,
// 접수 항목 → 기록 항목(다른 id)으로 바뀌어도 같은 말풍선(remount 없음), 자동 스크롤·"최신으로", 이용 가능 상태 재조회.
await step("N21", async () => {
  await setMock("runnerAll=ready&codex=fixture&turnMs=2500&hint=&pendingInput=1");
  await chat.getByRole("button", { name: "← 대화 목록" }).click().catch(() => {});
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  const input = chat.getByLabel("메시지");
  const scroller = chat.locator("[data-chat-scroll]");
  const gap = () => scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
  // 보내기 요청 응답을 1.5초 늦춘다(네트워크 대기 없이 말풍선이 보이는지).
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    await sleep(1500);
    return route.continue();
  });
  const text = "반응성 질문 하나";
  await input.fill(text);
  await chat.locator("button:not([disabled])", { hasText: /^보내기$/ }).waitFor({ timeout: 8000 });
  // 입력창 잠금 변화 기록(보내기 → 완료 사이 한 번 잠기고 한 번 풀려야 한다)
  await input.evaluate((ta) => {
    window.__taLog = [];
    new MutationObserver(() => window.__taLog.push(ta.disabled)).observe(ta, { attributes: true, attributeFilter: ["disabled"] });
  });
  const reads0 = await statusReads();
  const t0 = Date.now();
  await sendBtn().click();
  const bubble = chat.locator("li[data-role=user]", { hasText: text });
  const early = (await appears(bubble, 1200)) && (await appears(chat.locator("[data-chat-thinking]"), 200));
  check("N21. own bubble + thinking before POST response", early && Date.now() - t0 < 1500, `${Date.now() - t0}ms`);
  check("N21. input moved to bubble immediately", (await input.inputValue()) === "");
  await bubble.first().evaluate((el) => {
    window.__bubble = el;
  });
  // 서버 실행 중(접수 항목)
  await chat.locator("li[data-role=user][data-sending]").waitFor({ state: "detached", timeout: 8000 });
  check("N21. running: bubble is not a warning", (await bubble.first().getAttribute("class"))?.includes("border-dashed") === false);
  check("N21. running: no unknown-result notice", (await chat.getByText(/전달 확인 중|이전 전송의 결과를 확인하지 못했습니다/).count()) === 0);
  check("N21. running: same bubble element as before POST", await page.evaluate(() => window.__bubble.isConnected));
  // 보내자마자 위로 올려 읽는 중이면 완료 내용을 따라가지 않는다.
  await page.setViewportSize({ width: 1280, height: 420 });
  await page.waitForTimeout(200);
  await scroller.evaluate((el) => (el.scrollTop = 0));
  await chat.getByText(`모의 응답: ${text}`).waitFor({ timeout: 15000 });
  check("N21. completed: same bubble element (pending -> recorded id, no remount)", await page.evaluate(() => window.__bubble.isConnected));
  check("N21. completed: one user bubble", (await bubble.count()) === 1);
  const log = await page.evaluate(() => window.__taLog);
  check("N21. input locked once, unlocked once (no flicker)", JSON.stringify(log) === "[true,false]", JSON.stringify(log));
  check("N21. status re-read after send", (await statusReads()) > reads0);
  await page.unroute("**/api/research/sessions/*/messages");
  check("N21. scrolled up: not followed", (await scroller.evaluate((el) => el.scrollTop)) < 10);
  const jump = chat.getByRole("button", { name: "최신으로" });
  check("N21. scrolled up: jump button shown", await appears(jump, 2000));
  await jump.click();
  check("N21. jump goes to bottom", (await gap()) <= 48, String(await gap()));
  check("N21. jump button hidden at bottom", (await jump.count()) === 0);
  // 바닥에 있으면 새 내용을 따라간다.
  await input.fill("반응성 질문 둘");
  await sendBtn().click();
  await chat.getByText("모의 응답: 반응성 질문 둘").waitFor({ timeout: 15000 });
  await page.waitForTimeout(300);
  check("N21. at bottom: follows new content", (await gap()) <= 48, String(await gap()));
  // 대화를 다시 열면 마지막 메시지가 보인다.
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  await chat.getByRole("link", { name: /준비 대화/ }).click();
  await chat.getByText("모의 응답: 반응성 질문 둘").waitFor();
  await page.waitForTimeout(300);
  check("N21. reopened conversation shows the last message", (await gap()) <= 48, String(await gap()));
  await shot("responsiveness-420");
  await page.setViewportSize({ width: 1280, height: 800 });
  // 이용 가능 상태에서도: 네트워크 복귀 때 조회, 대화창이 열린 동안 느린 주기(45초 ±15%)로 조회해 idle 정지를 알아챈다.
  await setMock("runnerAll=idle_stopped");
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  check("N21. online event re-reads status", await appears(chat.getByText("환경이 쉬는 중"), 3000));
  await setMock("runnerAll=ready");
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await chat.getByText("환경이 쉬는 중").first().waitFor({ state: "hidden", timeout: 3000 });
  await setMock("runnerAll=idle_stopped");
  const t1 = Date.now();
  check("N21. idle stop noticed by slow recheck while panel open", await appears(chat.getByText("환경이 쉬는 중"), 60000), `${Math.round((Date.now() - t1) / 1000)}s`);
  await setMock("runnerAll=ready&pendingInput=0");
});

// N22. #89 Enter 보내기: Enter = 보내기(한 번), Shift+Enter = 줄바꿈, IME 조합 중 Enter는 보내지 않음(확정 뒤 Enter가 전체 문구를 한 번 보냄).
await step("N22", async () => {
  await setMock("runnerAll=ready&codex=fixture&turnMs=1500");
  // N21 끝의 쉬는 중 표시를 지금 상태(ready)로 갱신한다.
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await chat.getByText("환경이 쉬는 중").first().waitFor({ state: "hidden", timeout: 5000 });
  const input = chat.getByLabel("메시지");
  const sentTexts = [];
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    sentTexts.push(route.request().postDataJSON()?.text);
    return route.continue();
  });
  check("N22. desktop browser treats Enter as send", await page.evaluate(() => window.matchMedia("(any-pointer: fine)").matches));
  // Enter = 보내기
  await input.fill("엔터 질문");
  await input.press("Enter");
  await chat.getByText("모의 응답: 엔터 질문").waitFor({ timeout: 10000 });
  check("N22. Enter sends once", JSON.stringify(sentTexts) === JSON.stringify(["엔터 질문"]), JSON.stringify(sentTexts));
  await input.and(chat.locator(":enabled")).waitFor({ timeout: 10000 });
  // Shift+Enter = 줄바꿈
  await input.fill("첫 줄");
  await input.press("Shift+Enter");
  await input.pressSequentially("둘째 줄");
  await page.waitForTimeout(300);
  check("N22. Shift+Enter inserts a newline", (await input.inputValue()) === "첫 줄\n둘째 줄", JSON.stringify(await input.inputValue()));
  check("N22. Shift+Enter does not send", sentTexts.length === 1, JSON.stringify(sentTexts));
  // IME 조합 중 Enter: 조합 확정용 keydown(isComposing, Safari식 keyCode 229)은 보내지 않는다.
  await input.fill("조합 중 질문");
  await input.evaluate((ta) => {
    ta.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true, data: "" }));
    ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, bubbles: true, cancelable: true }));
    ta.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "문" }));
    const safari = new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true });
    Object.defineProperty(safari, "keyCode", { get: () => 229 });
    ta.dispatchEvent(safari);
  });
  await page.waitForTimeout(800);
  check("N22. Enter during IME composition does not send", sentTexts.length === 1, JSON.stringify(sentTexts));
  check("N22. composition Enter keeps the text", (await input.inputValue()) === "조합 중 질문", JSON.stringify(await input.inputValue()));
  // 확정 뒤 Enter(연타 포함)는 전체 문구를 한 번만 보낸다.
  await input.evaluate((ta) => {
    for (let i = 0; i < 2; i++) ta.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
  });
  await chat.getByText("모의 응답: 조합 중 질문").waitFor({ timeout: 10000 });
  check("N22. Enter after composition sends full text exactly once", JSON.stringify(sentTexts.slice(1)) === JSON.stringify(["조합 중 질문"]), JSON.stringify(sentTexts));
  await page.unroute("**/api/research/sessions/*/messages");
  await setMock("turnMs=2500");
});

// N23. #89 review: 접수(202) 뒤 첫 대화 조회가 일시 실패(503)해도 보내는 중을 유지하고 조회만 다시 해 실행 중 → 완료 기록으로 복구한다.
await step("N23", async () => {
  await setMock("runnerAll=ready&codex=fixture&turnMs=4000");
  const input = chat.getByLabel("메시지");
  await input.and(chat.locator(":enabled")).waitFor({ timeout: 10000 });
  const posts = [];
  let failNextGet = false;
  const gets = [];
  await page.route("**/api/research/sessions/*/messages", async (route) => {
    posts.push(route.request().method());
    const r = await route.fetch();
    failNextGet = true;
    return route.fulfill({ response: r });
  });
  await page.route("**/api/research/sessions/*", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    if (failNextGet) {
      failNextGet = false;
      gets.push(503);
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "unavailable", message: "Unavailable" } }) });
    }
    gets.push(200);
    return route.continue();
  });
  await input.evaluate((ta) => {
    window.__taLog = [];
    new MutationObserver(() => window.__taLog.push(ta.disabled)).observe(ta, { attributes: true, attributeFilter: ["disabled"] });
  });
  const text = "조회 실패 뒤 복구 질문";
  await input.fill(text);
  await sendBtn().click();
  const bubble = chat.locator("li[data-role=user]", { hasText: text });
  await bubble.first().waitFor();
  await bubble.first().evaluate((el) => (window.__bubble = el));
  // 첫 GET 503 직후: 말풍선·생각 중 유지, 입력 잠금 유지
  for (let i = 0; i < 40 && !gets.includes(503); i++) await sleep(50);
  await page.waitForTimeout(400);
  check("N23. first GET after 202 failed (503)", gets[0] === 503, JSON.stringify(gets));
  check("N23. bubble kept after failed GET", await page.evaluate(() => window.__bubble.isConnected));
  check("N23. thinking kept after failed GET", await chat.locator("[data-chat-thinking]").isVisible());
  check("N23. input stays locked after failed GET", await input.isDisabled());
  await chat.getByText(`모의 응답: ${text}`).waitFor({ timeout: 20000 });
  check("N23. recovered to the final record", (await bubble.count()) === 1 && (await page.evaluate(() => window.__bubble.isConnected)));
  check("N23. GET retried, POST not resent", posts.length === 1 && gets.filter((g) => g === 200).length >= 1, `${posts.length} ${JSON.stringify(gets)}`);
  const log = await page.evaluate(() => window.__taLog);
  check("N23. input locked once, unlocked once", JSON.stringify(log) === "[true,false]", JSON.stringify(log));
  await page.unroute("**/api/research/sessions/*/messages");
  await page.unroute("**/api/research/sessions/*");
  await setMock("turnMs=2500");
});

// N24. #89 review: 바닥에서 기다리면 "생각 중"이 같은 수의 긴 답변 하나로 바뀌는 완료도 바닥을 따라간다.
// 완료 뒤 사용자가 도구 상세를 펼친 것은 따라가지 않는다(읽던 위치 유지).
await step("N24", async () => {
  await setMock("runnerAll=ready&codex=fixture&turnMs=3000");
  const input = chat.getByLabel("메시지");
  const scroller = chat.locator("[data-chat-scroll]");
  const gap = () => scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
  await page.setViewportSize({ width: 1280, height: 420 });
  await input.and(chat.locator(":enabled")).waitFor({ timeout: 10000 });
  const toolsBefore = await chat.locator("li", { hasText: "research_list" }).count();
  await input.fill("/fixture/long-answer 긴 답변 질문");
  await input.press("Enter");
  await chat.locator("[data-chat-thinking]").waitFor();
  await page.waitForTimeout(500);
  check("N24. waiting at bottom", (await gap()) <= 48, String(await gap()));
  await chat.getByText("긴 답변 문단 80.").waitFor({ timeout: 15000 });
  await page.waitForTimeout(500);
  check("N24. no tool item in this turn (same entry count)", (await chat.locator("li", { hasText: "research_list" }).count()) === toolsBefore);
  check("N24. long answer followed to the bottom", (await gap()) <= 48, String(await gap()));
  // 도구가 있는 답변 뒤, 바닥에서 도구 상세를 직접 펼치면 위치를 유지한다.
  await input.and(chat.locator(":enabled")).waitFor({ timeout: 10000 });
  await input.fill("펼침 위치 질문");
  await input.press("Enter");
  await chat.getByText("모의 응답: 펼침 위치 질문").waitFor({ timeout: 15000 });
  await page.waitForTimeout(1500);
  const tool = chat.getByRole("button", { name: /research_list/ }).last();
  // 머리글 위쪽이 화면 위로 잘린 경우(브라우저 scroll anchoring이 아래 답변을 기준점으로 잡는 반례)도 덮도록 조금 올려 둔다.
  await tool.evaluate((el) => {
    const s = el.closest("[data-chat-scroll]");
    s.scrollTop += el.getBoundingClientRect().top - s.getBoundingClientRect().top + 12;
  });
  await page.waitForTimeout(300);
  const before = await tool.evaluate((el) => ({ scroll: el.closest("[data-chat-scroll]").scrollTop, head: Math.round(el.getBoundingClientRect().top) }));
  await tool.evaluate((el) => el.click()); // 스크롤 없이 누른다
  await chat.getByRole("button", { name: "저장된 원본 기록 보기" }).last().waitFor();
  await page.waitForTimeout(500);
  const after = await tool.evaluate((el) => ({ scroll: el.closest("[data-chat-scroll]").scrollTop, head: Math.round(el.getBoundingClientRect().top) }));
  check("N24. manual expand keeps scrollTop", after.scroll === before.scroll, `${before.scroll} -> ${after.scroll}`);
  check("N24. manual expand keeps the clicked header in place", after.head === before.head, `${before.head} -> ${after.head}`);
  // 바닥에 붙어 있던 경우(위쪽이 잘리지 않음)도 같은 위치를 유지한다.
  await chat.getByRole("button", { name: "최신으로" }).click();
  await tool.evaluate((el) => el.click()); // 접기
  await page.waitForTimeout(1200);
  const top0 = await scroller.evaluate((el) => el.scrollTop);
  await tool.evaluate((el) => el.click());
  await page.waitForTimeout(500);
  const top1 = await scroller.evaluate((el) => el.scrollTop);
  check("N24. manual expand at bottom keeps reading position", top1 === top0, `${top0} -> ${top1}`);
  await page.setViewportSize({ width: 1280, height: 800 });
  await setMock("turnMs=2500");
});

// N25. #90 스트림(모의 API, 실제 runner 스트림이 아님): 연결 중 polling 멈춤·답변 조각 실시간 표시·완료 뒤 같은 DOM(remount 없음)·
// 중지(202 requestId, 부분 답변 표시 전용)·알림/모르는 항목 표시·숨김 30초 닫기와 after 재연결·플래그 꺼짐이면 polling.
await step("N25", async () => {
  const counts = () => fetch(`${ORIGIN}/__mock/counts`).then((r) => r.json());
  const sessionGets = async () => (await mockCalls()).filter((c) => c.method === "GET" && /^\/research\/sessions\/[^/]+$/.test(c.path)).length;
  const waitFor = async (fn, ms) => {
    for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) if (await fn()) return true;
    return false;
  };
  const input = chat.getByLabel("메시지");
  const openSession = async () => {
    await openPanel();
    await chat.getByRole("button", { name: "← 대화 목록" }).click().catch(() => {});
    await chat.getByRole("link", { name: /준비 대화/ }).click();
    await input.and(chat.locator(":enabled")).waitFor({ timeout: 10000 });
  };
  await setMock("runnerAll=ready&codex=fixture&turnMs=6000&stream=on");
  await page.reload();
  await openSession();
  check("N25. stream connected", await waitFor(async () => (await counts()).streamOpen >= 1, 5000));

  // 받는 중 표시와 polling 멈춤
  const g0 = await sessionGets();
  const r0 = await statusReads();
  await input.fill("스트림 질문 하나");
  const t0 = Date.now();
  await input.press("Enter");
  const answer = chat.locator("li[data-role=assistant]", { hasText: "모의 응답" }).last();
  check("N25. answer streams before the turn ends", (await appears(answer, 4500)) && Date.now() - t0 < 5500, `${Date.now() - t0}ms`);
  await answer.evaluate((el) => (window.__ans = el));
  check("N25. stop button while answering", await chat.getByRole("button", { name: "중지" }).isVisible());
  await input.and(chat.locator(":enabled")).waitFor({ timeout: 15000 });
  check("N25. same answer element after the record replaces the stream", await page.evaluate(() => window.__ans.isConnected));
  check("N25. one recorded answer", (await chat.locator("li[data-role=assistant]", { hasText: "모의 응답: 스트림 질문 하나" }).count()) === 1);
  const gets = (await sessionGets()) - g0;
  check("N25. no 2s session polling while streaming (confirm + completion only)", gets <= 3, String(gets));
  check("N25. no status polling while streaming", (await statusReads()) === r0, String((await statusReads()) - r0));
  check("N25. no stop button after completion", (await chat.getByRole("button", { name: "중지" }).count()) === 0);

  // review(1908aaf): 완료 확정 GET이 일시 실패(503)해도 스트림 연결 중 GET만 다시 해 완료 기록으로 복구하고 입력 잠금을 푼다.
  {
    const gets = [];
    let failNext = false;
    await page.route("**/api/research/sessions/*", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      if (failNext) {
        failNext = false;
        gets.push(503);
        return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "unavailable", message: "Unavailable" } }) });
      }
      gets.push(200);
      return route.continue();
    });
    const posts0 = (await mockCalls()).filter((c) => c.method === "POST" && /\/messages$/.test(c.path)).length;
    await input.fill("완료 조회 실패 질문");
    await input.press("Enter");
    await appears(chat.locator("li[data-role=assistant]", { hasText: "모의 응답" }).last(), 5000);
    await page.waitForTimeout(800);
    gets.length = 0;
    failNext = true; // 다음 GET = 완료 확정 GET
    check("N25. completion GET 503 -> retried, input unlocked", await appears(input.and(chat.locator(":enabled")), 15000), JSON.stringify(gets));
    check("N25. completion GET failed once then recovered", gets[0] === 503 && gets.includes(200), JSON.stringify(gets));
    check("N25. recovered record shows the answer", await appears(chat.getByText("모의 응답: 완료 조회 실패 질문")));
    check(
      "N25. message not resent",
      (await mockCalls()).filter((c) => c.method === "POST" && /\/messages$/.test(c.path)).length === posts0 + 1
    );
    await page.unroute("**/api/research/sessions/*");
  }

  // 중지: 부분 답변은 표시 전용, 대화는 idle(실패 아님)
  const i0 = (await counts()).interrupts;
  await input.fill("중지할 질문");
  await input.press("Enter");
  await appears(chat.locator("li[data-role=assistant]", { hasText: "모의 응답: 중" }), 5000);
  await chat.getByRole("button", { name: "중지" }).click();
  check("N25. one interrupt request", await waitFor(async () => (await counts()).interrupts === i0 + 1, 3000));
  check("N25. stopped partial answer marked", await appears(chat.getByText("중지됨(부분)"), 8000));
  check("N25. partial answer is display-only", await appears(chat.getByText(/이어서 질문해도 Codex는 이 내용을 보지 않습니다/)));
  check("N25. stop is not a failure", (await chat.getByText("마지막 요청이 실패했습니다").count()) === 0);
  check("N25. input unlocked after stop", await appears(input.and(chat.locator(":enabled")), 5000));

  // review(62e0406): A 턴 중지 → A 종료로 입력 해제 → B 턴 전송 → A 중지 응답이 늦게 도착해도 B의 중지 버튼은 잠기지 않는다.
  {
    await page.route("**/api/research/sessions/*/interrupt", async (route) => {
      const r = await route.fetch(); // 서버(모의)는 바로 중지하고 기록을 남긴다
      await sleep(3000); // 응답 전달만 늦춘다
      return route.fulfill({ response: r }).catch(() => {});
    });
    await input.fill("늦은 중지 A");
    await input.press("Enter");
    await appears(chat.locator("li[data-role=assistant]", { hasText: "늦은" }), 5000);
    await chat.getByRole("button", { name: "중지" }).click();
    await input.and(chat.locator(":enabled")).waitFor({ timeout: 8000 });
    await input.fill("다음 턴 B");
    await input.press("Enter");
    await chat.getByRole("button", { name: /^중지/ }).waitFor({ timeout: 5000 });
    await page.waitForTimeout(3500); // A의 늦은 응답이 도착한 뒤(B 턴 6초 안)
    const b = chat.getByRole("button", { name: /^중지/ });
    check("N25. late stop reply of turn A does not lock turn B's stop button", (await b.textContent())?.trim() === "중지" && (await b.isEnabled()), `${await b.textContent()} enabled=${await b.isEnabled()}`);
    await page.unroute("**/api/research/sessions/*/interrupt");
    await input.and(chat.locator(":enabled")).waitFor({ timeout: 15000 });
  }

  // 서버 알림·모르는 항목: 대화 전체는 그대로 보인다
  await setMock("turnMs=1500");
  await input.fill("/fixture/notice 알림 질문");
  await input.press("Enter");
  check("N25. partial_removed notice shown", await appears(chat.getByText(/보존 한도를 넘어 삭제되었습니다/), 10000));
  check("N25. unknown item type shown without breaking the conversation", await appears(chat.getByText(/표시하지 못하는 기록 항목입니다 \(future_kind\)/)));

  // 숨김 30초 → 닫기, 다시 보이면 cursor(after)로 이어 붙기
  const a0 = (await counts()).streamAfter;
  await setHidden(true);
  check("N25. stream closed after 30s hidden", await waitFor(async () => (await counts()).streamOpen === 0, 36000));
  await setHidden(false);
  check("N25. reconnects with after= when visible", await waitFor(async () => (await counts()).streamOpen >= 1 && (await counts()).streamAfter > a0, 8000));

  // 플래그 꺼짐(404 stream_disabled): 기존 polling으로 동작하고 중지 버튼은 없다
  await setMock("stream=off&turnMs=3000");
  await page.reload();
  await openSession();
  await input.fill("폴백 질문");
  await input.press("Enter");
  await chat.locator("[data-chat-thinking]").waitFor();
  await page.waitForTimeout(1200);
  check("N25. stream off: no stop button", (await chat.getByRole("button", { name: "중지" }).count()) === 0);
  check("N25. stream off: reply via polling", await appears(chat.getByText("모의 응답: 폴백 질문"), 10000));
  await setMock("turnMs=2500");
});

// N13. 연구 권한 상실(같은 계정 commenter로 변경): 준비 POST·상태 조회 없음, 기존 권한 안내
await step("N13", async () => {
  const acc = (await me()).accountId;
  await setMock(`demote=${acc}&codexRunner=`);
  const p0 = (await prepares()).length;
  await page.reload();
  await settle();
  const r0 = await statusReads();
  await openPanel();
  await sleep(3000);
  check("N13. role loss: no preparation POST", (await prepares()).length === p0);
  check("N13. role loss: no status reads", (await statusReads()) === r0);
  check("N13. role loss: existing permission notice", await appears(chat.getByText(/승인한 편집자·관리자만 이용할 수 있습니다/)));
});

// N14. 비로그인: POST 없음
await step("N14", async () => {
  const anon = await browser.newContext();
  const ap = await track(await anon.newPage());
  const p0 = (await prepares()).length;
  await ap.goto(U("/posts/"));
  await ap.waitForLoadState("networkidle");
  await ap.waitForTimeout(800);
  check("N14. logged-out visit sends no POST", (await prepares()).length === p0);
  await anon.close();
});

check("N15. no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
await setMock("codexRunner=&runner=&prepare=ok&runnerAll=&hint=&credHint=1&readyMs=2500&turnMs=2500&codex=fixture&csrf=normal&window=&remaining=&pendingInput=0&stream=off");
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
