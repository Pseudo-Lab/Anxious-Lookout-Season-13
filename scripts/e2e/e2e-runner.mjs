// #10 사용자별 실행 환경 화면 검증(모의 API, api-contracts/issue10-user-runners-interface-v1.md).
// 실제 controller·Pod 생성·중복 방지·격리 검증이 아니다(모의 서버의 계정별 합침은 backend 증거가 아님).
// - 진입 준비 POST: 탭 진입·로그인·새로고침·계정 전환·자격 false→true 때만 1회, 이동·대화창·조회·표시 변화·시간 경과로는 없음
// - 상태: 환경 활성화 중/사용 대기/연결 확인/쉬는 중/준비 전/실패, 일시 상태만 열린 대화창·보이는 탭에서 다시 조회
// - 전송 버튼만 잠금, 초안 입력 가능·유지, 자동 전송 없음, 접수 전 거절 6개는 미확인 보관 없음
// - 이전 API(404/405)·403·500·codexRunner false·자격 전환·계정 전환의 늦은 응답·연구 권한 상실
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
  "me=normal&health=normal&logout=normal&nextLogin=approved&reuse=0&as=&access=approved&publish=allowed&codex=fixture&csrf=normal&window=&remaining=&turnMs=2500" +
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
  check("N4. preflight refusal message", await appears(chat.getByText(/환경이 쉬는 중이라 보내지 않았습니다/)));
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

// N5. 준비 실패 → 다시 시도(새 key), 준비 전 → 대화 준비, 모르는 reason은 일반 안내·잠금, 6개 접수 전 거절 문구
await step("N5", async () => {
  await setMock("runnerAll=runner_failed&prepare=fail&readyMs=1500");
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  check("N5. failed state + retry button", await appears(chat.getByText("환경을 준비하지 못했습니다").first()) && (await chat.getByRole("button", { name: "다시 시도" }).count()) > 0);
  await shot("runner-failed");
  const prep0 = (await prepares()).length;
  await chat.getByRole("button", { name: "다시 시도" }).first().click();
  check("N5. retry -> 환경 활성화 중", await appears(chat.getByText("환경 활성화 중").first()));
  check("N5. fails again -> retry offered (no automatic retry)", await appears(chat.getByRole("button", { name: "다시 시도" }), 8000));
  await sleep(3000);
  check("N5. exactly one POST for the retry", (await prepares()).length === prep0 + 1, String((await prepares()).length - prep0));
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
    ["runner_failed", /환경을 준비하지 못해 보내지 않았습니다/],
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
await setMock("codexRunner=&runner=&prepare=ok&runnerAll=&hint=&readyMs=2500&turnMs=2500&codex=fixture&csrf=normal&window=&remaining=");
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
