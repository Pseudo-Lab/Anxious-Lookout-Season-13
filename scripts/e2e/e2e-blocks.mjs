// #32 고정 블록 편집기(Tiptap v3)를 실제 글 쓰기 화면(/write/new/)에서 브라우저로 확인한다(시험 전용 화면은 두지 않는다).
// 편집기 내부 상태는 ProseMirror DOM에 붙은 Tiptap 인스턴스(el.editor)로 읽고, 저장 형태는 자동 저장된 서버 초안으로 본다.
// - K*: 한국어 IME. Chromium CDP(Input.imeSetComposition/insertText)로 자모 단위 조합을 흉내 낸다.
//   OS IME 자체를 쓰는 것이 아니므로 실제 기기(macOS Safari·iOS·Android) 수동 확인을 대신하지 않는다.
// - S*: 계약 구조 제한(목록 3단·표 병합 거절·columns·mark 공존·링크 정책·id 유일성·붙여넣기 정리).
// - Y*: YouTube 카드. 편집 화면에서 YouTube 관련 외부 요청이 하나도 없어야 한다.
// - I*: 이미지 붙여넣기·파일 선택 업로드(모의 API).
// - W*: WebKit·Firefox 기본 편집(조합 없는 입력만 가능).
import { chromium, firefox, webkit } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;
const NEW = U("/write/new/");

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
const setMock = (q) => fetch(`${ORIGIN}/__mock/set?${q}`).then((r) => r.json());
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
// WebKit은 페이지를 떠날 때 끊긴 같은 origin 요청(RSC prefetch·상태 조회 등)을 "… due to access control checks." pageerror로 남긴다.
// 탐색 구간(홈 → 글 쓰기 이동) 안에서 생긴 이 형태의 같은 origin 오류만 탐색 오류로 분류한다.
const ORIGIN_HOST = new URL(ORIGIN).host;
const isLeaveCancel = (msg) => {
  const m = /^(\S+) due to access control checks\.?$/.exec(msg);
  return !!m && m[1].replace(/^\/+/, "").startsWith(ORIGIN_HOST);
};
const YT_HOSTS = /(^|\.)(youtube\.com|youtube-nocookie\.com|youtu\.be|ytimg\.com|googlevideo\.com|ggpht\.com)$/;

// ---- 문서 JSON 도우미 ----
function walk(node, fn, parents = []) {
  fn(node, parents);
  for (const c of node.content ?? []) walk(c, fn, [...parents, node]);
}
const textOf = (node) => (node.content ?? []).map((c) => (c.type === "text" ? c.text : c.type === "hardBreak" ? "\n" : textOf(c))).join("");
const blocks = (doc) => (doc?.content ?? []).filter((b) => !(b.type === "paragraph" && !b.content));
function listDepth(node, d = 0) {
  let max = d;
  for (const c of node.content ?? []) max = Math.max(max, listDepth(c, ["bulletList", "orderedList"].includes(c.type) ? d + 1 : d));
  return max;
}

// ---- 한글 자모 분해: 2벌식처럼 초성 → 초성+중성 → 완성 순으로 조합 문자열을 만든다 ----
const CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ";
function steps(ch) {
  const c = ch.charCodeAt(0) - 0xac00;
  if (c < 0 || c > 11171) return [ch];
  const l = Math.floor(c / 588);
  const v = Math.floor((c % 588) / 28);
  const lv = String.fromCharCode(0xac00 + l * 588 + v * 28);
  return c % 28 ? [CHO[l], lv, ch] : [CHO[l], lv];
}

await setMock("me=normal&health=normal&logout=normal&nextLogin=approved&access=approved&codex=fixture&window=&remaining=&runner=&runnerAll=");
// 같은 모의 서버에서 앞서 돈 시험(e2e-write의 의도한 거절 등)과 섞이지 않게 이 시험 시작 시점의 거절 수를 기준으로 센다
const rejectsAtStart = (await fetch(`${ORIGIN}/__mock/counts`).then((r) => r.json())).postRevisionRejects ?? 0;

// 모의 OAuth 시작 주소로 바로 들어가 session을 받는다(로그인 버튼 흐름은 e2e.mjs가 확인한다)
async function login(page) {
  await page.goto(U("/api/auth/github/start"), { waitUntil: "domcontentloaded" });
  await page.locator("header").getByText("mock-user").first().waitFor();
}

function harness(page) {
  const main = page.locator("main");
  const editor = main.locator(".ProseMirror");
  const tb = main.getByRole("toolbar", { name: "서식", exact: true });
  return {
    main,
    editor,
    tb,
    async fresh() {
      // load는 전역 폰트 CDN 응답에 묶여 있어 기다리지 않는다(편집기 표시를 기다린다)
      // 앞 화면의 자동 저장 주소 교체(?draft=)가 진행 중이면 Firefox는 새 탐색을 NS_BINDING_ABORTED로 끊는다. 끊긴 탐색만 다시 한다.
      for (let i = 0; ; i++) {
        try {
          await page.goto(NEW, { waitUntil: "domcontentloaded", timeout: 60000 });
          break;
        } catch (e) {
          if (i >= 2 || !/ABORTED|interrupted by another navigation/i.test(String(e))) throw e;
          await page.waitForTimeout(500);
        }
      }
      await editor.waitFor({ timeout: 15000 });
      await editor.click();
    },
    /** 편집기 내부 JSON(Tiptap). */
    async doc() {
      await page.waitForTimeout(80);
      return editor.evaluate((el) => el.editor.getJSON());
    },
    /** 저장 형태: 자동 저장이 끝난 서버 초안의 post-blocks 본문. problem: 화면의 계약 위반 안내. */
    async blocks() {
      await page.waitForTimeout(300);
      await main.getByText(/^초안 자동 저장됨/).first().waitFor({ timeout: 15000 });
      // 상태 문구가 주소(?draft=) 교체보다 먼저 보일 수 있다(e2e-write와 같은 이유)
      await page.waitForURL(/[?&]draft=/, { timeout: 10000 }).catch(() => {});
      const id = new URL(page.url()).searchParams.get("draft");
      const r = id ? await page.request.get(U(`/api/research/post-drafts/${id}`)) : null;
      const draft = r?.ok() ? await r.json() : null;
      // 본문 계약 위반 안내 등 편집 화면의 경고(role=alert, Next route announcer는 main 밖)
      const problem = await main.locator("[role=alert]").count();
      return { problem, doc: draft?.content ?? null };
    },
  };
}


// ---- #36 B1·B2: 목록 Backspace와 표 삽입 위치. Chromium·WebKit·Firefox에서 같은 순서로 확인한다. ----
// 문서 모양을 짧은 문자열로: p(글), ul[항목,항목[ul[...]]], ol[...], quote{...}, cols{|}, table
function outline(node) {
  const kids = (n) => (n.content ?? []).map(outline).join(",");
  switch (node.type) {
    case "doc":
      return (node.content ?? []).filter((b) => !(b.type === "paragraph" && !b.content)).map(outline).join(",");
    case "paragraph":
      return `p(${textOf(node)})`;
    case "bulletList":
    case "orderedList":
      return `${node.type === "bulletList" ? "ul" : "ol"}[${(node.content ?? []).map((it) => textOf(it.content?.[0] ?? {}) + (it.content?.[1] ? `[${outline(it.content[1])}]` : "")).join(",")}]`;
    case "blockquote":
      return `quote{${kids(node)}}`;
    case "columns":
      return `cols{${(node.content ?? []).map(kids).join("|")}}`;
    default:
      return node.type;
  }
}
/**
 * 선택 메뉴처럼 화면에 고정된(스크롤을 따라 다시 놓이는) 요소를 누른다. Playwright의 자동 scroll-into-view와 메뉴 재배치가
 * 맞물려 "outside of the viewport"로 맴도는 일을 피하려고, 화면 안에서 그 점의 실제 요소가 대상인지 확인한 뒤 그 좌표를 누른다.
 * ok()가 참이 될 때까지(화면 정착) 다시 잰다. 끝내 맞지 않으면 누르지 않고 이유를 돌려준다(리뷰 full round7).
 * stablePoint는 그 점만 돌려준다(끌기 같은 다른 마우스 동작용, 리뷰 full round8 F3).
 */
async function stablePoint(p, locator, ok = async () => true) {
  let last = "";
  for (let i = 0; i < 30; i++) {
    // 화면 밖이면 가운데로 옮긴다(Playwright click이 하던 스크롤을 대신한다). 화면에 고정된 메뉴는 그대로 둔다.
    await locator
      .evaluate((el) => {
        const r = el.getBoundingClientRect();
        if (r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) el.scrollIntoView({ block: "center", inline: "nearest" });
      })
      .catch(() => {});
    const b = await locator.boundingBox().catch(() => null);
    const hit =
      b &&
      (await locator
        .evaluate((el, [x, y]) => x >= 0 && y >= 0 && x <= innerWidth && y <= innerHeight && (document.elementFromPoint(x, y) === el || el.contains(document.elementFromPoint(x, y))), [b.x + b.width / 2, b.y + b.height / 2])
        .catch(() => false));
    if (hit && (await ok())) return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    last = `box=${JSON.stringify(b)} hit=${hit}`;
    await p.waitForTimeout(100);
  }
  return last || "not reachable";
}
async function stableClick(p, locator, ok = async () => true) {
  const at = await stablePoint(p, locator, ok);
  if (typeof at === "string") return at;
  await p.mouse.click(at.x, at.y);
  return null;
}

/** stableClick을 하고, 누르지 못했으면 그 이유를 실패로 남긴다. 구간은 false면 멈춘다(후속 입력이 본문에 새지 않게). */
async function reach(p, name, locator, ok) {
  const why = await stableClick(p, locator, ok);
  if (why) check(`${name}: target reachable on screen`, false, why);
  return !why;
}

async function editingRegressions(p, h, tag) {
  const shape = async () => outline(await h.doc());
  const kb = p.keyboard;
  // B1a. 빈 마지막 항목: Backspace 1번은 항목 해제(문단), 2번은 앞 항목 끝에 합침. 표식이 새로 생기지 않는다
  await h.fresh();
  await kb.type("- a");
  await kb.press("Enter");
  await kb.press("Backspace");
  const once = await shape();
  await kb.press("Backspace");
  await kb.type("Z");
  check(`B1a. ${tag}: empty last item → paragraph → joins the previous item (no new marker)`, once === "ul[a]" && (await shape()) === "ul[aZ]", `${once} → ${await shape()}`);
  // B1b. 번호 목록 마지막 항목
  await h.fresh();
  await kb.type("1. a");
  await kb.press("Enter");
  await kb.type("b");
  await kb.press("Enter");
  await kb.press("Backspace");
  await kb.press("Backspace");
  await kb.type("Z");
  check(`B1b. ${tag}: ordered list, same two-step Backspace`, (await shape()) === "ol[a,bZ]", await shape());
  // B1c. 가운데 항목 첫 글자에서: 해제하면 목록이 나뉘고, 다시 Backspace면 앞 항목에 합치며 목록이 다시 하나가 된다
  await h.fresh();
  await kb.type("- a");
  await kb.press("Enter");
  await kb.type("b");
  await kb.press("Enter");
  await kb.type("c");
  await kb.press("ArrowUp");
  await kb.press("Home");
  // 브라우저가 옮긴 caret을 편집기가 읽을 때까지 기다린다(selectionchange는 비동기)
  const caret = () => h.editor.evaluate((el) => `${el.editor.state.selection.$from.parent.textContent}@${el.editor.state.selection.$from.parentOffset}`);
  // 무거운 실행에서 Chromium이 Home을 놓치는 일이 있어(caret이 줄 끝에 남음) 줄 처음이 될 때까지 Home을 다시 누른다
  for (let i = 0; i < 20 && (await caret()) !== "b@0"; i++) {
    await p.waitForTimeout(50);
    if (i % 5 === 4) await kb.press("Home");
  }
  const at = await caret();
  await kb.press("Backspace");
  const split = await shape();
  await kb.press("Backspace");
  check(`B1c. ${tag}: middle item lifts out, then joins back into one list`, split === "ul[a],p(b),ul[c]" && (await shape()) === "ul[ab,c]", `caret=${at} ${split} → ${await shape()}`);
  // B1d. 중첩 항목: 한 단계씩 올라간 뒤 문단이 되고, 마지막에는 가장 깊은 마지막 항목에 합친다
  await h.fresh();
  await kb.type("- a");
  await kb.press("Enter");
  await kb.press("Tab");
  await kb.type("b");
  await kb.press("Enter");
  const steps = [];
  for (let i = 0; i < 3; i++) {
    await kb.press("Backspace");
    steps.push(await shape());
  }
  await kb.type("Z");
  check(`B1d. ${tag}: nested empty item outdents, becomes a paragraph, then joins the deepest last item`,
    steps.join(" / ") === "ul[a[ul[b]],] / ul[a[ul[b]]] / ul[a[ul[b]]]" && (await shape()) === "ul[a[ul[bZ]]]", `${steps.join(" / ")} → ${await shape()}`);
  // B1e. 단 안 목록
  await h.fresh();
  await h.tb.getByRole("combobox", { name: "단 나누기" }).selectOption({ label: "1:1" });
  await kb.type("- a");
  await kb.press("Enter");
  await kb.press("Backspace");
  await kb.press("Backspace");
  await kb.type("Z");
  check(`B1e. ${tag}: list inside a column behaves the same`, (await shape()) === "cols{ul[aZ]|p()}", await shape());
  // B1f. 인용 안 목록
  await h.fresh();
  await kb.type("> ");
  await kb.type("- a");
  await kb.press("Enter");
  await kb.press("Backspace");
  await kb.press("Backspace");
  await kb.type("Z");
  check(`B1f. ${tag}: list inside a quote behaves the same`, (await shape()) === "quote{ul[aZ]}", await shape());
  // B1g. 저장도 계약을 통과한다
  const saved = await h.blocks();
  check(`B1g. ${tag}: result saves as valid post-blocks`, saved.problem === 0 && saved.doc?.blocks?.[0]?.type === "quote", `problem=${saved.problem}`);

  // F1k. 리뷰 full round6: 크기 목록을 실제로 눌러 연 뒤 키보드로 고르는 동안 포커스가 목록에 남고(본문으로 새지 않고),
  //      Enter로 끝내면 고른 크기(18pt)가 선택한 단어에만 있고 본문에 새 문단이 생기지 않는다. 선택 메뉴와 고정 툴바 모두.
  for (const where of ["menu", "toolbar"]) {
    await h.fresh();
    await kb.type("hello world");
    // 리뷰 full round7: 자동 저장·주소(?draft=) 교체가 끝난 뒤에 줄을 화면 가운데로 옮기고 선택한다(그 사이 스크롤·좌표가 바뀌지 않게)
    await h.blocks();
    await h.editor.getByText("hello world").evaluate((el) => el.scrollIntoView({ block: "center" }));
    await h.editor.getByText("hello world").click();
    // 단어 선택은 키보드로 한다(브라우저마다 더블클릭 단어 판정이 다르다). 마우스를 누르지 않았으므로 선택 메뉴가 뜬다.
    await kb.press("End");
    for (let i = 0; i < 5; i++) await kb.press("Shift+ArrowLeft");
    const selected = () => h.editor.evaluate((el) => el.editor.state.doc.textBetween(el.editor.state.selection.from, el.editor.state.selection.to));
    const scope = where === "menu" ? h.main.getByRole("toolbar", { name: "선택한 글자 서식" }) : h.tb;
    await scope.waitFor({ timeout: 5000 }).catch(() => {});
    // 누르기 직전 확인: 목록 가운데가 화면 안이고, 그 점의 실제 요소가 바로 그 목록이며, 선택이 "world"다.
    const why = await stableClick(p, scope.getByLabel("글자 크기"), async () => (await selected()) === "world");
    if (why) {
      check(`F1k. ${tag} ${where}: size list reachable at a stable on-screen point with "world" selected`, false, `selected=${await selected()} ${why}`);
      continue;
    }
    const steps = [];
    for (let i = 0; i < 6; i++) {
      await kb.press("ArrowDown");
      steps.push(await p.evaluate(() => document.activeElement?.tagName));
    }
    await kb.press("Enter");
    await p.waitForTimeout(300);
    const d = await h.doc();
    const runs = d.content?.[0]?.content ?? [];
    const w = runs.find((n) => n.text === "world");
    const size = w?.marks?.find((m) => m.type === "textStyle")?.attrs?.fontSize ?? null;
    const paragraphs = (d.content ?? []).filter((b) => b.type === "paragraph").length;
    const back = await p.evaluate(() => document.activeElement?.classList.contains("ProseMirror"));
    check(
      `F1k. ${tag} ${where}: keyboard size choice keeps focus in the list, applies 18pt to the word only, Enter adds no paragraph and returns to the editor`,
      steps.length === 6 && steps.every((x) => x === "SELECT") && size === 18 && !runs.find((n) => n.text === "hello ")?.marks?.length && paragraphs === 1 && back,
      `active=${steps.join(",")} size=${size} paragraphs=${paragraphs} back=${back}`
    );
  }

  // B2. 표는 커서가 있는 블록 뒤에 들어간다(문서 맨 위가 아니다)
  await h.fresh();
  await kb.type("one");
  await kb.press("Enter");
  await kb.type("two");
  await kb.press("Enter");
  await kb.type("three");
  await h.editor.getByText("two", { exact: true }).click();
  await kb.press("End");
  await h.tb.getByRole("button", { name: "표 넣기" }).click();
  await kb.type("cell");
  const mid = await shape();
  check(`B2a. ${tag}: table goes right after the block with the cursor`, mid === "p(one),p(two),table,p(three)", mid);
  const firstCell = (await h.doc()).content.find((b) => b.type === "table")?.content?.[0]?.content?.[0];
  check(`B2a. ${tag}: cursor lands in the first cell`, textOf(firstCell ?? {}) === "cell", textOf(firstCell ?? {}));
  await h.editor.getByText("three", { exact: true }).click();
  await kb.press("End");
  await h.tb.getByRole("button", { name: "표 넣기" }).click();
  check(`B2b. ${tag}: table at the last block goes after it`, (await shape()) === "p(one),p(two),table,p(three),table", await shape());
  await h.editor.getByText("one", { exact: true }).click();
  await kb.press("Home");
  await h.tb.getByRole("button", { name: "표 넣기" }).click();
  check(`B2c. ${tag}: cursor at the start of a block still inserts after that block`, (await shape()) === "p(one),table,p(two),table,p(three),table", await shape());
  const b2 = await h.blocks();
  check(`B2d. ${tag}: saved as valid post-blocks`, b2.problem === 0 && (b2.doc?.blocks ?? []).filter((b) => b.type === "table").length === 3, `problem=${b2.problem}`);
}

// ======================= Chromium =======================
const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();
const pageErrors = [];
const external = [];
page.on("pageerror", (e) => pageErrors.push(e.message));
page.on("request", (r) => {
  const host = new URL(r.url()).hostname;
  if (YT_HOSTS.test(host)) external.push(r.url());
});
let nextPrompt = [];
page.on("dialog", (d) => {
  if (d.type() === "prompt") void d.accept(nextPrompt.shift() ?? "");
  else void d.accept();
});
const H = harness(page);
const cdp = await context.newCDPSession(page);
const comp = (text) => cdp.send("Input.imeSetComposition", { text, selectionStart: text.length, selectionEnd: text.length });
const commit = (text) => cdp.send("Input.insertText", { text });
/**
 * 조합 중에 Enter·Tab 같은 키를 누를 때 OS IME가 보내는 순서를 흉내 낸다:
 * keydown(keyCode 229, isComposing) → 조합 확정(compositionend) → 실제 키(keydown 13 등).
 * CDP로 키만 보내면 조합이 확정되지 않은 채 남는데, 실제 IME에서는 생기지 않는 상태라 쓰지 않는다.
 */
async function imeKey(composing, key) {
  await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Process", code: key, windowsVirtualKeyCode: 229, nativeVirtualKeyCode: 229 });
  await commit(composing);
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Process", code: key, windowsVirtualKeyCode: 229, nativeVirtualKeyCode: 229 });
  await page.keyboard.press(key);
}
/** 단어를 자모 단위로 조합한다. leaveLast면 마지막 음절을 조합 중인 채로 둔다. */
async function typeKo(word, { leaveLast = false } = {}) {
  const chars = [...word];
  for (const [i, ch] of chars.entries()) {
    if (ch === " ") {
      await commit(" ");
      continue;
    }
    for (const s of steps(ch)) await comp(s);
    if (!(leaveLast && i === chars.length - 1)) await commit(ch);
  }
}

await login(page);
await page.goto(NEW);
check("L0. new post page renders the block editor and toolbar", (await H.editor.waitFor({ timeout: 15000 }).then(() => true, () => false)) && (await H.tb.isVisible()));

// K1. 기본 조합
await H.fresh();
await typeKo("안녕하세요 반갑습니다");
let d = await H.doc();
check("K1. composed Korean text is kept exactly", textOf(blocks(d)[0] ?? {}) === "안녕하세요 반갑습니다", JSON.stringify(textOf(blocks(d)[0] ?? {})));

// K2. 받침 이동(안 → 아+니): 조합 중이던 음절이 바뀌어 확정되고 다음 음절 조합이 시작된다
await H.fresh();
for (const s of ["ㅇ", "아", "안"]) await comp(s);
await commit("아");
for (const s of ["ㄴ", "니"]) await comp(s);
await commit("니");
d = await H.doc();
check("K2. final consonant moving to next syllable (안→아니)", textOf(blocks(d)[0] ?? {}) === "아니", JSON.stringify(textOf(blocks(d)[0] ?? {})));

// K3a. 조합 확정 직후 Enter(Chrome 순서: compositionend → keydown Enter)
await H.fresh();
await typeKo("첫줄");
await page.keyboard.press("Enter");
await typeKo("둘째");
d = await H.doc();
check("K3a. Enter right after commit splits paragraph without losing the last syllable", blocks(d).map(textOf).join("|") === "첫줄|둘째", blocks(d).map(textOf).join("|"));

// K3b. 조합 중 Enter(tiptap#4108 경로): 마지막 음절이 조합 중일 때 Enter
await H.fresh();
await typeKo("한글", { leaveLast: true });
await imeKey("글", "Enter");
await typeKo("다음");
d = await H.doc();
const k3b = blocks(d).map(textOf).join("|");
check("K3b. Enter while composing keeps the composing syllable (tiptap#4108 path)", k3b === "한글|다음", JSON.stringify(k3b));

// K4. 조합 중 Backspace(IME 안에서 자모 삭제) → 조합 취소, 확정 뒤 Backspace는 음절 단위
await H.fresh();
await typeKo("가나");
for (const s of ["ㄷ", "다", "닭"]) await comp(s);
for (const s of ["달", "다", "ㄷ"]) await comp(s);
await comp("");
d = await H.doc();
const k4a = textOf(blocks(d)[0] ?? {});
await page.keyboard.press("Backspace");
d = await H.doc();
check("K4. jamo backspace inside composition and cancel leave committed text intact; Backspace removes one syllable", k4a === "가나" && textOf(blocks(d)[0] ?? {}) === "가", `${k4a} → ${textOf(blocks(d)[0] ?? {})}`);

// K5. 줄 처음 stored mark(prosemirror#1458): 빈 문단에서 굵게 켜고 조합
await H.fresh();
await page.keyboard.press("Control+b");
await typeKo("굵게");
d = await H.doc();
const k5 = blocks(d)[0]?.content ?? [];
check("K5. stored bold mark at line start applies to composed text, no duplication", k5.length === 1 && k5[0].text === "굵게" && k5[0].marks?.some((m) => m.type === "bold"), JSON.stringify(k5));

// K6. mark 경계: 굵게 끄고 이어서 조합
await page.keyboard.press("Control+b");
await typeKo("보통");
d = await H.doc();
const k6 = blocks(d)[0]?.content ?? [];
check("K6. mark boundary: bold off then composing creates a plain run", k6.length === 2 && k6[1].text === "보통" && !(k6[1].marks ?? []).length, JSON.stringify(k6));

// K7. 목록: "- " 입력 규칙, 조합, Enter로 다음 항목, Tab 들여쓰기
await H.fresh();
await page.keyboard.type("- ");
await typeKo("항목");
await page.keyboard.press("Enter");
await page.keyboard.press("Tab");
await typeKo("하위", { leaveLast: true });
await imeKey("위", "Enter");
await typeKo("셋째");
d = await H.doc();
const list = blocks(d)[0];
const items = [];
walk(list ?? {}, (n) => n.type === "listItem" && items.push(textOf(n.content?.[0] ?? {})));
check("K7. list input rule + composing + Enter + Tab nesting", list?.type === "bulletList" && listDepth(d) === 2 && items.join("|") === "항목|하위|셋째", `${list?.type} depth=${listDepth(d)} items=${items.join("|")}`);

// K8. 표 칸 안에서 조합 + Tab으로 다음 칸
await H.fresh();
await H.tb.getByRole("button", { name: "표 넣기" }).click();
await typeKo("머리");
await page.keyboard.press("Tab");
await typeKo("칸", { leaveLast: true });
await imeKey("칸", "Tab");
await typeKo("셋");
d = await H.doc();
const cells = [];
walk(d, (n) => (n.type === "tableCell" || n.type === "tableHeader") && cells.push(textOf(n)));
check("K8. composing in table cells, Tab to next cell while composing", cells.slice(0, 3).join("|") === "머리|칸|셋", cells.slice(0, 3).join("|"));

// K9. Shift+Enter는 문단 안 줄바꿈(hardBreak = 계약 run.text의 LF)
await H.fresh();
await typeKo("윗줄");
await page.keyboard.press("Shift+Enter");
await typeKo("아랫줄");
d = await H.doc();
check("K9. Shift+Enter inserts an in-paragraph line break", blocks(d).length === 1 && textOf(blocks(d)[0]) === "윗줄\n아랫줄", JSON.stringify(textOf(blocks(d)[0] ?? {})));

// K10. 굵게 안에서 넣은 줄바꿈은 굵은 run 안에 남는다(줄바꿈 노드가 넣는 위치의 marks를 받는다, 리뷰 M3)
await H.fresh();
await page.keyboard.press("Control+b");
await typeKo("윗줄");
await page.keyboard.press("Shift+Enter");
await typeKo("아랫줄");
{
  const b = await H.blocks();
  const runs = b.doc?.blocks?.[0]?.runs ?? [];
  check("K10. Shift+Enter inside bold keeps one bold run with LF (saved draft)", runs.length === 1 && runs[0].text === "윗줄\n아랫줄" && runs[0].marks.join() === "bold", JSON.stringify(runs));
}

// S1. 목록 4단계 이상은 거절(조용히 고치지 않고 안내)
await H.fresh();
await page.keyboard.type("- a");
for (const t of ["b", "c", "d"]) {
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await page.keyboard.type(t);
}
d = await H.doc();
check("S1. list nesting stops at 3 levels with a notice", listDepth(d) === 3 && (await H.main.getByText("목록은 3단계까지만").count()) === 1, `depth=${listDepth(d)}`);

// S2. 단 나누기 2:1, 단 안에서는 표 넣기 불가
await H.fresh();
await H.tb.getByRole("combobox", { name: "단 나누기" }).selectOption({ label: "2:1" });
await typeKo("왼쪽 단");
d = await H.doc();
const cols = blocks(d).find((b) => b.type === "columns");
check("S2. columns 2:1 inserted, text goes into first column", cols?.content?.map((c) => c.attrs.width).join(":") === "2:1" && textOf(cols.content[0]) === "왼쪽 단", JSON.stringify(cols?.content?.map((c) => c.attrs)));
check("S2. table insert disabled inside columns", await H.tb.getByRole("button", { name: "표 넣기" }).isDisabled());
await H.main.getByRole("group", { name: "단" }).getByRole("button", { name: "단 비율 1:2" }).click();
d = await H.doc();
check("S2. column ratio preset changes widths", blocks(d).find((b) => b.type === "columns")?.content?.map((c) => c.attrs.width).join(":") === "1:2");

// 붙여넣기 도우미: HTML/텍스트를 ClipboardEvent로 넣는다
async function pasteHtml(html, text = "") {
  await page.evaluate(
    ([h, t]) => {
      const dt = new DataTransfer();
      if (h) dt.setData("text/html", h);
      dt.setData("text/plain", t);
      document.querySelector(".ProseMirror").dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    },
    [html, text]
  );
}

// S3. 병합 칸이 있는 표 붙여넣기는 거절
await H.fresh();
await pasteHtml('<table><tr><td colspan="2">합친 칸</td></tr><tr><td>a</td><td>b</td></tr></table>');
d = await H.doc();
check("S3. pasting a table with merged cells is rejected with a notice", !blocks(d).some((b) => b.type === "table") && (await H.main.getByText("표 칸 병합은 지원하지 않습니다").count()) === 1, JSON.stringify(blocks(d).map((b) => b.type)));

// S4. 외부 HTML 붙여넣기: iframe·외부 img·script·style 색은 들어오지 않는다
await H.fresh();
await pasteHtml('<p>앞<img src="https://example.com/x.png">뒤</p><iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe><script>window.__x=1</script><p><span style="color: rgb(220, 38, 38); font-size: 40px">빨강</span> <span style="color: red">이름색</span></p>');
d = await H.doc();
const types = new Set();
const colors = [];
walk(d, (n) => {
  types.add(n.type);
  for (const m of n.marks ?? []) if (m.type === "textStyle") colors.push(m.attrs.color);
});
check("S4. paste drops iframe/external img/script; rgb() color normalized to hex, named color dropped", !types.has("embed") && !types.has("postImage") && !(await page.evaluate(() => window.__x)) && colors.filter(Boolean).join(",") === "#DC2626", `types=${[...types]} colors=${JSON.stringify(colors)}`);

// S5. code mark와 bold 공존, 글자 색
await H.fresh();
await page.keyboard.type("코드굵게");
await page.keyboard.press("Shift+Home");
// 브라우저 선택을 편집기가 읽은 뒤에 누른다(selectionchange는 비동기라 무거운 실행에서 늦게 반영된다)
await H.editor.evaluate((el) => new Promise((ok) => { const t0 = Date.now(); const tick = () => (!el.editor.state.selection.empty || Date.now() - t0 > 3000 ? ok() : setTimeout(tick, 30)); tick(); }));
await H.tb.getByRole("button", { name: "굵게" }).click();
await H.tb.getByRole("button", { name: "인라인 코드" }).click();
await H.tb.getByRole("button", { name: "글자 색 #2563EB" }).click();
d = await H.doc();
const marks = (blocks(d)[0]?.content?.[0]?.marks ?? []).map((m) => (m.type === "textStyle" ? `color:${m.attrs.color}` : m.type)).sort().join(",");
check("S5. code mark coexists with bold and color", marks === "bold,code,color:#2563EB", marks);

// S6. 링크 정책: javascript: 거절, https 허용
await H.fresh();
await page.keyboard.type("링크");
await page.keyboard.press("Shift+Home");
nextPrompt = ["javascript:alert(1)"];
await H.tb.getByRole("button", { name: "링크", exact: true }).click();
d = await H.doc();
const noLink = !(blocks(d)[0]?.content?.[0]?.marks ?? []).some((m) => m.type === "link");
nextPrompt = ["https://example.com/a"];
await H.tb.getByRole("button", { name: "링크", exact: true }).click();
d = await H.doc();
const link = (blocks(d)[0]?.content?.[0]?.marks ?? []).find((m) => m.type === "link");
check("S6. javascript: link refused, https link set", noLink && link?.attrs?.href === "https://example.com/a", JSON.stringify(link?.attrs));

// S7. 블록 id: 모든 블록 노드에 UUID, 같은 id를 가진 HTML을 붙여 넣으면 새 id로 바뀐다
await H.fresh();
await pasteHtml('<p data-id="11111111-1111-4111-8111-111111111111">하나</p><p data-id="11111111-1111-4111-8111-111111111111">둘</p><ul><li><p>항목</p></li></ul>');
d = await H.doc();
const ids = [];
let missing = 0;
walk(d, (n) => {
  if (n.type === "doc" || n.type === "text" || n.type === "hardBreak") return;
  if (n.attrs?.id) ids.push(n.attrs.id);
  else missing++;
});
const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
check("S7. every block node has a unique UUID; duplicated pasted ids are reissued", missing === 0 && new Set(ids).size === ids.length && ids.every((i) => uuidRe.test(i) || i === "11111111-1111-4111-8111-111111111111"), `ids=${ids.length} unique=${new Set(ids).size} missing=${missing}`);

// S8. 블록 이동(위/아래 버튼) 후 id 유지
await H.fresh();
await page.keyboard.type("A");
await page.keyboard.press("Enter");
await page.keyboard.type("B");
d = await H.doc();
const beforeIds = blocks(d).map((b) => b.attrs.id);
await H.tb.getByRole("button", { name: "블록 위로" }).click();
d = await H.doc();
check("S8. move block up swaps order and keeps ids", blocks(d).map(textOf).join("") === "BA" && blocks(d).map((b) => b.attrs.id).join() === [...beforeIds].reverse().join(), blocks(d).map(textOf).join(""));

// S9. 표: 첫 행 머리글 토글, 칸 정렬은 cell.align, 칸 배경
await H.fresh();
await H.tb.getByRole("button", { name: "표 넣기" }).click();
await H.tb.getByRole("button", { name: "가운데 정렬" }).click();
await H.main.getByRole("group", { name: "칸 배경" }).getByRole("button", { name: "칸 배경 #DBEAFE" }).click();
d = await H.doc();
const table = blocks(d).find((b) => b.type === "table");
const firstCell = table?.content?.[0]?.content?.[0];
check("S9. table: header row, cell align/background on the cell (paragraph align untouched)",
  firstCell?.type === "tableHeader" && firstCell.attrs.align === "center" && firstCell.attrs.background === "#DBEAFE" && (firstCell.content?.[0]?.attrs?.textAlign ?? "left") === "left",
  JSON.stringify(firstCell?.attrs));

// Y1. YouTube: 주소에서 ID만(시작 시각 버림), 편집 화면 카드는 재생하지 않음
await H.fresh();
nextPrompt = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"];
await H.tb.getByRole("button", { name: "YouTube 영상" }).click();
d = await H.doc();
const embed = blocks(d).find((b) => b.type === "embed");
check("Y1. YouTube URL becomes embed with 11-char id only (t= dropped)", embed?.attrs?.resourceId === "dQw4w9WgXcQ" && embed.attrs.provider === "youtube", JSON.stringify(embed?.attrs));
check("Y1. editor card has no iframe and says it does not play", (await H.editor.locator("iframe").count()) === 0 && (await H.main.getByText("편집 화면에서는 재생하지 않습니다").count()) === 1);
await page.keyboard.type("캡션");
d = await H.doc();
check("Y1. caption text goes into the embed node", textOf(blocks(d).find((b) => b.type === "embed") ?? {}) === "캡션", JSON.stringify(blocks(d).map(textOf)));

// Y2. YouTube 주소를 붙여 넣어도 embed로 자동 승격하지 않는다
await H.fresh();
await pasteHtml("", "https://youtu.be/dQw4w9WgXcQ");
d = await H.doc();
check("Y2. pasted YouTube URL is not auto-promoted to an embed", !blocks(d).some((b) => b.type === "embed") && textOf(blocks(d)[0] ?? {}).includes("youtu.be"), JSON.stringify(blocks(d).map((b) => b.type)));

// Y3. 알 수 없는 주소는 거절
await H.fresh();
nextPrompt = ["https://vimeo.com/123"];
await H.tb.getByRole("button", { name: "YouTube 영상" }).click();
d = await H.doc();
check("Y3. non-YouTube URL refused", !blocks(d).some((b) => b.type === "embed"));

// I1. 파일 선택 업로드 → imageId로 블록 삽입, 자체 이미지 주소만
await H.fresh();
await H.main.getByLabel("본문 이미지 파일").setInputFiles({ name: "사진.png", mimeType: "image/png", buffer: PNG });
await H.editor.locator("figure[data-type=post-image] img").first().waitFor({ timeout: 10000 }).catch(() => {});
d = await H.doc();
const img = blocks(d).find((b) => b.type === "postImage");
const src = await H.editor.locator("figure[data-type=post-image] img").first().getAttribute("src").catch(() => null);
check("I1. picked image uploads and inserts by imageId with alt from file name", /^[0-9a-f-]{36}$/.test(img?.attrs?.imageId ?? "") && img.attrs.alt === "사진" && src === `${BASE}/api/media/post-images/${img.attrs.imageId}`, JSON.stringify(img?.attrs));

// F3. 이미지 폭: % 칸(반올림·10..100 제한)과 가장자리 손잡이 끌기. 끄는 동안이 아니라 놓을 때 한 번 저장한다
{
  const widthOf = async () => blocks(await H.doc()).find((b) => b.type === "postImage")?.attrs?.widthPercent;
  const box = H.editor.getByLabel("이미지 폭(%)");
  const setWidth = async (v) => {
    await box.fill(v);
    await box.press("Enter");
  };
  await setWidth("37.4");
  const typed = await widthOf();
  await setWidth("5");
  const low = await widthOf();
  await setWidth("250");
  const high = await widthOf();
  check("F3. width box rounds and clamps to 10..100", typed === 37 && low === 10 && high === 100, `${typed} ${low} ${high}`);
  // 리뷰 full round8: 자동 저장·주소 교체가 끝난 뒤 이미지를 화면 가운데로 옮기고, 손잡이가 화면 안에서 실제로 잡히는 점에서만 끈다
  await H.blocks();
  await H.editor.locator("figure[data-type=post-image]").first().evaluate((el) => el.scrollIntoView({ block: "center" }));
  const handle = H.editor.getByTestId("image-resize");
  const at = await stablePoint(page, handle);
  if (typeof at === "string") {
    check("F3. resize handle reachable on screen", false, at);
  } else {
    await page.mouse.move(at.x, at.y);
    await page.mouse.down();
    await page.mouse.move(at.x - 60, at.y, { steps: 5 });
    const during = await widthOf();
    await page.mouse.move(at.x - 120, at.y, { steps: 5 });
    await page.mouse.up();
    const dragged = await widthOf();
    check("F3. dragging the handle changes the width once, on release, as an integer %", during === 100 && Number.isInteger(dragged) && dragged >= 10 && dragged < 100, `during=${during} after=${dragged}`);
  }
  const saved = await H.blocks();
  check("F3. saved image width equals the editor value", saved.problem === 0 && saved.doc?.blocks?.find((b) => b.type === "image")?.widthPercent === (await widthOf()), JSON.stringify(saved.doc?.blocks?.map((b) => b.widthPercent)));
}

// F4. 이미지 블록 위/아래 이동(블록 화면의 버튼). id는 그대로, 저장 순서도 같다
// 리뷰 full round8·PM: 모든 F 구간은 자동 저장·주소 교체 뒤, 화면에서 실제로 잡히는 대상만 누르고, 못 누르면 그 구간을 멈춘다
F4: {
  await H.fresh();
  await page.keyboard.type("first");
  await page.keyboard.press("Enter");
  await page.keyboard.type("second");
  await H.main.getByLabel("본문 이미지 파일").setInputFiles({ name: "옮길.png", mimeType: "image/png", buffer: PNG });
  await H.editor.locator("figure[data-type=post-image] img").first().waitFor({ timeout: 10000 }).catch(() => {});
  await H.blocks();
  const order = async () => outline(await H.doc());
  const start = await order();
  const imgId = async () => (await H.doc()).content.find((b) => b.type === "postImage")?.attrs?.id;
  const id0 = await imgId();
  const up = H.editor.getByRole("button", { name: "이미지 위로" });
  const dn = H.editor.getByRole("button", { name: "이미지 아래로" });
  if (!(await reach(page, "F4. up 1", up))) break F4;
  const up1 = await order();
  if (!(await reach(page, "F4. up 2", up))) break F4;
  const up2 = await order();
  if (!(await reach(page, "F4. up 3 (at the top)", up))) break F4;
  const stay = await order();
  if (!(await reach(page, "F4. down", dn))) break F4;
  const down = await order();
  check("F4. image moves up/down one block at a time and stops at the top", start === "p(first),p(second),postImage" && up1 === "p(first),postImage,p(second)" && up2 === "postImage,p(first),p(second)" && stay === up2 && down === up1, [start, up1, up2, stay, down].join(" / "));
  check("F4. the moved image keeps its id", (await imgId()) === id0);
  const saved = await H.blocks();
  check("F4. saved order matches the editor", saved.problem === 0 && (saved.doc?.blocks ?? []).map((b) => b.type).join(",") === "paragraph,image,paragraph", JSON.stringify(saved.doc?.blocks?.map((b) => b.type)));
}

// F5. 단 안 이미지: 단 안에서 파일로 올리거나 이미지 패널에서 넣으면 그 단 안에 이미지 블록이 들어가고, 계약대로 저장된다
F5: {
  await H.fresh();
  await H.tb.getByRole("combobox", { name: "단 나누기" }).selectOption({ label: "1:1" });
  await page.keyboard.type("left");
  check("F5. image upload is available inside columns", !(await H.tb.getByRole("button", { name: "이미지 올리기" }).isDisabled()));
  await H.main.getByLabel("본문 이미지 파일").setInputFiles({ name: "왼쪽.png", mimeType: "image/png", buffer: PNG });
  await H.editor.locator("figure[data-type=post-image] img").first().waitFor({ timeout: 10000 }).catch(() => {});
  await H.blocks();
  if (!(await reach(page, "F5. right column", H.editor.locator(".pb-column").nth(1)))) break F5;
  await page.keyboard.type("right");
  // 이미지 패널에서 올리면 본문 커서 위치(오른쪽 단)에 넣는다
  await H.main.getByRole("region", { name: "이미지" }).getByLabel("이미지 파일").setInputFiles({ name: "오른쪽.png", mimeType: "image/png", buffer: PNG });
  await H.editor.locator("figure[data-type=post-image]").nth(1).waitFor({ timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(300);
  const cols = (await H.doc()).content.find((b) => b.type === "columns");
  const kinds = (cols?.content ?? []).map((c) => (c.content ?? []).filter((b) => !(b.type === "paragraph" && !b.content)).map((b) => b.type).join("+"));
  check("F5. picked file goes into the left column, panel upload into the right column", kinds.join(" | ") === "paragraph+postImage | paragraph+postImage", kinds.join(" | "));
  const saved = await H.blocks();
  const sc = saved.doc?.blocks?.find((b) => b.type === "columns");
  check("F5. saved as columns > column > image (depth 3)", saved.problem === 0 && sc?.columns?.every((c) => c.blocks.some((b) => b.type === "image")), JSON.stringify(sc?.columns?.map((c) => c.blocks.map((b) => b.type))));
}

// I2. 이미지 파일 붙여넣기
await H.fresh();
await page.evaluate((b64) => {
  const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bin], "붙여넣기.png", { type: "image/png" }));
  document.querySelector(".ProseMirror").dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
}, PNG.toString("base64"));
await H.editor.locator("figure[data-type=post-image] img").first().waitFor({ timeout: 10000 }).catch(() => {});
d = await H.doc();
check("I2. pasted image file uploads and inserts", blocks(d).some((b) => b.type === "postImage"), JSON.stringify(blocks(d).map((b) => b.type)));
let hasBlob = false;
walk(d, (n) => {
  if (JSON.stringify(n.attrs ?? {}).match(/blob:|data:/)) hasBlob = true;
});
check("I2. no blob:/data: URL in the document", !hasBlob);

// I3. GIF는 올리지 않는다
await H.fresh();
await H.main.getByLabel("본문 이미지 파일").setInputFiles({ name: "a.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a") });
check("I3. unsupported image type refused with a notice", (await H.main.getByText("PNG, JPEG, WebP 이미지만").count()) === 1);

// A1. 여러 블록을 섞은 실제 편집 결과가 post-blocks v1 검사를 통과한다(adapter)
await H.fresh();
await typeKo("첫 문단");
await page.keyboard.press("Shift+Enter");
await typeKo("둘째 줄");
await page.keyboard.press("Enter");
await page.keyboard.type("- ");
await typeKo("항목");
await page.keyboard.press("Enter");
await page.keyboard.press("Tab");
await typeKo("하위");
await page.keyboard.press("Enter");
await page.keyboard.press("Enter");
await page.keyboard.press("Enter");
await H.tb.getByRole("button", { name: "표 넣기" }).click();
await typeKo("칸");
await H.tb.getByRole("button", { name: "오른쪽 정렬" }).click();
const a1 = await H.blocks();
const a1types = (a1.doc?.blocks ?? []).map((b) => b.type).join(",");
const a1cell = a1.doc?.blocks?.find((b) => b.type === "table")?.rows?.[0];
check("A1. mixed editor output is saved as valid post-blocks (LF run, nested list, header row, cell align)",
  a1.problem === 0 && a1.doc?.format === "post-blocks" && a1.doc.blocks[0].runs?.[0]?.text === "첫 문단\n둘째 줄" && a1types.startsWith("paragraph,list") && a1types.includes("table") &&
    a1cell?.header === true && a1cell.cells[0].align === "right" && !("align" in a1cell.cells[0].blocks[0]),
  `problem=${a1.problem} types=${a1types}`);

check("Y*. no request to YouTube/ytimg/googlevideo hosts during editing", external.length === 0, external.slice(0, 3).join(" "));

await editingRegressions(page, H, "chromium");

// G1·G2. #36 계약 revision 선언: 모든 본문 저장이 X-Post-Contract-Revision을 보냈다. 서버가 더 새 revision을 요구하면(409)
// 자동 저장을 멈추고 새로고침을 안내하며, 저장·닫기를 막고 입력은 화면에 남긴다(초안을 만들지 않는다).
{
  const counts = () => fetch(`${ORIGIN}/__mock/counts`).then((r) => r.json());
  check("G1. every body save so far declared revision 1.1 (no gate rejection)", (await counts()).postRevisionRejects === rejectsAtStart, `${rejectsAtStart} → ${JSON.stringify(await counts())}`);
  await setMock("postRevision=1.2");
  try {
    await H.fresh();
    await page.keyboard.type("gate test");
    await H.main.getByText("새 버전의 편집기가 있습니다", { exact: false }).waitFor({ timeout: 15000 }).catch(() => {});
    check("G2. 409 upgrade: refresh notice shown", (await H.main.getByText("새 버전의 편집기가 있습니다", { exact: false }).count()) === 1);
    check("G2. 409 upgrade: save and close are blocked, refresh offered", (await H.main.getByRole("button", { name: "글 저장" }).isDisabled()) && (await H.main.getByRole("button", { name: "새로고침" }).count()) === 1);
    check("G2. 409 upgrade: input stays, no draft was created", textOf((await H.doc()).content?.[0] ?? {}) === "gate test" && !new URL(page.url()).searchParams.get("draft"), page.url());
    check("G2. 409 upgrade: the server saw the rejected write", (await counts()).postRevisionRejects > rejectsAtStart);
  } finally {
    await setMock("postRevision=1.1");
  }
}
// F1·F2. 선택 툴바: 글자를 선택하면 선택 영역 위에 서식 메뉴가 뜨고, 굵게·글자 크기(pt)가 선택한 글자에만 적용된다.
// 커서를 옮겨 선택이 풀리면 사라진다. 손가락 입력(모바일)에서는 띄우지 않고 고정 툴바의 글자 크기를 쓴다.
F1: {
  await H.fresh();
  await page.keyboard.type("hello world");
  const menu = H.main.getByRole("toolbar", { name: "선택한 글자 서식" });
  check("F1. no selection menu without a selection", (await menu.count()) === 0);
  // 리뷰 full round8: F1k와 같은 준비. 자동 저장·주소(?draft=) 교체가 끝난 뒤 보이는 본문에서 선택한다
  await H.blocks();
  const selectedWord = () => H.editor.evaluate((el) => el.editor.state.doc.textBetween(el.editor.state.selection.from, el.editor.state.selection.to));
  // 고정(sticky) 툴바 아래에 가리지 않게 줄을 화면 가운데로 옮긴 뒤 누른다
  await H.editor.getByText("hello world").evaluate((el) => el.scrollIntoView({ block: "center" }));
  // 문단 상자는 편집기 폭 전체라 글자 자체("world")의 위치를 Range로 잰다
  const word = await H.editor.getByText("hello world").evaluate((el) => {
    const t = el.firstChild;
    const r = document.createRange();
    r.setStart(t, 6);
    r.setEnd(t, 11);
    const b = r.getBoundingClientRect();
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  });
  await page.mouse.dblclick(word.x, word.y);
  await menu.waitFor({ timeout: 5000 }).catch(() => {});
  const selRect = await page.evaluate(() => {
    const r = window.getSelection().getRangeAt(0).getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, text: window.getSelection().toString() };
  });
  const mb = await menu.boundingBox().catch(() => null);
  check("F1. double-click selection shows the menu right above the selection", selRect.text === "world" && !!mb && mb.y + mb.height <= selRect.top + 1 && mb.y + mb.height >= selRect.top - 40, `${JSON.stringify(selRect)} ${JSON.stringify(mb)}`);
  const boldWhy = await stableClick(page, menu.getByRole("button", { name: "굵게" }), async () => (await selectedWord()) === "world");
  check("F1. menu bold reachable with \"world\" selected", boldWhy === null, boldWhy ?? "");
  // 리뷰 round2 M2: 실제로 목록을 눌러 포커스가 메뉴로 옮겨 가도 메뉴·선택이 남고, 키보드로 고른 크기가 선택한 글자에 적용된다
  const size = menu.getByLabel("글자 크기");
  const sizeWhy = boldWhy ?? (await stableClick(page, size, async () => (await selectedWord()) === "world"));
  check("F1. menu size list reachable with \"world\" selected", sizeWhy === null, `${sizeWhy ?? ""} selected=${await selectedWord()}`);
  // 누르지 못했으면 이어서 키를 보내지 않는다(본문에 키가 들어가 뒤 검사가 연쇄로 틀리지 않게)
  if (sizeWhy) break F1;
  check("F1. clicking the size list keeps the menu open", (await menu.count()) === 1);
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  const runs = (await H.doc()).content[0].content;
  const w = runs.find((n) => n.text === "world");
  const h = runs.find((n) => n.text === "hello ");
  check("F1·F2. bold and 18pt apply to the selected word only", !!w && w.marks?.some((m) => m.type === "bold") && w.marks?.some((m) => m.type === "textStyle" && m.attrs.fontSize === 18) && !h?.marks?.length, JSON.stringify(runs));
  check("F1. the menu stays while the text is still selected", (await menu.count()) === 1);
  // 크기를 고르면 편집기가 다음 frame에 포커스를 되찾는다. 그 뒤에 커서를 옮긴다
  await page.waitForFunction(() => document.activeElement?.classList.contains("ProseMirror"), null, { timeout: 5000 }).catch(() => {});
  await page.keyboard.press("ArrowRight");
  await menu.waitFor({ state: "detached", timeout: 3000 }).catch(() => {});
  check("F1. collapsing the selection hides the menu", (await menu.count()) === 0, JSON.stringify(await page.evaluate(() => ({ active: `${document.activeElement?.tagName}.${String(document.activeElement?.className).slice(0, 30)}`, sel: String(getSelection()), pm: document.querySelector("main .ProseMirror").editor.state.selection.toJSON() }))));
  const saved = await H.blocks();
  const sr = saved.doc?.blocks?.[0]?.runs ?? [];
  check("F2. saved run carries the contract mark {fontSize: 18} after bold", saved.problem === 0 && JSON.stringify(sr) === JSON.stringify([{ text: "hello ", marks: [] }, { text: "world", marks: ["bold", { fontSize: 18 }] }]), JSON.stringify(sr));
  check("F2. editor shows 18pt as 24px", (await H.editor.locator('[data-font-size="18"]').evaluate((el) => getComputedStyle(el).fontSize).catch(() => "")) === "24px");

  const mctx = await browser.newContext({ isMobile: true, hasTouch: true, viewport: { width: 390, height: 844 } });
  const mp = await mctx.newPage();
  // 좁은 화면에서는 계정 이름이 메뉴 안에 숨어 있어 session 응답으로 로그인을 확인한다
  await mp.goto(U("/api/auth/github/start"), { waitUntil: "domcontentloaded" });
  for (let i = 0; i < 50; i++) {
    const me = await (await mp.request.get(U("/api/auth/me"))).json().catch(() => null);
    if (me?.user) break;
    await mp.waitForTimeout(200);
  }
  const mh = harness(mp);
  await mh.fresh();
  await mp.keyboard.type("touch text");
  await mp.keyboard.press("Shift+Home");
  await mp.waitForTimeout(300);
  check("F1. touch device: no floating menu, the fixed toolbar keeps font size", (await mh.main.getByRole("toolbar", { name: "선택한 글자 서식" }).count()) === 0 && (await mh.tb.getByLabel("글자 크기").count()) === 1);
  await mctx.close();
}

// F6. 표 열 폭: 넓게·좁게 버튼과 테두리 끌기는 열 가중치(columnWidths)로 저장되고, 편집기·공개 화면 모두 그 비율의 고정 배치로 그린다.
// 열을 넣고 빼면 가중치 길이도 맞춰지고, "같게"는 가중치를 없앤다(균등 = 생략).
F6: {
  await H.fresh();
  await H.tb.getByRole("button", { name: "표 넣기" }).click();
  await page.keyboard.type("A");
  await H.blocks();
  const tableAttr = async () => (await H.doc()).content.find((b) => b.type === "table")?.attrs?.columnWidths ?? null;
  const t = H.tb.getByRole("group", { name: "표" });
  const inTable = async () => H.editor.evaluate((el) => el.editor.isActive("table"));
  if (!(await reach(page, "F6. widen 1", t.getByRole("button", { name: "열 넓게" }), inTable))) break F6;
  if (!(await reach(page, "F6. widen 2", t.getByRole("button", { name: "열 넓게" }), inTable))) break F6;
  const widened = await tableAttr();
  check("F6. widening the cursor column twice stores integer weights for 3 columns", JSON.stringify(widened) === "[156,100,100]", JSON.stringify(widened));
  const pct = await H.editor.locator("table colgroup col").evaluateAll((cs) => cs.map((c) => c.style.width));
  check("F6. editor draws the weights as colgroup percentages", pct.length === 3 && Math.abs(parseFloat(pct[0]) - 43.82) < 0.1, JSON.stringify(pct));
  // 열 추가(커서 열 오른쪽, 같은 가중치) → 열 삭제
  if (!(await reach(page, "F6. add column", t.getByRole("button", { name: "오른쪽에 열 추가" }), inTable))) break F6;
  const added = await tableAttr();
  if (!(await reach(page, "F6. delete column", t.getByRole("button", { name: "열 삭제" }), inTable))) break F6;
  const removed = await tableAttr();
  // 커서가 있던 첫 열("A")을 지웠으므로 남은 칸에 다시 쓴다(빈 본문은 저장할 수 없다)
  await page.keyboard.type("B");
  check("F6. adding/removing a column keeps weights in step", JSON.stringify(added) === "[156,156,100,100]" && JSON.stringify(removed) === "[156,100,100]", `${JSON.stringify(added)} ${JSON.stringify(removed)}`);
  // 테두리 끌기: 첫 칸 오른쪽 가장자리를 오른쪽으로 끌면 첫 열이 넓어지고 둘째 열이 좁아진다(표 폭 그대로)
  await H.blocks();
  const cell = H.editor.locator("table tr").first().locator("th, td").first();
  await cell.evaluate((el) => el.scrollIntoView({ block: "center" }));
  // 첫 칸 오른쪽 가장자리 점이 화면 안이고 그 칸에 잡힐 때까지 다시 잰다
  let edge = null;
  for (let i = 0; i < 30 && !edge; i++) {
    edge = await cell.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const x = r.right - 2;
      const y = r.top + r.height / 2;
      const inView = x >= 0 && y >= 0 && x <= innerWidth && y <= innerHeight;
      return inView && el.contains(document.elementFromPoint(x, y)) ? { x, y } : null;
    });
    if (!edge) await page.waitForTimeout(100);
  }
  if (!edge) {
    check("F6. column border reachable on screen", false, JSON.stringify(await cell.boundingBox()));
    break F6;
  }
  await page.mouse.move(edge.x, edge.y);
  await page.mouse.down();
  await page.mouse.move(edge.x + 62, edge.y, { steps: 6 });
  const during = await tableAttr();
  const guide = await page.getByTestId("column-resize-guide").count();
  await page.mouse.up();
  const dragged = await tableAttr();
  check("F6. dragging a column border shows a guide, saves once on release and shifts width to the left column", guide === 1 && (await page.getByTestId("column-resize-guide").count()) === 0 && JSON.stringify(during) === "[156,100,100]" && !!dragged && dragged[0] > 438 && dragged[1] < 281 && Math.abs(dragged[2] - 281) <= 2, `${JSON.stringify(during)} → ${JSON.stringify(dragged)}`);
  const saved = await H.blocks();
  const sw = saved.doc?.blocks?.find((b) => b.type === "table")?.columnWidths;
  check("F6. saved table carries canonical columnWidths", saved.problem === 0 && Array.isArray(sw) && sw.length === 3, JSON.stringify(sw));
  // 공개 renderer: 같은 비율의 고정 배치
  await H.main.getByLabel("제목").fill("열 폭 글");
  await H.main.getByRole("button", { name: "글 저장" }).click();
  const committed = await page.waitForURL(/\/write\/\?id=/, { timeout: 30000, waitUntil: "commit" }).then(() => true, () => false);
  check("F6. the post with weighted columns is saved", committed, committed ? "" : (await H.main.innerText()).slice(0, 300).replace(/\s+/g, " "));
  const art = H.main.locator(".prose").first();
  await art.locator("table").first().waitFor({ timeout: 15000 });
  const rendered = await art.locator("table").first().evaluate((tb) => ({ cls: tb.className, cols: [...tb.querySelectorAll("col")].map((c) => c.style.width) }));
  const sum = sw.reduce((a, b) => a + b, 0);
  check("F6. renderer uses a fixed layout with the same column percentages", rendered.cls.includes("pb-weighted") && rendered.cols.length === 3 && Math.abs(parseFloat(rendered.cols[0]) - (100 * sw[0]) / sum) < 0.01, JSON.stringify(rendered));
  // 같게: 가중치를 없앤다
  await H.main.getByRole("button", { name: "내용 수정" }).click();
  await H.editor.waitFor({ timeout: 15000 });
  if (!(await reach(page, "F6. a table cell", H.editor.locator("table td, table th").first()))) break F6;
  if (!(await reach(page, "F6. even", H.tb.getByRole("group", { name: "표" }).getByRole("button", { name: "열 폭 같게" }), inTable))) break F6;
  check("F6. 'even' removes the weights (equal = omitted)", (await tableAttr()) === null);
  await H.main.getByRole("button", { name: "초안 버리기…" }).click();
  await H.main.getByRole("button", { name: "초안 버리기", exact: true }).click();
}

// F6b. 리뷰 round4 M3: 정규화된 작은 가중치([1,2,2])의 표를 저장·새로고침한 뒤에도 넓게·좁게가 양방향으로 바뀐다
F6b: {
  const csrf = (await (await page.request.get(U("/api/auth/me"))).json()).csrfToken;
  const id = () => crypto.randomUUID();
  const cell = (t) => ({ id: id(), align: "left", blocks: [{ id: id(), type: "paragraph", runs: t ? [{ text: t, marks: [] }] : [] }] });
  const table = { id: id(), type: "table", columnWidths: [1, 2, 2], rows: [{ id: id(), header: true, cells: [cell("a"), cell("b"), cell("c")] }, { id: id(), header: false, cells: [cell("1"), cell("2"), cell("3")] }] };
  const r = await page.request.post(U("/api/research/post-drafts"), {
    data: { title: "작은 가중치", content: { format: "post-blocks", version: 1, blocks: [table] }, description: "", tags: [], coverImageId: null, documentIds: [] },
    headers: { Origin: ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID(), "X-Post-Contract-Revision": "1.1" },
  });
  const draft = await r.json();
  await page.goto(U(`/write/new/?draft=${draft.id}`), { waitUntil: "domcontentloaded" });
  await page.reload({ waitUntil: "domcontentloaded" });
  await H.editor.waitFor({ timeout: 15000 });
  const w = async () => (await H.doc()).content.find((b) => b.type === "table")?.attrs?.columnWidths ?? null;
  const ratio = (x) => (x ? x[0] / x[1] : null);
  const start = await w();
  if (!(await reach(page, "F6b. header cell", H.editor.locator("table th").first()))) break F6b;
  const t = H.tb.getByRole("group", { name: "표" });
  const inTable = async () => H.editor.evaluate((el) => el.editor.isActive("table"));
  if (!(await reach(page, "F6b. widen", t.getByRole("button", { name: "열 넓게" }), inTable))) break F6b;
  const wide = await w();
  if (!(await reach(page, "F6b. narrow 1", t.getByRole("button", { name: "열 좁게" }), inTable))) break F6b;
  if (!(await reach(page, "F6b. narrow 2", t.getByRole("button", { name: "열 좁게" }), inTable))) break F6b;
  const narrow = await w();
  check("F6b. small canonical weights [1,2,2]: widen and narrow both change the first column", JSON.stringify(start) === "[1,2,2]" && ratio(wide) > 0.5 && ratio(narrow) < 0.5 && wide[1] === wide[2] && narrow[1] === narrow[2], `${JSON.stringify(start)} → ${JSON.stringify(wide)} → ${JSON.stringify(narrow)}`);
  const saved = await H.blocks();
  const sw = saved.doc?.blocks?.[0]?.columnWidths;
  check("F6b. the changed weights save as valid canonical weights", saved.problem === 0 && Array.isArray(sw) && sw[0] / sw[1] < 0.5, JSON.stringify(sw));
}

// R1. renderer(저장한 글 화면의 최신 버전): 서식·영상 카드. 누르기 전에는 YouTube 요청 0, 누르면 nocookie 주소의 고정 sandbox iframe
await H.fresh();
await page.keyboard.type("굵은 ");
await page.keyboard.press("Shift+Home");
await H.tb.getByRole("button", { name: "굵게" }).click();
await H.tb.getByLabel("글자 크기").selectOption("18");
await page.keyboard.press("End");
nextPrompt = ["https://youtu.be/dQw4w9WgXcQ"];
await H.tb.getByRole("button", { name: "YouTube 영상" }).click();
await H.main.getByLabel("제목").fill("영상 글");
await H.main.getByRole("button", { name: "글 저장" }).click();
await page.waitForURL(/\/write\/\?id=/, { timeout: 15000 });
const preview = H.main.locator(".prose").first();
await preview.waitFor({ timeout: 15000 });
check("R1. saved post renders bold as <strong> and the inactive YouTube card", (await preview.locator("strong").first().innerText().catch(() => "")).trim() === "굵은" && (await preview.getByRole("button", { name: /눌러서 재생/ }).count()) === 1);
check("R1. no YouTube request before activation (editor + rendered post)", external.length === 0, external.slice(0, 3).join(" "));
check("F2. renderer draws 18pt as exactly 24px", (await preview.locator("strong span").first().evaluate((el) => el.style.fontSize).catch(() => "")) === "24px");
const firstYt = page.waitForRequest((r) => YT_HOSTS.test(new URL(r.url()).hostname), { timeout: 5000 }).catch(() => null);
await preview.getByRole("button", { name: /눌러서 재생/ }).click();
const frame = preview.locator("iframe");
const fsrc = await frame.getAttribute("src").catch(() => null);
const fsandbox = await frame.getAttribute("sandbox").catch(() => null);
check("R1. activation opens youtube-nocookie iframe with the validated id and fixed sandbox", fsrc === "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1" && fsandbox === "allow-scripts allow-same-origin allow-presentation", `${fsrc} | ${fsandbox}`);
const first = await firstYt;
check("R1. the first YouTube-side request after activation is the nocookie embed", first?.url() === fsrc, first?.url() ?? "(none)");

check("no page errors (chromium)", pageErrors.length === 0, pageErrors.slice(0, 3).join(" | "));
await browser.close();

// ======================= WebKit · Firefox: 조합 없는 기본 편집 =======================
for (const [name, type] of [["webkit", webkit], ["firefox", firefox]]) {
  const b = await type.launch();
  const ctx = await b.newContext();
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push({ msg: e.message, at: Date.now() }));
  p.on("dialog", (d) => void d.accept());
  const h = harness(p);
  await login(p);
  // 안정화: 로그인 뒤 홈의 prefetch·상태 조회가 잦아든 뒤에 글 쓰기 화면으로 옮긴다
  await p.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  const leaveStart = Date.now();
  await h.fresh();
  const leaveEnd = Date.now();
  // 진단: 탐색 구간 안의 같은 origin 끊긴 요청 오류만 탐색 오류로 정보 출력한다. 그 밖의 오류는 단계와 관계없이 실패로 센다.
  const inLeave = (e) => e.at >= leaveStart && e.at <= leaveEnd && isLeaveCancel(e.msg);
  const navigation = errs.filter(inLeave).map((e) => e.msg);
  const before = errs.filter((e) => !inLeave(e)).map((e) => e.msg);
  if (navigation.length) console.log(`INFO  ${name}: ${navigation.length} same-origin request(s) cut off while leaving home: ${navigation.slice(0, 2).join(" | ")}`);
  check(`W. ${name}: no non-navigation page errors before the editor`, before.length === 0, before.slice(0, 2).join(" | "));
  errs.length = 0;
  await p.keyboard.insertText("한글 입력");
  await p.keyboard.press("Enter");
  await p.keyboard.type("- ");
  await p.keyboard.insertText("항목");
  await p.keyboard.press("Enter");
  await p.keyboard.press("Tab");
  await p.keyboard.insertText("하위");
  const dd = await h.doc();
  const bs = blocks(dd);
  check(`W. ${name}: Korean insertText, paragraph split, list nesting`, textOf(bs[0] ?? {}) === "한글 입력" && bs[1]?.type === "bulletList" && listDepth(dd) === 2, `${bs.map((x) => x.type).join(",")} depth=${listDepth(dd)}`);
  check(`W. ${name}: no page errors while editing`, errs.length === 0, errs.slice(0, 2).map((e) => e.msg).join(" | "));
  // 목록·표 회귀는 화면을 여러 번 새로 연다. 그때 끊긴 같은 origin 요청(WebKit)은 탐색 오류로 따로 센다.
  errs.length = 0;
  await editingRegressions(p, h, name);
  const cut = errs.filter((e) => isLeaveCancel(e.msg));
  const other = errs.filter((e) => !isLeaveCancel(e.msg));
  if (cut.length) console.log(`INFO  ${name}: ${cut.length} same-origin request(s) cut off by re-opening the editor`);
  check(`W. ${name}: no non-navigation page errors in list/table regressions`, other.length === 0, other.slice(0, 2).map((e) => e.msg).join(" | "));
  await b.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
