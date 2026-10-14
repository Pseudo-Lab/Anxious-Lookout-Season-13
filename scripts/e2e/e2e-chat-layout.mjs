// #9 공통 대화창 폭 조절·넓은 답변 표시 검증(모의 API). 실제 backend/Codex 동작 검증이 아니다.
// - 왼쪽 경계 끌기·키보드 폭 조절, 화면 폭에 맞춘 최소/최대, 닫기/재열기·이동·새로고침·계정 전환 뒤 폭 유지
// - 넓은 표·코드·긴 문자열 답변: 표 마지막 열까지 대화창 안에서 접근, 페이지·대화창 바깥 가로 넘침 없음
// - 1280/1024/768/390px 화면, 좁음/기본/넓음 폭의 스크린샷(SHOTS_DIR)
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;
const SHOTS = process.env.SHOTS_DIR;
const TAG = (process.env.EXPECT_SHA ?? "local").slice(0, 7);

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

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text());
});
page.on("dialog", (d) => void d.accept());
const chat = page.locator("#chat-panel");
const sep = page.getByRole("separator", { name: "대화창 폭 조절" });

async function shot(name) {
  if (!SHOTS) return;
  await page.screenshot({ path: `${SHOTS}/${TAG}-${name}.png` }).catch(() => {});
}

async function login() {
  await page.goto(U("/"));
  await page.locator("header").first().getByRole("link", { name: "GitHub 로그인" }).first().click();
  await page.locator("header").first().getByText("mock-user").first().waitFor();
}
async function switchAccount() {
  const tab = await context.newPage();
  await tab.goto(U("/api/auth/github/start"));
  await tab.waitForURL(U("/"));
  await tab.close();
}
async function csrf() {
  return (await (await page.request.get(U("/api/auth/me"))).json()).csrfToken;
}
async function accountId() {
  return (await (await page.request.get(U("/api/auth/me"))).json()).user.accountId;
}
async function createSession(title) {
  const r = await page.request.post(U("/api/research/sessions"), {
    data: { title },
    headers: { Origin: ORIGIN, "X-CSRF-Token": await csrf(), "Idempotency-Key": crypto.randomUUID() },
  });
  return (await r.json()).id;
}
async function openPanel() {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(300);
  if (!(await chat.isVisible())) await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await chat.getByLabel("메시지").waitFor();
}
const panelWidth = async () => Math.round((await chat.boundingBox()).width);
const valueNow = async () => Number(await sep.getAttribute("aria-valuenow"));
const storedWidth = (acc) =>
  page.evaluate((a) => JSON.parse(sessionStorage.getItem(`research:pending:${a}:chat-ui`) ?? "null")?.width ?? null, acc);

// 화면·대화창 넘침과 표 마지막 열 접근 측정
async function measure() {
  return page.evaluate(() => {
    const de = document.documentElement;
    const panel = document.getElementById("chat-panel");
    const pr = panel.getBoundingClientRect();
    const scroller = panel.querySelector("[data-chat-scroll]") ?? panel.querySelector(".overflow-y-auto");
    const header = document.querySelector("body > header");
    const main = document.querySelector("body > main");
    const table = panel.querySelector(".prose table");
    const box = table?.parentElement;
    // 대화창 밖으로 나간 요소(표·코드 스크롤 상자 안쪽과 폭 조절 경계는 제외)
    const outside = [...panel.querySelectorAll("*")]
      .filter((el) => {
        if (el.closest(".table-scroll, pre, [role=separator]")) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (r.right > pr.right + 1 || r.left < pr.left - 1);
      })
      .map((el) => `${el.tagName.toLowerCase()}:${(el.textContent ?? "").trim().slice(0, 20)}`);
    const texts = [...panel.querySelectorAll(".prose p, .prose li, li[data-role] > p")];
    const clipped = texts.filter((p) => p.scrollWidth > p.clientWidth + 1).map((p) => (p.textContent ?? "").slice(0, 20));
    const mobile = window.matchMedia("(max-width: 767px)").matches;
    return {
      vw: de.clientWidth,
      pageOverflow: de.scrollWidth - de.clientWidth,
      bodyPad: Math.round(parseFloat(getComputedStyle(document.body).paddingRight)),
      panelW: Math.round(pr.width),
      panelLeft: Math.round(pr.left),
      mainRight: Math.round(main.getBoundingClientRect().right),
      scrollerOverflow: scroller.scrollWidth - scroller.clientWidth,
      headerOverflow: Math.max(header.scrollWidth - header.clientWidth, header.firstElementChild.scrollWidth - header.firstElementChild.clientWidth),
      boxScrolls: box ? box.scrollWidth > box.clientWidth : null,
      boxFocusable: box ? box.tabIndex === 0 && box.getAttribute("role") === "region" : null,
      // 처음 위치에서 오른쪽에 가려진 열이 있다는 가장자리 그림자
      moreRight: box?.parentElement?.hasAttribute("data-more-right") ?? null,
      moreLeft: box?.parentElement?.hasAttribute("data-more-left") ?? null,
      outside,
      clipped,
      mobile,
    };
  });
}
async function lastColumn() {
  return page.evaluate(async () => {
    const panel = document.getElementById("chat-panel");
    const th = [...panel.querySelectorAll(".prose table th")].at(-1);
    if (!th) return { found: false };
    th.scrollIntoView({ block: "center", inline: "end" });
    await new Promise((r) => setTimeout(r, 150));
    const pr = panel.getBoundingClientRect();
    const r = th.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + Math.min(r.width / 2, 20), r.top + r.height / 2);
    const out = { found: true, text: th.textContent, inPanel: r.left >= pr.left - 1 && r.right <= pr.right + 1, hit: !!hit && th.contains(hit) };
    // 다음 측정을 위해 표 스크롤을 처음으로
    const box = th.closest(".table-scroll");
    out.edgeAtEnd = !!box?.parentElement?.hasAttribute("data-more-left") && !box?.parentElement?.hasAttribute("data-more-right");
    if (box) box.scrollLeft = 0;
    await new Promise((r) => setTimeout(r, 150));
    return out;
  });
}
function layoutChecks(label, m, col, { expectWidth } = {}) {
  check(`${label}: no page horizontal overflow`, m.pageOverflow <= 0, String(m.pageOverflow));
  check(`${label}: no panel horizontal overflow`, m.scrollerOverflow <= 0, String(m.scrollerOverflow));
  check(`${label}: no element outside panel`, m.outside.length === 0, m.outside.slice(0, 3).join(" | "));
  check(`${label}: long text wraps inside`, m.clipped.length === 0, m.clipped.slice(0, 3).join(" | "));
  check(`${label}: header nav fits`, m.headerOverflow <= 0, String(m.headerOverflow));
  check(`${label}: table scrolls inside its box`, m.boxScrolls === true);
  check(`${label}: scrolling table is keyboard focusable region`, m.boxFocusable === true);
  check(`${label}: last column reachable inside panel`, col.found && col.inPanel && col.hit && /마지막열/.test(col.text), JSON.stringify(col));
  check(`${label}: edge shadow shows hidden columns side`, m.moreRight === true && m.moreLeft === false && col.edgeAtEnd === true, JSON.stringify([m.moreRight, m.moreLeft, col.edgeAtEnd]));
  if (!m.mobile) {
    check(`${label}: page content not under panel`, m.mainRight <= m.panelLeft + 1 && m.bodyPad === m.panelW, `main ${m.mainRight} panel ${m.panelLeft} pad ${m.bodyPad}/${m.panelW}`);
    check(`${label}: content keeps >= 360px`, m.panelLeft >= 360, String(m.panelLeft));
    if (expectWidth !== undefined) check(`${label}: panel width ${expectWidth}`, Math.abs(m.panelW - expectWidth) <= 1, String(m.panelW));
  }
}

await setMock("me=normal&health=normal&logout=normal&nextLogin=approved&reuse=0&as=&access=approved&codex=fixture&window=&remaining=");
await login();
const accountA = await accountId();
const sid = await createSession("넓은 표 대화");
await page.goto(U(`/research/session/?id=${sid}`));
await chat.getByLabel("메시지").waitFor();
await chat.getByLabel("메시지").fill("/fixture/wide-answer 모델 비교 표");
await chat.getByRole("button", { name: "보내기" }).click();
check("C0. wide answer rendered", await appears(chat.getByText("모의 응답: 비교 표입니다."), 15000));
await chat.getByLabel("메시지").and(chat.locator(":enabled")).waitFor({ timeout: 15000 });
await page.goto(U("/posts/"));
await openPanel();
const resizable = (await sep.count()) > 0;
check("C1. resize boundary present on desktop", resizable);

if (resizable) {
  // C2. 끌기: 왼쪽으로 끌면 넓어지고, 끄는 동안만 조절 상태(커서·글자 선택 막기)
  await step("C2", async () => {
    await chat.getByLabel("메시지").fill("폭 조절 중 초안");
    check("C2. boundary aria (value/min/max)", (await valueNow()) === 416 && (await sep.getAttribute("aria-valuemin")) === "320" && (await sep.getAttribute("aria-valuemax")) === "920");
    const b = await sep.boundingBox();
    const cx = b.x + b.width / 2;
    const cy = b.y + b.height / 2;
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 200, cy, { steps: 8 });
    const during = await page.evaluate(() => ({
      flag: document.documentElement.hasAttribute("data-chat-resizing"),
      select: getComputedStyle(document.body).userSelect,
      pad: Math.round(parseFloat(getComputedStyle(document.body).paddingRight)),
    }));
    check("C2. resizing state while dragging", during.flag && during.select === "none" && during.pad === 616, JSON.stringify(during));
    await shot("1280-dragging");
    await page.mouse.up();
    const after = await page.evaluate(() => ({
      flag: document.documentElement.hasAttribute("data-chat-resizing"),
      select: getComputedStyle(document.body).userSelect,
    }));
    check("C2. drag left widens panel by drag distance", (await panelWidth()) === 616 && (await valueNow()) === 616, String(await panelWidth()));
    check("C2. resizing state cleared after drop", !after.flag && after.select !== "none", JSON.stringify(after));
    check("C2. dropped width saved for account", (await storedWidth(accountA)) === 616);
    // 최대·최소에서 멈춤
    await page.mouse.move(cx - 200, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 2000, cy, { steps: 5 });
    await page.mouse.up();
    check("C2. drag clamps at max (content keeps 360px)", (await panelWidth()) === 920, String(await panelWidth()));
    const b2 = await sep.boundingBox();
    await page.mouse.move(b2.x + b2.width / 2, cy);
    await page.mouse.down();
    await page.mouse.move(b2.x + 2000, cy, { steps: 5 });
    await page.mouse.up();
    check("C2. drag clamps at min 320", (await panelWidth()) === 320, String(await panelWidth()));
  });

  // C3. 끄는 중 Escape는 취소(시작 폭 복귀, 대화창은 열린 채), 창 포커스 이탈은 그 폭으로 끝냄
  await step("C3", async () => {
    const b = await sep.boundingBox();
    const cx = b.x + b.width / 2;
    const cy = b.y + b.height / 2;
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 150, cy, { steps: 5 });
    await page.keyboard.press("Escape");
    await page.mouse.up();
    const st = await page.evaluate(() => ({ flag: document.documentElement.hasAttribute("data-chat-resizing"), select: getComputedStyle(document.body).userSelect }));
    check("C3. Escape cancels drag back to start width", (await panelWidth()) === 320 && (await chat.isVisible()), String(await panelWidth()));
    check("C3. no resizing state after cancel", !st.flag && st.select !== "none", JSON.stringify(st));
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx - 100, cy, { steps: 5 });
    await page.evaluate(() => window.dispatchEvent(new Event("blur")));
    const st2 = await page.evaluate(() => document.documentElement.hasAttribute("data-chat-resizing"));
    await page.mouse.move(cx - 300, cy, { steps: 3 });
    await page.mouse.up();
    check("C3. window blur ends drag at current width", !st2 && (await panelWidth()) === 420, String(await panelWidth()));
  });

  // C4. 키보드: ←/→(Shift 4배), Home/End, 두 번 누르면 기본 폭, 넓게/기본 폭 버튼
  await step("C4", async () => {
    await sep.focus();
    await page.keyboard.press("Home");
    check("C4. Home -> min", (await panelWidth()) === 320 && (await valueNow()) === 320);
    await page.keyboard.press("ArrowLeft");
    check("C4. ArrowLeft widens by 16", (await panelWidth()) === 336);
    await page.keyboard.press("Shift+ArrowLeft");
    check("C4. Shift+ArrowLeft widens by 64", (await panelWidth()) === 400);
    await page.keyboard.press("ArrowRight");
    check("C4. ArrowRight narrows by 16", (await panelWidth()) === 384);
    await page.keyboard.press("End");
    check("C4. End -> max", (await panelWidth()) === 920);
    check("C4. focus stays on boundary", await sep.evaluate((el) => el === document.activeElement));
    await sep.dblclick();
    check("C4. double-click resets default 416", (await panelWidth()) === 416);
    await chat.getByRole("button", { name: "대화창 넓게" }).click();
    check("C4. widen button -> 720", (await panelWidth()) === 720);
    await chat.getByRole("button", { name: "대화창 기본 폭으로" }).click();
    check("C4. default button -> 416", (await panelWidth()) === 416);
    check("C4. draft kept through resizing", (await chat.getByLabel("메시지").inputValue()) === "폭 조절 중 초안");
  });

  // C5. 폭 유지: 닫기/재열기, 다른 페이지(클라이언트 이동), 새로고침
  await step("C5", async () => {
    await sep.focus();
    await page.keyboard.press("Home");
    for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+ArrowLeft");
    check("C5. setup width 576", (await panelWidth()) === 576);
    await chat.getByRole("button", { name: "대화창 닫기" }).click();
    check("C5. padding removed while closed", await page.evaluate(() => getComputedStyle(document.body).paddingRight === "0px"));
    await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
    check("C5. width kept after close/reopen", (await panelWidth()) === 576);
    await page.evaluate(() => document.querySelector('header a[href$="/status/"]')?.click());
    await page.waitForURL(/\/status\//);
    check("C5. width kept after client navigation", (await panelWidth()) === 576 && (await page.evaluate(() => getComputedStyle(document.body).paddingRight)) === "576px");
    await page.reload();
    await openPanel();
    check("C5. width kept after reload", (await panelWidth()) === 576);
    check("C5. draft kept after reload", (await chat.getByLabel("메시지").inputValue()) === "폭 조절 중 초안");
  });

  // C6. 화면 폭 변경: 고른 폭은 화면에 맞춰 줄였다가 다시 넓히면 복귀
  await step("C6", async () => {
    await sep.focus();
    await page.keyboard.press("End");
    check("C6. 1280 max 920", (await panelWidth()) === 920);
    await page.setViewportSize({ width: 1024, height: 800 });
    await page.waitForTimeout(200);
    const m1024 = await measure();
    check("C6. 1024: clamped to 664", m1024.panelW === 664 && (await sep.getAttribute("aria-valuemax")) === "664", String(m1024.panelW));
    check("C6. 1024: no page overflow / header fits", m1024.pageOverflow <= 0 && m1024.headerOverflow <= 0, JSON.stringify([m1024.pageOverflow, m1024.headerOverflow]));
    await page.setViewportSize({ width: 768, height: 800 });
    await page.waitForTimeout(200);
    const m768 = await measure();
    check("C6. 768: clamped to 408", m768.panelW === 408, String(m768.panelW));
    check("C6. 768: no page overflow / header fits", m768.pageOverflow <= 0 && m768.headerOverflow <= 0, JSON.stringify([m768.pageOverflow, m768.headerOverflow]));
    await page.setViewportSize({ width: 600, height: 800 });
    await page.waitForTimeout(200);
    check("C6. 600: switches to full-screen dialog", (await chat.getAttribute("role")) === "dialog" && (await sep.count()) === 0);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.waitForTimeout(200);
    check("C6. back to 1280 restores chosen 920", (await panelWidth()) === 920 && (await storedWidth(accountA)) === 920);
    check("C6. no stale modal state after resize", await page.evaluate(() => !document.querySelector("body > main")?.hasAttribute("inert") && document.documentElement.style.overflow === ""));
  });
}

// C7. 화면별·폭별 넓은 답변 표시(마지막 열 접근·넘침 없음) + 스크린샷
await page.goto(U("/posts/"));
await openPanel();
for (const vw of [1280, 1024, 768]) {
  await step(`C7 ${vw}`, async () => {
    await page.setViewportSize({ width: vw, height: 800 });
    await page.waitForTimeout(250);
    const presets = resizable
      ? [
          ["narrow", async () => { await sep.focus(); await page.keyboard.press("Home"); }, 320],
          ["default", async () => { await sep.dblclick(); }, Math.min(416, vw - 360)],
          ["wide", async () => { await sep.focus(); await page.keyboard.press("End"); }, vw - 360],
        ]
      : [["default", async () => {}, undefined]];
    for (const [label, apply, expectWidth] of presets) {
      await apply();
      await page.waitForTimeout(200);
      const col = await lastColumn();
      const m = await measure();
      layoutChecks(`C7. ${vw} ${label}`, m, col, { expectWidth });
      await chat.locator(".prose table").first().evaluate((t) => t.scrollIntoView({ block: "center" })).catch(() => {});
      await page.mouse.move(5, 5);
      await shot(`${vw}-${label}`);
    }
  });
}

// C8. 작은 화면(390): 전체 화면, 경계 없음, 넘침 없음, 표는 안에서 가로 스크롤(키보드 포함)
await step("C8", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  check("C8. 390: full-screen dialog without boundary", (await chat.getAttribute("role")) === "dialog" && (await sep.count()) === 0);
  const col = await lastColumn();
  const m = await measure();
  layoutChecks("C8. 390", m, col);
  const box = chat.locator(".table-scroll").first();
  await box.focus();
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowRight");
  // 브라우저의 키보드 스크롤은 부드럽게(애니메이션) 진행되므로 끝날 때까지 기다린다.
  await box.evaluate((el) => new Promise((r) => { const t0 = Date.now(); const tick = () => (el.scrollLeft > 0 || Date.now() - t0 > 2000 ? r() : requestAnimationFrame(tick)); tick(); }));
  const kb = await box.evaluate((el) => ({ left: el.scrollLeft, focused: el === document.activeElement, sw: el.scrollWidth, cw: el.clientWidth }));
  check("C8. focused table scrolls with arrow keys", kb.left > 0, JSON.stringify(kb));
  await box.evaluate((el) => {
    el.scrollLeft = 0;
    el.scrollIntoView({ block: "center" });
  });
  await page.waitForTimeout(400);
  await shot("390-full");
  // 입력창은 아래에 붙어 있어 긴 답변을 위로 올려도 보인다
  await chat.locator("[data-chat-scroll]").evaluate((el) => (el.scrollTop = 0)).catch(() => {});
  const ta = await chat.getByLabel("메시지").boundingBox();
  check("C8. composer pinned at bottom while reading", !!ta && ta.y + ta.height <= 844 && ta.y > 500, JSON.stringify(ta));
  await shot("390-top");
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(200);
});

// 비교용: 넓은 화면 대화 처음 부분과 대화 목록
await step("C7 shots", async () => {
  await chat.locator("[data-chat-scroll]").evaluate((el) => (el.scrollTop = 0));
  await page.mouse.move(5, 5);
  await shot("1280-top");
  await chat.getByRole("button", { name: "← 대화 목록" }).click();
  await chat.getByLabel("새 대화 제목").waitFor();
  await shot("1280-list");
  await chat.getByRole("link", { name: /넓은 표 대화/ }).click();
  await chat.getByLabel("메시지").waitFor();
});

// C9. 계정 전환: 폭은 계정별로 저장되고 다른 계정으로 새지 않는다
if (resizable) {
  await step("C9", async () => {
    await sep.focus();
    await page.keyboard.press("Home");
    for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowLeft");
    check("C9. account A width 384", (await panelWidth()) === 384);
    await switchAccount();
    await page.reload();
    const accountB = await accountId();
    check("C9. setup: switched to account B", accountB !== accountA);
    await openPanel().catch(async () => {
      await chat.getByLabel("새 대화 제목").waitFor();
    });
    check("C9. account B starts at default width", (await panelWidth()) === 416, String(await panelWidth()));
    check("C9. account B does not see A's conversation", (await chat.getByText("넓은 표 대화").count()) === 0);
    await sep.focus();
    await page.keyboard.press("End");
    await setMock(`as=${accountA}`);
    await switchAccount();
    await page.reload();
    await openPanel();
    check("C9. back to A: A's width 384 restored", (await panelWidth()) === 384 && (await accountId()) === accountA, String(await panelWidth()));
    check("C9. back to A: draft kept", (await chat.getByLabel("메시지").inputValue()) === "폭 조절 중 초안");
    await setMock("as=");
  });
}

check("C10. no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
