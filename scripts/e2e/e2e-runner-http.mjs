// #10 실제 FastAPI HTTP fixture(infra/hosting/m3/USER-RUNNER-HTTP-FIXTURE.md)에서 실행 환경 준비/상태/권한/선접수를 브라우저로 확인한다.
// UI 모의 제어(__mock) 미사용. phase는 호스트(live-runner-http.sh)가 test-image 전용 runner-phase로 정한 합성 값이다.
// 실제 Pod/controller/provider/모델·available:true·정상 전송은 범위 밖이다(이 source 단위에서 READY도 runner_unreachable).
// 결과 이름의 [real]은 실제 API 응답, [hybrid]는 실제 응답에 Playwright가 지연을 더한 것이다.
// 조율: <name>.id/<name>.role → 승인 → <name>.ok, 승인 후 <name>.account / 요청 req-<n>.cmd → req-<n>.done.
import { chromium } from "playwright";
import fs from "node:fs/promises";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:18110";
const SHARED = process.env.SHARED ?? "/shared";
const SHOTS = process.env.SHOTS_DIR ?? "";
const U = (p) => `${ORIGIN}${p}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
      await sleep(300);
    }
  }
}
// 호스트 요청(phase·역할·flag). 원자적으로 쓰고 처리 결과를 기다린다.
let reqN = 0;
async function host(cmd) {
  reqN += 1;
  await fs.writeFile(`${SHARED}/req-${reqN}.tmp`, `${cmd}\n`);
  await fs.rename(`${SHARED}/req-${reqN}.tmp`, `${SHARED}/req-${reqN}.cmd`);
  await waitFile(`req-${reqN}.done`, 240_000);
  const [status, out = ""] = (await fs.readFile(`${SHARED}/req-${reqN}.done`, "utf8")).split("\n");
  if (status !== "ok") throw new Error(`host refused: ${cmd}`);
  return out.trim();
}
// 읽기 전용 registry 스냅샷(phase, generation, podBound, creationReserved, entryReceipts, pendingEnsure, lastActivity)
const inspect = async (accountId) => JSON.parse(await host(`inspect ${accountId}`));

const browser = await chromium.launch();
const consoleErrors = [];
const isRunnerPost = (r) => r.method() === "POST" && new URL(r.url()).pathname.endsWith("/research/codex/runner");
const isStatusGet = (r) => r.method() === "GET" && new URL(r.url()).pathname.endsWith("/research/codex/status");

// 계정마다 별도 브라우저 context. 브라우저가 보낸 준비 POST·상태 GET과 준비 응답을 기록한다(page.request API 호출은 제외됨).
async function newUser() {
  const context = await browser.newContext();
  const net = { posts: [], responses: [], gets: 0 };
  context.on("request", (r) => {
    if (isRunnerPost(r)) net.posts.push(r.headers()["idempotency-key"]);
    if (isStatusGet(r)) net.gets += 1;
  });
  context.on("response", async (r) => {
    if (!isRunnerPost(r.request())) return;
    net.responses.push({ status: r.status(), body: await r.json().catch(() => null) });
  });
  const page = await context.newPage();
  await prepareTab(page);
  return { context, page, net, chat: page.locator("#chat-panel") };
}
async function prepareTab(p) {
  p.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text());
  });
  p.on("dialog", (d) => void d.accept());
  await p.addInitScript(() => {
    let hidden = false;
    Object.defineProperty(document, "visibilityState", { get: () => (hidden ? "hidden" : "visible"), configurable: true });
    Object.defineProperty(document, "hidden", { get: () => hidden, configurable: true });
    window.__setHidden = (h) => {
      hidden = h;
      document.dispatchEvent(new Event("visibilitychange"));
    };
  });
}
async function settle(p) {
  await p.waitForLoadState("networkidle").catch(() => {});
  await p.waitForTimeout(800);
}
async function signIn(p) {
  await p.goto(U("/"));
  await p.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().click();
  await p.waitForURL(U("/"));
  await p.locator("header").getByRole("button", { name: "로그아웃" }).first().waitFor();
}
const meOf = async (p) => (await (await p.request.get(U("/api/auth/me"))).json()).user;
async function approvedAccount(name, p, role) {
  await signIn(p);
  const me = await meOf(p);
  await fs.writeFile(`${SHARED}/${name}.role`, role);
  await fs.writeFile(`${SHARED}/${name}.id`, me.githubId);
  await waitFile(`${name}.ok`);
  await signIn(p); // 승인·역할 변경은 기존 session을 지운다
  const after = await meOf(p);
  await fs.writeFile(`${SHARED}/${name}.account`, after.accountId);
  return after;
}
function apiClient(p) {
  const csrf = async () => (await (await p.request.get(U("/api/auth/me"))).json()).csrfToken;
  return {
    async get(path) {
      const r = await p.request.get(U(`/api${path}`));
      return { status: r.status(), body: await r.json().catch(() => null) };
    },
    async send(method, path, data) {
      const r = await p.request.fetch(U(`/api${path}`), {
        method,
        data,
        headers: { Origin: ORIGIN, "X-CSRF-Token": await csrf(), "Idempotency-Key": crypto.randomUUID() },
      });
      return { status: r.status(), body: await r.json().catch(() => null) };
    },
  };
}
// 숨김 → 표시로 상태를 한 번 다시 읽게 한다(열린 대화창이 있을 때). 준비 POST는 생기지 않아야 한다.
async function refreshStatus(u) {
  await u.page.evaluate(() => window.__setHidden(true));
  await sleep(300);
  await u.page.evaluate(() => window.__setHidden(false));
  await sleep(1500);
}
async function shot(u, name) {
  if (!SHOTS) return;
  await u.page.mouse.move(5, 5);
  await u.page.screenshot({ path: `${SHOTS}/runner-http-${name}.png` }).catch(() => {});
}
const sendBtnOf = (u) => u.chat.getByRole("button", { name: /^(보내기|다시 보내기)$/ });
const actionBtnOf = (u) => u.chat.getByRole("button", { name: /^(대화 준비|다시 시도|환경 다시 켜기)$/ });
const lastResponse = (u) => u.net.responses.at(-1);
async function waitResponses(u, n, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (u.net.responses.length < n && Date.now() < deadline) await sleep(100);
  return u.net.responses.length >= n;
}
const statusShape = (b) =>
  b && b.available === false && b.verification === "unverified" && b.model === "gpt-6.1-sol" && !("accessExpiresAt" in b) && !("remainingAdmissions" in b);

// ---------- 설정: A(editor), B(commenter → editor) ----------
const A = await newUser();
const B = await newUser();
const userA = await approvedAccount("A", A.page, "editor");
const userB = await approvedAccount("B", B.page, "commenter");
const apiA = apiClient(A.page);
const apiB = apiClient(B.page);
check("setup. A approved editor, B approved commenter", userA.isApproved && userA.role === "editor" && userB.isApproved && userB.role === "commenter");

// ---------- E1. 첫 진입 → 202 provisioning [real] ----------
let sid;
await step("E1", async () => {
  await settle(A.page);
  check("E1 [real]. me codexRunner true for approved editor", userA.codexRunner === true, String(userA.codexRunner));
  check("E1 [real]. one entry POST after approved sign-in", A.net.posts.length === 1, String(A.net.posts.length));
  await waitResponses(A, 1);
  const r = A.net.responses[0];
  check(
    "E1 [real]. 202 with unwrapped status body (provisioning, hint 2, no trial fields)",
    r?.status === 202 && statusShape(r.body) && r.body.reason === "provisioning" && r.body.retryAfterSeconds === 2,
    JSON.stringify(r)
  );
  const g = await apiA.get("/research/codex/status");
  check("E1 [real]. GET same projection", g.status === 200 && statusShape(g.body) && g.body.reason === "provisioning", JSON.stringify(g));

  const created = await apiA.send("POST", "/research/sessions", { title: "준비 통합 대화" });
  sid = created.body?.id;
  check("E1 [real]. session create allowed while preparing", created.status === 201 || created.status === 200, String(created.status));
  // 앱 안 이동(링크)은 진입이 아니다. 주소로 새 문서를 여는 것(goto)은 새로고침처럼 새 탭 진입이다.
  const header = A.page.locator("header").first();
  await header.getByRole("link", { name: "글 목록" }).first().click();
  await A.page.waitForURL(/\/posts\/$/);
  await settle(A.page);
  await header.getByRole("link", { name: "내 연구" }).first().click();
  await A.page.waitForURL(/\/research\//);
  await settle(A.page);
  check("E1 [real]. in-app navigation sends no POST", A.net.posts.length === 1, String(A.net.posts.length));
  await A.page.goto(U(`/research/session/?id=${sid}`));
  await A.chat.getByLabel("메시지").waitFor();
  await settle(A.page);
  check("E1 [real]. full document load = one new entry POST", A.net.posts.length === 2, String(A.net.posts.length));
  check("E1 [real]. 환경 활성화 중 shown", await appears(A.chat.getByText("환경 활성화 중")));
  await A.chat.getByLabel("메시지").fill("A 준비 중 초안");
  check("E1 [real]. draft editable, only send locked", (await A.chat.getByLabel("메시지").isEnabled()) && (await sendBtnOf(A).isDisabled()));
  const g0 = A.net.gets;
  await sleep(6500);
  const polled = A.net.gets - g0;
  check("E1 [real]. visible-panel GET polling ~2s (2..5 in 6.5s), no POST", polled >= 2 && polled <= 5 && A.net.posts.length === 2, `${polled} ${A.net.posts.length}`);
  await shot(A, "provisioning");
});

// ---------- G1. 상태 조회는 읽기 전용: polling이 activity·phase·generation을 바꾸지 않음 [real] ----------
let snapA;
await step("G1", async () => {
  snapA = await inspect(userA.accountId);
  check("G1 [real]. after entry: provisioning, unbound, one pending ensure", snapA.phase === "provisioning" && snapA.podBound === false && snapA.pendingEnsure === 1, JSON.stringify(snapA));
  const g0 = A.net.gets;
  await sleep(12000);
  const s2 = await inspect(userA.accountId);
  check("G1 [real]. visible-panel polling did happen", A.net.gets - g0 >= 3, String(A.net.gets - g0));
  check("G1 [real]. polling left lastActivity/phase/generation unchanged",
    s2.lastActivity === snapA.lastActivity && s2.phase === snapA.phase && s2.generation === snapA.generation, JSON.stringify(s2));
});

// ---------- R1. 새로고침·두 번째 탭: 탭 진입마다 1회, 서로 다른 key, 같은 준비로 합쳐짐 [real] ----------
await step("R1", async () => {
  const p0 = A.net.posts.length;
  await A.page.reload();
  await A.chat.getByLabel("메시지").waitFor();
  await settle(A.page);
  check("R1 [real]. reload = one new entry POST", A.net.posts.length === p0 + 1, String(A.net.posts.length - p0));
  check("R1 [real]. draft kept across reload", (await A.chat.getByLabel("메시지").inputValue()) === "A 준비 중 초안");
  const second = await A.context.newPage();
  await prepareTab(second);
  await second.goto(U("/posts/"));
  await settle(second);
  await second.close();
  check("R1 [real]. second tab = one more entry POST", A.net.posts.length === p0 + 2, String(A.net.posts.length - p0));
  check("R1 [real]. every entry uses a distinct key", new Set(A.net.posts).size === A.net.posts.length);
  await waitResponses(A, A.net.posts.length);
  const tail = A.net.responses.slice(-2);
  check(
    "R1 [real]. coalesced entries still report provisioning (202)",
    tail.length === 2 && tail.every((r) => r.status === 202 && r.body?.reason === "provisioning"),
    JSON.stringify(tail.map((r) => [r.status, r.body?.reason]))
  );
  const s = await inspect(userA.accountId);
  check("R1 [real]. server coalesced: one state/pending ensure, same generation (receipts are not Pods)",
    s.pendingEnsure === 1 && s.generation === snapA.generation && s.phase === "provisioning" && s.entryReceipts >= snapA.entryReceipts, JSON.stringify(s));
  check("R1 [real]. coalesced entries did not extend lastActivity", s.lastActivity === snapA.lastActivity, `${snapA.lastActivity} → ${s.lastActivity}`);
});

// ---------- T1. phase 전이: 문구·버튼·전송 잠금·polling·명시 준비(#16 stopping·확인 필요 포함) [real] ----------
async function toPhase(phase) {
  await host(`phase ${userA.accountId} ${phase}`);
  await refreshStatus(A);
}
await step("T1", async () => {
  // capacity_wait: 버튼 없음, 15초 hint
  await toPhase("waiting");
  check("T1 [real]. waiting → 사용 대기 중", await appears(A.chat.getByText("사용 대기 중")));
  check("T1 [real]. waiting: no action button, send locked, draft kept",
    (await actionBtnOf(A).count()) === 0 && (await sendBtnOf(A).isDisabled()) && (await A.chat.getByLabel("메시지").inputValue()) === "A 준비 중 초안");
  let g0 = A.net.gets;
  await sleep(9000);
  check("T1 [real]. waiting: slow polling (≤1 GET in 9s)", A.net.gets - g0 <= 1, String(A.net.gets - g0));

  // runner_unreachable(합성 UID/불확실)
  await toPhase("unreachable");
  check("T1 [real]. unreachable → 환경 연결 확인 중", await appears(A.chat.getByText("환경 연결 확인 중")));
  const text = (await A.chat.innerText()).replace(/\s+/g, " ");
  check("T1 [real]. unreachable copy: checking connection/preparation, no recovery claim",
    text.includes("환경 연결과 준비 결과를 확인하고 있습니다") && !/잠시|켜지면|새 환경|복구/.test(text), text.slice(0, 160));
  check("T1 [real]. unreachable: no action button, send locked", (await actionBtnOf(A).count()) === 0 && (await sendBtnOf(A).isDisabled()));
  await shot(A, "unreachable");

  // DB READY도 runner_unreachable. 진입 POST는 Pod가 묶여 있으면 200(재사용).
  await toPhase("ready");
  const g = await apiA.get("/research/codex/status");
  check("T1 [real]. ready (DB) → GET runner_unreachable, hint 5", g.status === 200 && statusShape(g.body) && g.body.reason === "runner_unreachable" && g.body.retryAfterSeconds === 5, JSON.stringify(g.body));
  const n0 = A.net.responses.length;
  await A.page.reload();
  await A.chat.getByLabel("메시지").waitFor();
  await waitResponses(A, n0 + 1);
  const r = lastResponse(A);
  check("T1 [real]. ready: entry POST 200 reuse, still runner_unreachable", r?.status === 200 && r.body?.reason === "runner_unreachable", JSON.stringify(r));
  check("T1 [real]. ready: never shown as connected/available",
    (await appears(A.chat.getByText("환경 연결 확인 중"))) && (await A.chat.getByText(/연결되어 있습니다/).count()) === 0 && (await sendBtnOf(A).isDisabled()));

  // stopping(합성 UID, #16): 종료 근거 전에는 idle_stopped가 아니라 stopping(hint 5). 진입 POST는 저장되지 않는 거절이다.
  await toPhase("stopping");
  const gs = await apiA.get("/research/codex/status");
  check("T1 [real]. stopping → GET stopping, hint 5 (not idle_stopped)",
    gs.status === 200 && statusShape(gs.body) && gs.body.reason === "stopping" && gs.body.retryAfterSeconds === 5, JSON.stringify(gs.body));
  check("T1 [real]. stopping: 환경 정리 중, no action button, send locked",
    (await appears(A.chat.getByText("환경 정리 중"))) && (await actionBtnOf(A).count()) === 0 && (await sendBtnOf(A).isDisabled()));
  g0 = A.net.gets;
  await sleep(11000);
  check("T1 [real]. stopping: polls with 5s hint", A.net.gets - g0 >= 1 && A.net.gets - g0 <= 3, String(A.net.gets - g0));
  await shot(A, "stopping");
  let p0 = A.net.posts.length;
  let n1 = A.net.responses.length;
  await A.page.reload();
  await A.chat.getByLabel("메시지").waitFor();
  await waitResponses(A, n1 + 1);
  check("T1 [real]. stopping: entry POST refused (no 2xx), still 환경 정리 중",
    lastResponse(A)?.status >= 400 && (await appears(A.chat.getByText("환경 정리 중"))), JSON.stringify(lastResponse(A)));
  await sleep(6000);
  check("T1 [real]. stopping: refused POST not repeated", A.net.posts.length === p0 + 1, String(A.net.posts.length - p0));

  // runner_failed(#16): 확인 필요. 버튼·자동 조회 없음, 진입 POST는 409 runner_reconciliation_required이며 근거를 바꾸지 않는다.
  await toPhase("failed");
  check("T1 [real]. failed → 확인이 필요합니다, no action button",
    (await appears(A.chat.getByText("확인이 필요합니다"))) && (await actionBtnOf(A).count()) === 0);
  g0 = A.net.gets;
  p0 = A.net.posts.length;
  await sleep(7000);
  check("T1 [real]. failed: no automatic GET polling or POST", A.net.gets === g0 && A.net.posts.length === p0, `${A.net.gets - g0} ${A.net.posts.length - p0}`);
  await shot(A, "failed");
  const sf = await inspect(userA.accountId);
  n1 = A.net.responses.length;
  await A.page.reload();
  await A.chat.getByLabel("메시지").waitFor();
  await waitResponses(A, n1 + 1);
  check("T1 [real]. failed: entry POST → 409 runner_reconciliation_required",
    lastResponse(A)?.status === 409 && lastResponse(A)?.body?.error?.code === "runner_reconciliation_required", JSON.stringify(lastResponse(A)));
  check("T1 [real]. failed: GET result shown (확인이 필요합니다), no button", (await appears(A.chat.getByText("확인이 필요합니다"))) && (await actionBtnOf(A).count()) === 0);
  await sleep(5000);
  const sf2 = await inspect(userA.accountId);
  check("T1 [real]. failed: one POST, phase/generation/receipts unchanged",
    A.net.posts.length === p0 + 1 && sf2.phase === sf.phase && sf2.generation === sf.generation && sf2.entryReceipts === sf.entryReceipts, JSON.stringify(sf2));

  // idle_stopped: "환경 다시 켜기" → POST 1회 → provisioning
  await toPhase("stopped");
  check("T1 [real]. stopped → 환경이 쉬는 중 + 환경 다시 켜기", (await appears(A.chat.getByText("환경이 쉬는 중"))) && (await appears(A.chat.getByRole("button", { name: "환경 다시 켜기" }))));
  check("T1 [real]. stopped: draft kept, send locked", (await A.chat.getByLabel("메시지").inputValue()) === "A 준비 중 초안" && (await sendBtnOf(A).isDisabled()));
  await shot(A, "stopped");
  p0 = A.net.posts.length;
  n1 = A.net.responses.length;
  await A.chat.getByRole("button", { name: "환경 다시 켜기" }).first().click();
  await waitResponses(A, n1 + 1);
  check("T1 [real]. resume: one POST → 202 provisioning", A.net.posts.length === p0 + 1 && lastResponse(A)?.status === 202 && lastResponse(A)?.body?.reason === "provisioning", JSON.stringify(lastResponse(A)));
});

// ---------- M1. 메시지 선접수 직접 503 6종: 서버 기록 변화 없음, 화면은 전송 잠금 [real] ----------
await step("M1", async () => {
  const cases = [
    ["preparing", "codex_provisioning"],
    ["waiting", "codex_capacity_wait"],
    ["unreachable", "codex_runner_unreachable"],
    ["ready", "codex_runner_unreachable"],
    ["stopping", "codex_runner_stopped"],
    ["failed", "codex_runner_failed"],
    ["stopped", "codex_runner_stopped"],
  ];
  for (const [phase, code] of cases) {
    await toPhase(phase);
    const before = await apiA.get(`/research/sessions/${sid}`);
    const post = await apiA.send("POST", `/research/sessions/${sid}/messages`, { text: `M1 ${phase}`, expectedVersion: before.body.version });
    const after = await apiA.get(`/research/sessions/${sid}`);
    check(`M1 [real]. ${phase}: direct 503 ${code}`, post.status === 503 && post.body?.error?.code === code, `${post.status} ${post.body?.error?.code}`);
    check(`M1 [real]. ${phase}: no item/version change`,
      after.body.version === before.body.version && after.body.items.length === before.body.items.length);
    check(`M1 [real]. ${phase}: UI send locked`, await sendBtnOf(A).isDisabled());
  }
  check("M1 [real]. draft kept through all refusals", (await A.chat.getByLabel("메시지").inputValue()) === "A 준비 중 초안");
});

// ---------- E3/E3b. 자격 없음(commenter) → 재로그인으로 editor 전환 [real] ----------
await step("E3", async () => {
  await B.page.goto(U("/research/"));
  await settle(B.page);
  check("E3 [real]. commenter: me codexRunner is present false", userB.codexRunner === false, String(userB.codexRunner));
  check("E3 [real]. commenter: no preparation POST, no status reads", B.net.posts.length === 0 && B.net.gets === 0, `${B.net.posts.length} ${B.net.gets}`);
  const g = await apiB.get("/research/codex/status");
  check("E3 [real]. commenter: GET 200 not_enabled_for_account", g.status === 200 && g.body?.available === false && g.body?.reason === "not_enabled_for_account", JSON.stringify(g));

  await host(`role ${userB.githubId} editor`);
  await signIn(B.page); // 역할 변경은 session을 지우므로 재로그인 경로로 확인한다
  await settle(B.page);
  const meB = await meOf(B.page);
  check("E3b [real]. after promotion + re-login: codexRunner true", meB.codexRunner === true && meB.role === "editor");
  check("E3b [real]. exactly one entry POST for the new eligibility", B.net.posts.length === 1, String(B.net.posts.length));
  await waitResponses(B, 1);
  check("E3b [real]. B entry → 202 provisioning (independent of A)", B.net.responses[0]?.status === 202 && B.net.responses[0]?.body?.reason === "provisioning", JSON.stringify(B.net.responses[0]));
});

// ---------- E4. 세션 소실 401: 기존 인증 처리, 반복 POST 없음 [real] ----------
await step("E4", async () => {
  await B.page.goto(U("/research/"));
  if (!(await B.chat.isVisible())) await B.page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await appears(B.chat.getByText("환경 활성화 중"));
  const p0 = B.net.posts.length;
  await B.context.clearCookies();
  const g = await apiB.get("/research/codex/status");
  check("E4 [real]. GET after cookie loss → 401", g.status === 401, String(g.status));
  await sleep(8000);
  check("E4 [real]. no preparation POST after 401", B.net.posts.length === p0, String(B.net.posts.length - p0));
  await B.page.goto(U("/"));
  check("E4 [real]. signed-out header shown", await appears(B.page.locator("header").getByRole("link", { name: "GitHub 로그인" })));
  check("E4 [real]. signed-out visit sends no POST", B.net.posts.length === p0);
});

// ---------- A1. 계정 전환 뒤 문서 재진입: 이전 계정의 늦은 준비 응답·상태가 새 계정에 남지 않음 [hybrid: 실제 응답 + 4초 지연] ----------
// 원래 문서를 goto로 교체하므로 같은 문서 안의 account/eligibility epoch 경합(늦은 응답 폐기)은 증명하지 않는다(mock N11이 담당).
await step("A1", async () => {
  const C = await newUser();
  const userC = await approvedAccount("C", C.page, "editor");
  await settle(C.page);
  const p0 = C.net.posts.length;
  // 문서가 바뀌면 지연된 응답은 전달되지 못할 수 있다(그 경우를 숨기지 않고 센다).
  const delayed = { delivered: 0, dropped: 0 };
  await C.context.route("**/api/research/codex/**", async (route) => {
    const resp = await route.fetch();
    await sleep(4000);
    await route.fulfill({ response: resp }).then(() => delayed.delivered++, () => delayed.dropped++);
  });
  await C.page.reload(); // 진입 POST(지연) 시작
  await sleep(500);
  await C.context.clearCookies({ name: "m3_fixture_identity" });
  const tab = await C.context.newPage();
  await tab.goto(U("/api/auth/github/start"));
  await tab.waitForURL(U("/"));
  const switched = await meOf(tab);
  await tab.close();
  check("A1 [hybrid]. setup: switched to another (unapproved) account", switched?.accountId && switched.accountId !== userC.accountId);
  await C.page.goto(U("/research/"));
  await sleep(6000);
  const text = await C.page.locator("body").innerText();
  check("A1 [hybrid]. after re-entry, no C runner state shown to the new account", !/환경 활성화 중|환경 연결 확인 중/.test(text), `${text.replace(/\s+/g, " ").slice(0, 120)} (delayed delivered=${delayed.delivered} dropped=${delayed.dropped})`);
  check("A1 [hybrid]. new (ineligible) account sends no POST", C.net.posts.length === p0 + 1, String(C.net.posts.length - p0));
  await C.context.close();
});

// ---------- U1. 미바인딩 생성 예약(확인 중) → runner_unreachable, 재진입은 같은 generation으로 합침 [real] ----------
await step("U1", async () => {
  const D = await newUser();
  const userD = await approvedAccount("D", D.page, "editor");
  await settle(D.page);
  const created = await apiClient(D.page).send("POST", "/research/sessions", { title: "예약 확인 대화" });
  await D.page.goto(U(`/research/session/?id=${created.body?.id}`));
  await D.chat.getByLabel("메시지").waitFor();
  await D.chat.getByLabel("메시지").fill("D 초안");
  await host(`reserve ${userD.accountId}`);
  const s0 = await inspect(userD.accountId);
  check("U1 [real]. fixture: creation reserved, unbound", s0.creationReserved === true && s0.podBound === false, JSON.stringify(s0));
  await refreshStatus(D);
  const g = await apiClient(D.page).get("/research/codex/status");
  check("U1 [real]. GET runner_unreachable, unverified, hint 5", g.status === 200 && statusShape(g.body) && g.body.reason === "runner_unreachable" && g.body.retryAfterSeconds === 5, JSON.stringify(g.body));
  const panel = (await D.chat.innerText()).replace(/\s+/g, " ");
  check("U1 [real]. whole panel (status + composer) makes no recovery/turn-on claim", !/잠시|켜지면|새 환경|복구/.test(panel), panel.slice(0, 200));
  check("U1 [real]. UI 확인 중 copy, no action button, send locked, draft kept",
    (await appears(D.chat.getByText("환경 연결과 준비 결과를 확인하고 있습니다"))) && (await actionBtnOf(D).count()) === 0 &&
      (await sendBtnOf(D).isDisabled()) && (await D.chat.getByLabel("메시지").inputValue()) === "D 초안");
  await shot(D, "reserved");
  const n0 = D.net.responses.length;
  await D.page.reload();
  await D.chat.getByLabel("메시지").waitFor();
  await waitResponses(D, n0 + 1);
  const r = lastResponse(D);
  check("U1 [real]. re-entry POST 202 runner_unreachable (unbound)", r?.status === 202 && r.body?.reason === "runner_unreachable", JSON.stringify(r));
  const s1 = await inspect(userD.accountId);
  check("U1 [real]. re-entry coalesced: same generation, no new create", s1.generation === s0.generation && s1.pendingEnsure === s0.pendingEnsure && s1.creationReserved === true, JSON.stringify(s1));
  const before = await apiClient(D.page).get(`/research/sessions/${created.body?.id}`);
  const post = await apiClient(D.page).send("POST", `/research/sessions/${created.body?.id}/messages`, { text: "U1", expectedVersion: before.body.version });
  check("U1 [real]. message preflight 503 codex_runner_unreachable", post.status === 503 && post.body?.error?.code === "codex_runner_unreachable", `${post.status} ${post.body?.error?.code}`);
  await D.context.close();
});

// ---------- P1. 진입 POST network 실패 → GET not_prepared → "대화 준비" 명시 요청 1회 [hybrid: 첫 POST만 abort] ----------
await step("P1", async () => {
  const E = await newUser();
  await signIn(E.page);
  const me0 = await meOf(E.page);
  await fs.writeFile(`${SHARED}/E.role`, "editor");
  await fs.writeFile(`${SHARED}/E.id`, me0.githubId);
  await waitFile("E.ok");
  let aborted = 0;
  await E.context.route("**/api/research/codex/runner", (route) => (aborted++ === 0 ? route.abort("failed") : route.continue()));
  await signIn(E.page);
  const userE = await meOf(E.page);
  await settle(E.page);
  check("P1 [hybrid]. first entry POST aborted once, not retried automatically", aborted === 1 && E.net.posts.length === 1, `${aborted} ${E.net.posts.length}`);
  await host(`phase ${userE.accountId} not_prepared`); // 새 계정에 registry가 없음을 확인만 한다
  const g = await apiClient(E.page).get("/research/codex/status");
  check("P1 [real]. GET not_prepared (no registry row)", g.status === 200 && statusShape(g.body) && g.body.reason === "not_prepared" && !("retryAfterSeconds" in g.body), JSON.stringify(g.body));
  if (!(await E.chat.isVisible())) await E.page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  check("P1 [real]. UI 대화 준비 button", await appears(E.chat.getByRole("button", { name: "대화 준비" })));
  const n0 = E.net.responses.length;
  await E.chat.getByRole("button", { name: "대화 준비" }).first().click();
  await waitResponses(E, n0 + 1);
  check("P1 [real]. explicit prepare: one POST, new key → 202 provisioning",
    E.net.posts.length === 2 && E.net.posts[0] !== E.net.posts[1] && lastResponse(E)?.status === 202 && lastResponse(E)?.body?.reason === "provisioning", JSON.stringify(lastResponse(E)));
  await E.context.close();
});

// ---------- F0. feature-off(API 재생성): 준비 POST 없음, 기존 상태·대화 유지 [real] ----------
await step("F0", async () => {
  await host("flag false");
  const p0 = A.net.posts.length;
  await A.page.goto(U(`/research/session/?id=${sid}`));
  await A.chat.getByLabel("메시지").waitFor();
  await settle(A.page);
  const meA = await meOf(A.page);
  check("F0 [real]. off: me codexRunner present false, research still allowed", meA.codexRunner === false && meA.isApproved && meA.role === "editor");
  check("F0 [real]. off: no preparation POST", A.net.posts.length === p0, String(A.net.posts.length - p0));
  check("F0 [real]. off: conversation still opens", (await A.chat.getByRole("heading", { name: "준비 통합 대화" }).count()) === 1);
  const text = (await A.chat.innerText()).replace(/\s+/g, " ");
  check("F0 [real]. off: no runner state copy", !/환경 활성화 중|환경 연결 확인 중|사용 대기 중|환경이 쉬는 중|환경 정리 중|확인이 필요합니다/.test(text), text.slice(0, 160));
  const g = await apiA.get("/research/codex/status");
  check("F0 [real]. off: legacy status (not a runner reason)", g.status === 200 && !["provisioning", "capacity_wait", "runner_unreachable", "runner_failed", "idle_stopped", "not_prepared"].includes(g.body?.reason), JSON.stringify(g.body));
  const before = await apiA.get(`/research/sessions/${sid}`);
  const post = await apiA.send("POST", `/research/sessions/${sid}/messages`, { text: "F0 off", expectedVersion: before.body.version });
  check("F0 [real]. off: message refusal is not a runner preflight code", !String(post.body?.error?.code ?? "").startsWith("codex_runner") && !["codex_provisioning", "codex_capacity_wait"].includes(post.body?.error?.code), `${post.status} ${post.body?.error?.code}`);
  await shot(A, "feature-off");
});

check("console has no errors", consoleErrors.length === 0, consoleErrors.join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
