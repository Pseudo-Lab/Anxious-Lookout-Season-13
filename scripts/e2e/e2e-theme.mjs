// #38 흰 바탕 + 시스템 다크 모드. 라이트·다크(prefers-color-scheme) 각각 주요 화면을 열어
// - 바탕이 단색(라이트 흰색, 다크 어두운 색)이고 그라데이션이 없는지,
// - axe-core color-contrast(WCAG AA) 위반이 없는지 확인하고,
// - slice 3: 편집기(#36 선택 툴바·크기 목록·열 폭 손잡이·이미지 폭 손잡이·패널 접기·사용 중 표시)와
//   D-38-1 사용자 색(다크 글자색 밝기 보정, 배경 칸은 어두운 글자, 저장 값 불변, 편집기 = 글 화면 색)을 확인하고,
// - 화면 사진을 SHOTS_DIR에 남긴다(theme-<모드>-<화면>.png).
import { createRequire } from "node:module";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const AXE = require.resolve("axe-core/axe.min.js");
const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const SHOTS = process.env.SHOTS_DIR ?? "/tmp";
const U = (p) => `${ORIGIN}${BASE}${p}`;

await fetch(`${ORIGIN}/__mock/set?me=normal&health=normal&nextLogin=approved&access=approved&codex=fixture`);

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

// 공개 화면(로그인 불필요)
// [이름, 경로, 준비 확인(검사 범위의 요소가 실제로 보이는지)]. 준비되지 않은 화면(오류·불러오는 중)은 검사하지 않고 실패로 센다.
const PUBLIC = [
  ["home", "/", (m) => [m.getByRole("heading", { name: "최근 글" }), m.locator("article").first()]],
  ["posts", "/posts/", (m) => [m.getByRole("heading", { name: "전체 글" }), m.locator("article").first()]],
  ["post", "/posts/welcome-to-observatory/", (m) => [m.locator("article h1").first(), m.locator(".prose p").first()]],
  ["post-table", "/posts/understanding-transformer-architecture/", (m) => [m.locator(".prose table").first(), m.locator(".prose pre").first()]],
  ["tags", "/tags/", (m) => [m.getByRole("heading", { name: "태그" }), m.locator('a[href*="/tags/"]').first()]],
];

// #38 slice2 round1: 준비 실패를 숨기지 않는다. 표시돼야 할 요소가 모두 보이고 "불러오는 중..."·오류 안내가 없어야 검사한다.
async function ready(page, name, scheme, locators) {
  // 리뷰 slice2 round2: 준비 조건 전체(필요한 요소가 모두 보임 + "불러오는 중..." 없음)를 하나의 제한 시간 안에서 기다리고,
  // 그 마지막 상태로 한 번만 판정한다(본문이 먼저 뜨고 버전 기록이 조금 늦는 정상 화면을 실패로 만들지 않는다).
  const main = page.locator("main");
  const deadline = Date.now() + 15000;
  const left = () => Math.max(1, deadline - Date.now());
  for (const l of locators) await l.waitFor({ state: "visible", timeout: left() }).catch(() => {});
  await main.getByText("불러오는 중...").first().waitFor({ state: "detached", timeout: left() }).catch(() => {});
  const missing = [];
  for (const l of locators) if (!(await l.isVisible().catch(() => false))) missing.push(String(l));
  const loading = await main.getByText("불러오는 중...").count();
  const failed = await main.locator('[role="alert"]').filter({ hasText: /못했습니다|HTTP \d{3}/ }).count();
  const ok = missing.length === 0 && loading === 0 && failed === 0;
  check(`T. ${scheme} ${name}: the screen under test is shown (not loading or an error)`, ok, `missing=${missing.join(" ; ")} loading=${loading} error=${failed}`);
  return ok;
}

/** 상대 휘도(sRGB). 계산된 rgb()/rgba()만 받는다. */
function luminance(rgb) {
  const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)/.exec(rgb);
  if (!m) return null;
  const c = [m[1], m[2], m[3]].map((v) => {
    const x = Number(v) / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

async function contrast(page) {
  if (!(await page.evaluate(() => !!window.axe))) await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "rule", values: ["color-contrast"] }, resultTypes: ["violations"] });
    // #38 D-38-1: 사용자가 고른 글자색이 저장 값 그대로 그려진 경우(라이트 모드, 밝은 배경 칸)는 표시만 하고 테마 결함과 나눈다
    const norm = (c) => {
      const g = document.createElement("canvas").getContext("2d");
      g.fillStyle = "#000";
      g.fillStyle = c;
      return g.fillStyle;
    };
    return r.violations.flatMap((v) =>
      v.nodes.map((n) => {
        const el = document.querySelector(n.target[n.target.length - 1]);
        const span = el?.closest(".pb-color");
        const asStored = !!span && norm(getComputedStyle(el).color) === norm(span.style.getPropertyValue("--pb-c"));
        return { target: n.target.join(" "), ratio: n.any[0]?.data?.contrastRatio, fg: n.any[0]?.data?.fgColor, bg: n.any[0]?.data?.bgColor, text: (n.html ?? "").slice(0, 80), asStored };
      })
    );
  });
}

// 공개 글 응답에 YouTube 블록을 더해 영상 카드(어두운 고정 카드)도 검사한다(리뷰 #38 slice1 round1)
const EMBED_POST = "/posts/welcome-to-observatory/";
async function withEmbed(page) {
  await page.route(/\/api\/public\/posts\/welcome-to-observatory\/?(\?|$)/, async (route) => {
    try {
      const res = await route.fetch();
      const body = await res.json();
      const blocks = body?.latestVersion?.content?.blocks ?? body?.content?.blocks;
      if (Array.isArray(blocks)) blocks.push({ id: "00000000-0000-4000-8000-00000000e3b0", type: "embed", provider: "youtube", resourceId: "dQw4w9WgXcQ", caption: [{ text: "영상 설명", marks: [] }] });
      await route.fulfill({ response: res, json: body });
    } catch {
      await route.continue().catch(() => {});
    }
  });
}

const report = (bad) => bad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | ");

// ---- slice 3: 편집기 화면(#36 새 UI 포함)과 D-38-1 사용자 색 ----
// 편집기 도구 막대의 글자색 8종·칸 배경 5종(BlockEditor COLORS·CELL_BACKGROUNDS)
const COLORS = ["#1C1917", "#DC2626", "#EA580C", "#CA8A04", "#16A34A", "#2563EB", "#7C3AED", "#DB2777"];
const BGS = ["#FEF3C7", "#DCFCE7", "#DBEAFE", "#F3E8FF", "#F5F5F4"];
// 리뷰 slice3 round1: 팔레트 밖 배경(계약은 임의 #RRGGBB 허용). 짙은 남색·중간 회색 2종
const CUSTOM_BGS = ["#1E3A8A", "#555555", "#777777"];
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
const uid = () => crypto.randomUUID();
const run = (text, marks = [], link) => (link ? { text, marks, link } : { text, marks });
const para = (runs) => ({ id: uid(), type: "paragraph", runs, align: "left" });
const cellPara = (runs) => ({ id: uid(), type: "paragraph", runs });
function editorContent(imageId) {
  const cell = (runs, background) => ({ id: uid(), blocks: [cellPara(runs)], align: "left", ...(background ? { background } : {}) });
  return {
    format: "post-blocks",
    version: 1,
    blocks: [
      { id: uid(), type: "heading", level: 2, runs: [run("편집기 대비")], align: "left" },
      para([run("선택할 문장입니다")]),
      para(COLORS.map((hex) => run(`색${hex.slice(1)} `, [{ color: hex }]))),
      para([run("크기 18pt", [{ fontSize: 18 }]), run(" 링크", [], "https://example.com/"), run(" 코드", ["code"])]),
      {
        id: uid(),
        type: "table",
        columnWidths: [2, 1, 1],
        rows: [
          { id: uid(), header: true, cells: [cell([run("머리 1")]), cell([run("머리 2")]), cell([run("머리 칸 배경")], BGS[0])] },
          ...[BGS.slice(0, 3), [...BGS.slice(3), undefined], CUSTOM_BGS].map((row) => ({
            id: uid(),
            header: false,
            cells: row.map((bg) => {
              return cell([run(bg ? `칸${bg.slice(1)} ` : "배경 없음 "), run("보라", [{ color: "#7C3AED" }]), run(" 링크", [], "https://example.com/"), run(" 빨강", [{ color: "#DC2626" }]), run(" 코드", ["code"])], bg);
            }),
          })),
        ],
      },
      {
        id: uid(),
        type: "columns",
        columns: [
          { id: uid(), width: 1, blocks: [{ id: uid(), type: "image", imageId, alt: "대비 그림", caption: [run("그림 설명")], widthPercent: 60, align: "center" }] },
          { id: uid(), width: 1, blocks: [para([run("단 글")])] },
        ],
      },
      { id: uid(), type: "quote", blocks: [para([run("인용 글")])] },
      { id: uid(), type: "code", text: "const a = 1;", language: "javascript" },
    ],
  };
}

/** 칠해진 색(계산값)을 sRGB 숫자로. 페이지 안에서 쓴다. */
const PAGE_HELPERS = `
  window.__rgb = (c) => { const g = document.createElement("canvas").getContext("2d"); g.fillStyle = "#000"; g.fillStyle = c; g.fillRect(0, 0, 1, 1); const d = g.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3]]; };
  window.__lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  window.__ratio = (a, b) => { const [x, y] = [window.__lum(a), window.__lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  // 요소 뒤에 실제로 보이는 바탕: 투명하지 않은 배경이 나올 때까지 조상을 올라간다(반투명은 아래 바탕과 섞는다)
  window.__under = (el) => { const stack = []; for (let n = el; n; n = n.parentElement) { const c = window.__rgb(getComputedStyle(n).backgroundColor); if (c[3] > 0) { stack.push(c); if (c[3] === 255) break; } } let out = [255, 255, 255]; const body = window.__rgb(getComputedStyle(document.body).backgroundColor); if (body[3] === 255) out = body.slice(0, 3); for (const c of stack.reverse()) { const a = c[3] / 255; out = out.map((v, i) => Math.round(c[i] * a + v * (1 - a))); } return out; };
  window.__over = (el, prop) => { const c = window.__rgb(getComputedStyle(el)[prop]); const base = window.__under(el.parentElement ?? el); const a = c[3] / 255; return c.slice(0, 3).map((v, i) => Math.round(v * a + base[i] * (1 - a))); };
`;
const helpers = (page) => page.evaluate(PAGE_HELPERS);

/**
 * 화면에 실제로 보이는 대상의 중심 좌표(e2e-blocks stablePoint와 같은 방식): 화면 밖이면 가운데로 옮기고,
 * 그 점의 실제 요소가 대상(또는 그 안)인지 확인한 뒤에만 돌려준다. 맞지 않으면 이유 문자열.
 */
async function stablePoint(p, locator, ok = async () => true) {
  let last = "";
  for (let i = 0; i < 30; i++) {
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
    if (hit && (await ok())) return { x: b.x + b.width / 2, y: b.y + b.height / 2, box: b };
    last = `box=${JSON.stringify(b)} hit=${hit}`;
    await p.waitForTimeout(100);
  }
  return last || "not reachable";
}
async function reach(p, name, locator, ok) {
  const at = await stablePoint(p, locator, ok);
  if (typeof at === "string") {
    check(`${name}: target reachable on screen`, false, at);
    return false;
  }
  await p.mouse.click(at.x, at.y);
  return true;
}

/** 저장 형태에서 글자색과 칸 배경만 모은다(D-38-1: 저장 값 불변 확인용). */
function storedColors(content) {
  const out = [];
  const walk = (v) => {
    if (Array.isArray(v)) return v.forEach(walk);
    if (!v || typeof v !== "object") return;
    if (typeof v.color === "string") out.push(`c${v.color}`);
    if (typeof v.background === "string") out.push(`b${v.background}`);
    Object.values(v).forEach(walk);
  };
  walk(content);
  return out.sort().join(",");
}

/**
 * 화면의 사용자 색. 글자색 span마다 [저장 값, 다크 값, 그려진 색, 배경 칸 안인지, 글자, 칸 다크 값(--pb-cell-cd), 놓인 바탕 위 대비],
 * 배경 칸마다 [저장 값, 그려진 배경, 그려진 기본 글자색, 기본 글자 대비, 링크 최소 대비, 인라인 코드 최소 대비].
 * 글자가 실제로 들어 있는 가장 안쪽 요소(편집기 장식 span이 있으면 그것)의 계산값을 잰다.
 */
const paintedColors = (root) =>
  root.evaluate((r) => {
    const rgb = (el, prop = "color") => window.__rgb(getComputedStyle(el)[prop]).slice(0, 3);
    const ratio = (el) => Number(window.__ratio(rgb(el), window.__under(el)).toFixed(2));
    const min = (els) => (els.length ? Math.min(...els.map(ratio)) : null);
    return {
      text: [...r.querySelectorAll(".pb-color")].map((s) => {
        const inner = s.querySelector(".pb-cell-color") ?? s;
        const cell = s.closest("td, th");
        return [s.style.getPropertyValue("--pb-c").trim(), s.style.getPropertyValue("--pb-cd").trim(), rgb(inner).join(","), !!cell?.style.backgroundColor, s.textContent, getComputedStyle(inner).getPropertyValue("--pb-cell-cd").trim(), ratio(inner)];
      }),
      cells: [...r.querySelectorAll("td, th")]
        .filter((c) => c.style.backgroundColor)
        .map((c) => [c.getAttribute("data-background") ?? "", rgb(c, "backgroundColor").join(","), rgb(c).join(","), Number(window.__ratio(rgb(c), rgb(c, "backgroundColor")).toFixed(2)), min([...c.querySelectorAll("a")]), min([...c.querySelectorAll("code")])]),
    };
  });
const hexRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(",");

/** 접근성 검사 한 번: 테마 결함(저장 값 그대로가 아닌 색)은 실패, 사용자가 고른 색이 저장 값 그대로 그려진 경우는 따로 적는다. */
async function scan(page, label, scheme) {
  await page.screenshot({ path: `${SHOTS}/theme-${label.replace(/[^a-z0-9-]+/gi, "-")}.png`, fullPage: true });
  const bad = await contrast(page);
  // D-38-3: 저장 값 그대로 그린 사용자 색의 예외는 라이트 모드에만 있다. 다크는 모든 위반이 실패다.
  const theme = scheme === "dark" ? bad : bad.filter((b) => !b.asStored);
  const stored = scheme === "dark" ? [] : bad.filter((b) => b.asStored);
  check(`T. ${label}: no WCAG AA color-contrast violations from the theme`, theme.length === 0, report(theme));
  if (stored.length) console.log(`NOTE  ${label}: user colours drawn as stored, below AA on their background (D-38-3 light-mode exception): ${stored.map((b) => `${b.text.match(/>([^<]*)</)?.[1] ?? ""} ${b.ratio} ${b.fg}/${b.bg}`).join(" | ")}`);
  return stored;
}

// 검증용 결함 주입(편집기 화면): nomenu = 선택 툴바를 숨긴다(F1이 실패해야 한다),
// storedcolor = 다크 밝기 보정을 끄고 저장 값으로 그린다(다크 D 검사가 실패해야 한다)
async function injectFault(page) {
  if (FAULT === "nomenu") await page.addStyleTag({ content: '[role="toolbar"][aria-label="선택한 글자 서식"] { display: none !important; }' });
  if (FAULT === "storedcolor") await page.addStyleTag({ content: ".pb-color { color: var(--pb-c) !important; }" });
  // fixedcell = 리뷰 slice3 round1 결함 재현: 칸 글자·링크·칸 안 글자색을 실제 배경과 무관한 고정 값으로(사용자 지정 배경 검사가 실패해야 한다)
  if (FAULT === "fixedcell")
    await page.addStyleTag({ content: ".pb-cell-bg { color: #1c1917 !important; --color-accent: #4f46e5 !important; } .pb-cell-color.pb-color, .pb-cell-color .pb-color, .pb-color .pb-cell-color { color: var(--pb-cd) !important; }" });
}

/** D-38-1·D-38-3 사용자 색 규칙을 그려진 값으로 확인한다(편집기와 글 화면에 같이 쓴다). */
function colourChecks(painted, tag, scheme) {
  const dark = scheme === "dark";
  const outside = painted.text.filter((t) => !t[3]);
  const inCells = painted.text.filter((t) => t[3]);
  // (a) 칸 밖 글자색: 라이트 = 저장 값, 다크 = 밝기 보정 값(--pb-cd)이며 바탕 위 AA
  check(
    `D. ${tag}: text colours outside cells are drawn ${dark ? "with the brightened dark value, AA on the surface" : "as stored"}`,
    outside.length === COLORS.length + 2 && outside.every((t) => t[2] === hexRgb(dark ? t[1] : t[0]) && (!dark || t[6] >= 4.5)) && (!dark || outside.filter((t) => t[0] !== t[1]).length >= 5),
    JSON.stringify(outside.map((t) => [t[0], t[1], t[2], t[6]]))
  );
  // (b) 배경 칸 안 글자색: 라이트 = 저장 값(D-38-3), 다크 = 그 칸 배경 위 AA 값(--pb-cell-cd). 팔레트 밖 배경 포함
  check(
    `D. ${tag}: text colours inside background cells are drawn ${dark ? "with the value computed for that cell's background, AA there" : "as stored"}`,
    inCells.length === 2 * (BGS.length + CUSTOM_BGS.length) && inCells.every((t) => (dark ? t[5] !== "" && t[2] === hexRgb(t[5]) && t[6] >= 4.5 : t[2] === hexRgb(t[0]))),
    JSON.stringify(inCells.map((t) => [t[0], t[5], t[2], t[6]]))
  );
  // (c) 배경 칸: 배경 = 저장 값, 기본 글자·링크·인라인 코드 = 그 배경 위 AA(두 모드)
  check(
    `D. ${tag}: background cells keep the stored background, with AA default text, links and inline code (palette and custom backgrounds)`,
    painted.cells.length === 1 + BGS.length + CUSTOM_BGS.length && painted.cells.every(([hex, bg, , fg, link, code]) => (hex === "" || bg === hexRgb(hex)) && fg >= 4.5 && (link === null || link >= 4.5) && (code === null || code >= 4.5)),
    JSON.stringify(painted.cells)
  );
}

async function editorScreens(page, scheme, send, csrf) {
  const tag = `${scheme} editor`;
  const main = page.locator("main");
  const up = await page.request.fetch(U("/api/research/post-images"), { method: "POST", data: PNG, headers: { "Content-Type": "image/png", Origin: ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": uid() } });
  const image = await up.json().catch(() => null);
  if (!up.ok() || typeof image?.id !== "string") throw new Error(`setup image upload → ${up.status()}`);
  const content = editorContent(image.id);
  const draft = await send("POST", "/research/post-drafts", { title: "대비 편집", content, description: "", tags: [], coverImageId: null, documentIds: [] });
  const want = storedColors(content);

  await page.goto(U(`/write/new/?draft=${draft.id}`), { waitUntil: "domcontentloaded" });
  const editor = main.locator(".ProseMirror");
  const toolbar = main.getByRole("toolbar", { name: "서식", exact: true });
  const panel = main.getByRole("region", { name: "이미지" });
  const img = editor.locator('img[alt="대비 그림"]');
  if (!(await ready(page, "editor", scheme, [editor, toolbar, panel, editor.getByText("선택할 문장입니다"), editor.locator("table td").first(), img, editor.locator(".pb-color").first()]))) return;
  await img.evaluate((el) => (el.complete && el.naturalWidth > 0 ? null : new Promise((ok) => el.addEventListener("load", ok, { once: true })))).catch(() => {});
  await helpers(page);
  await injectFault(page);

  const painted = await paintedColors(editor);
  colourChecks(painted, tag, scheme);
  await scan(page, `${tag}`, scheme);

  // #36 선택 툴바(F1)·크기 목록(F2): 검증된 문장을 눌러 커서를 두고 키보드로 선택한다
  const sentence = editor.locator("p", { hasText: "선택할 문장입니다" });
  const menu = page.getByRole("toolbar", { name: "선택한 글자 서식" });
  // 준비 전제(문장 전체가 선택되고 편집기에 포커스)가 맞을 때만 메뉴를 한 번 판정한다. 전제가 어긋나면(화면 정착 전 입력 등) 준비만 다시 한다.
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  const SENTENCE = "선택할 문장입니다";
  const editorState = () => editor.evaluate((el) => ({ selected: el.editor.state.doc.textBetween(el.editor.state.selection.from, el.editor.state.selection.to), focused: el.editor.isFocused, active: document.activeElement?.tagName }));
  const tries = [];
  let state = null;
  for (let i = 0; i < 3; i++) {
    if (!(await reach(page, `F1. ${tag} selection toolbar`, sentence))) break;
    await page.keyboard.press("End");
    await page.keyboard.press("Shift+Home");
    state = await editorState();
    if (state.selected === SENTENCE && state.focused) break;
    tries.push(state);
    state = null;
  }
  check(`F1. ${tag}: setup — the sentence is selected with the editor focused`, !!state, `retries=${JSON.stringify(tries)}`);
  if (state) {
    const shown = await menu
      .waitFor({ state: "visible", timeout: 5000 })
      .then(() => menu.evaluate((el) => getComputedStyle(el).visibility === "visible"))
      .catch(() => false);
    const bg = shown ? await menu.evaluate((el) => window.__lum(window.__rgb(getComputedStyle(el).backgroundColor))) : null;
    check(`F1. ${tag}: selection toolbar shown with a ${scheme} surface`, shown && (scheme === "light" ? bg > 0.9 : bg < 0.05), `shown=${shown} lum=${bg} ${JSON.stringify(await editorState())}`);
    // 크기 목록(고정 툴바·선택 툴바)과 색 견본: 글자 대비, 견본은 지금 모드에서 글자가 보일 색
    const selects = await page.evaluate(() =>
      [...document.querySelectorAll('select[aria-label="글자 크기"]')].filter((s) => s.checkVisibility()).map((s) => Number(window.__ratio(window.__rgb(getComputedStyle(s).color), window.__under(s)).toFixed(2)))
    );
    check(`F2. ${tag}: font-size lists (toolbar and selection menu) have AA text`, selects.length === 2 && selects.every((r) => r >= 4.5), JSON.stringify(selects));
    const swatches = await toolbar.evaluate((t) => [...t.querySelectorAll(".pb-swatch")].map((b) => [b.getAttribute("aria-label"), window.__rgb(getComputedStyle(b).backgroundColor).slice(0, 3).join(","), b.style.getPropertyValue("--pb-c").trim(), b.style.getPropertyValue("--pb-cd").trim()]));
    check(`D. ${tag}: colour swatches show the colour text will be drawn in`, swatches.length === COLORS.length && swatches.every(([, got, c, cd]) => got === hexRgb(scheme === "dark" ? cd : c)), JSON.stringify(swatches));
    if (shown) await scan(page, `${tag} selection toolbar`, scheme);
  }

  // #36 F6 열 폭 손잡이: 첫 칸 오른쪽 가장자리를 실제로 누르면 안내선이 보이고, 놓으면(움직이지 않음) 폭은 그대로다
  const firstCell = editor.locator("table tr").first().locator("th").first();
  const at = await stablePoint(page, firstCell);
  if (typeof at === "string") check(`F6. ${tag} column edge: target reachable on screen`, false, at);
  else {
    const x = at.box.x + at.box.width - 2;
    const y = at.y;
    const onEdge = await firstCell.evaluate((el, [px, py]) => el.contains(document.elementFromPoint(px, py)) || document.elementFromPoint(px, py) === el, [x, y]);
    await page.mouse.move(x, y);
    const cursor = await editor.evaluate((el) => el.parentElement.classList.contains("pb-col-resize"));
    await page.mouse.down();
    const guide = page.getByTestId("column-resize-guide");
    const guideShown = await guide.isVisible().catch(() => false);
    const ratio = guideShown ? await guide.evaluate((g) => Number(window.__ratio(window.__rgb(getComputedStyle(g).backgroundColor), window.__under(document.querySelector("main .ProseMirror"))).toFixed(2))) : null;
    await page.mouse.up();
    check(`F6. ${tag}: column edge shows the resize cursor and a guide line with ≥3:1 contrast`, onEdge && cursor && guideShown && ratio >= 3, `onEdge=${onEdge} cursor=${cursor} guide=${guideShown} ratio=${ratio}`);
  }

  // #36 F3 이미지 폭 손잡이와 이미지 도구: 손잡이가 바탕과 구별되는지(비텍스트 3:1)
  const handle = editor.getByTestId("image-resize");
  const handleAt = await stablePoint(page, handle);
  if (typeof handleAt === "string") check(`F3. ${tag} image handle: target reachable on screen`, false, handleAt);
  else {
    const r = await handle.evaluate((h) => Number(window.__ratio(window.__over(h, "backgroundColor"), window.__under(h.parentElement)).toFixed(2)));
    check(`F3. ${tag}: image width handle stands out from the surface (≥3:1)`, r >= 3, String(r));
  }

  // #36 F7·F8 이미지 패널: 내 이미지 목록(사용 중 표시)과 접은 상태
  const mine = panel.getByRole("button", { name: "내 이미지", exact: true });
  if (await reach(page, `F8. ${tag} my images`, mine)) {
    const inUse = panel.getByText(/^사용 중\(이 글/).first();
    const listed = await inUse.waitFor({ timeout: 10000 }).then(() => true, () => false);
    check(`F8. ${tag}: my images list shows the in-use label for this post's image`, listed, "");
    if (listed) await scan(page, `${tag} image list`, scheme);
  }
  const fold = panel.getByRole("button", { name: "이미지·표지 접기" });
  if (await reach(page, `F7. ${tag} collapse`, fold)) {
    const folded = await panel.getByRole("button", { name: "이미지·표지 펼치기" }).waitFor({ timeout: 5000 }).then(() => true, () => false);
    check(`F7. ${tag}: image panel collapses`, folded, "");
    if (folded) await scan(page, `${tag} panel collapsed`, scheme);
    const unfold = panel.getByRole("button", { name: "이미지·표지 펼치기" });
    if (folded && (await reach(page, `F7. ${tag} expand`, unfold))) await panel.getByRole("button", { name: "이미지·표지 접기" }).waitFor({ timeout: 5000 }).catch(() => {});
  }

  // 저장 값 불변: 편집기 상태와 서버 초안의 글자색·칸 배경이 처음 보낸 값과 같다
  // 편집기 JSON: textStyle attrs.color, 칸 attrs.background(null은 없음)
  const editorHex = storedColors(await editor.evaluate((el) => el.editor.getJSON()));
  const server = await (await page.request.get(U(`/api/research/post-drafts/${draft.id}`))).json();
  check(`D. ${tag}: stored colours unchanged (server draft)`, storedColors(server.content) === want, storedColors(server.content));
  check(`D. ${tag}: stored colours unchanged (editor state)`, editorHex === want, editorHex);

  // 편집 중 보이는 색 = 공개(renderer) 색: 같은 초안을 커밋해 글 화면에서 같은 저장 값의 그려진 색을 비교한다
  const committed = await send("POST", `/research/post-drafts/${draft.id}/commit`, { expectedDraftRevision: server.revision }, (b) => b?.post?.id);
  await page.goto(U(`/write/?id=${committed.post.id}`), { waitUntil: "domcontentloaded" });
  const article = main.locator(".prose").filter({ has: page.locator(".pb-color") }).first();
  if (!(await ready(page, "editor-render", scheme, [article, article.locator("table td").first()]))) return;
  await helpers(page);
  await injectFault(page);
  const rendered = await paintedColors(article);
  colourChecks(rendered, `${scheme} post view`, scheme);
  // 글 화면 칸에는 data-background가 없으므로 그려진 배경·글자색만 비교한다
  const key = (p) => JSON.stringify({ text: p.text.map((t) => [t[0], t[2], t[3]]), cells: p.cells.map((c) => c.slice(1)) });
  check(`D. ${scheme}: the post view draws user colours exactly as the editor does`, key(rendered) === key(painted), `editor=${key(painted).slice(0, 300)} view=${key(rendered).slice(0, 300)}`);
  await scan(page, `${scheme} post view with user colours`, scheme);
}

// 검증용 결함 주입(로그인 화면): research404 = /api/research GET을 404로(실패해야 한다),
// slowversions = 버전 기록 조회를 3초 늦게(통과해야 한다), hangversions = 버전 기록 조회에 응답하지 않음(실패해야 한다)
const FAULT = process.env.THEME_FAULT ?? "";

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  for (const [name, path, want] of PUBLIC) {
    await page.goto(U(path), { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
    if (!(await ready(page, name, scheme, want(page.locator("main"))))) continue;
    const bg = await page.evaluate(() => {
      const s = getComputedStyle(document.body);
      // 계산값이 oklch() 등일 수 있어 canvas로 sRGB를 얻는다
      const cv = document.createElement("canvas");
      cv.width = cv.height = 1;
      const g = cv.getContext("2d");
      g.fillStyle = s.backgroundColor;
      g.fillRect(0, 0, 1, 1);
      const d = g.getImageData(0, 0, 1, 1).data;
      return { color: `rgb(${d[0]}, ${d[1]}, ${d[2]})`, raw: s.backgroundColor, image: s.backgroundImage };
    });
    const lum = luminance(bg.color);
    check(
      `T. ${scheme} ${name}: solid ${scheme === "light" ? "white" : "dark"} background, no gradient`,
      bg.image === "none" && lum !== null && (scheme === "light" ? lum > 0.95 : lum < 0.05),
      JSON.stringify(bg)
    );
    await page.screenshot({ path: `${SHOTS}/theme-${scheme}-${name}.png`, fullPage: true });
    const bad = await contrast(page);
    check(`T. ${scheme} ${name}: no WCAG AA color-contrast violations`, bad.length === 0, bad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | "));
  }
  // 영상 카드가 있는 글
  await withEmbed(page);
  await page.goto(U(EMBED_POST), { waitUntil: "domcontentloaded" });
  const shown = await ready(page, "post-embed", scheme, [page.getByRole("button", { name: /눌러서 재생/ }), page.locator("main article h1").first()]);
  await page.screenshot({ path: `${SHOTS}/theme-${scheme}-post-embed.png`, fullPage: true });
  const bad = await contrast(page);
  check(`T. ${scheme} post with a YouTube card: card shown, no AA color-contrast violations`, shown && bad.length === 0, `shown=${shown} ${bad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | ")}`);
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // slice 2: 로그인 화면(내 글·글 화면·연구 목록·문서·자료·상태·대화창). 계정마다 자료를 만든다(모의 로그인은 새 계정일 수 있다).
  await page.goto(U("/api/auth/github/start"), { waitUntil: "domcontentloaded" });
  await page.locator("header").getByText("mock-user").first().waitFor({ timeout: 15000 });
  const csrf = (await (await page.request.get(U("/api/auth/me"))).json()).csrfToken;
  // 준비 요청은 성공 상태와 ID를 확인한다(실패하면 예외로 이 시험 전체가 실패한다)
  const send = async (method, path, data, idOf = (b) => b?.id) => {
    const r = await page.request.fetch(U(`/api${path}`), { method, data, headers: { Origin: ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID(), "X-Post-Contract-Revision": "1.1" } });
    const body = await r.json().catch(() => null);
    if (!r.ok() || typeof idOf(body) !== "string") throw new Error(`setup ${method} ${path} → ${r.status()} ${JSON.stringify(body)?.slice(0, 200)}`);
    return body;
  };
  const mat = await send("POST", "/research/materials", { title: "대비 자료", sourceUrl: "https://example.com/m", collectedAt: new Date().toISOString(), contentKind: "summary", content: "자료 본문" });
  const doc = await send("POST", "/research/documents", { title: "대비 문서", content: "# 제목\n\n본문 **굵게**와 `코드`\n\n> 인용" });
  const para = (t) => ({ id: crypto.randomUUID(), type: "paragraph", runs: [{ text: t, marks: [] }], align: "left" });
  const draft = await send("POST", "/research/post-drafts", { title: "대비 글", content: { format: "post-blocks", version: 1, blocks: [para("첫 문단"), para("둘째 문단")] }, description: "요약", tags: ["테마"], coverImageId: null, documentIds: [] });
  const committed = await send("POST", `/research/post-drafts/${draft.id}/commit`, { expectedDraftRevision: draft.revision }, (b) => b?.post?.id);
  const postId = committed.post.id;
  // slowversions: 버전 기록 조회만 3초 늦게(준비 확인이 기다려 통과해야 한다). hangversions: 끝내 응답하지 않음(실패해야 한다).
  if (FAULT === "slowversions" || FAULT === "hangversions")
    await page.route(/\/api\/research\/.*\/versions(\?|$)/, async (r) => {
      if (r.request().method() !== "GET") return r.continue();
      if (FAULT === "hangversions") return; // 응답하지 않는다
      await new Promise((ok) => setTimeout(ok, 3000));
      return r.continue().catch(() => {});
    });
  if (FAULT === "research404") await page.route(/\/api\/research\//, (r) => (r.request().method() === "GET" ? r.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "not_found", message: "x" } }) }) : r.continue()));
  const PRIVATE = [
    ["write", "/write/", (m) => [m.getByRole("heading", { name: "내 글" }), m.getByRole("link", { name: /대비 글/ })]],
    ["write-post", `/write/?id=${postId}`, (m) => [m.getByRole("heading", { name: "대비 글", level: 1 }), m.getByText("첫 문단"), m.getByRole("region", { name: "발행" }), m.getByRole("heading", { name: "버전 기록" })]],
    ["research", "/research/", (m) => [m.getByRole("heading", { name: "내 연구" }), m.getByText("대비 자료").first()]],
    ["document", `/research/document/?id=${doc.id}`, (m) => [m.getByRole("heading", { name: "대비 문서", level: 1 }), m.locator(".prose blockquote"), m.locator(".prose code").first(), m.getByText("관계 추가").first()]],
    ["material", `/research/material/?id=${mat.id}`, (m) => [m.getByRole("heading", { name: "대비 자료", level: 1 }), m.getByText("자료 본문")]],
    ["status", "/status/", (m) => [m.getByRole("heading", { name: "서비스 상태" }), m.getByText("정상 (요청을 받을 준비가 되었습니다)")]],
  ];
  for (const [name, path, want] of PRIVATE) {
    await page.goto(U(path), { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
    if (!(await ready(page, name, scheme, want(page.locator("main"))))) continue;
    if (name === "status") await page.locator("main").getByText("확인 중...").first().waitFor({ state: "detached", timeout: 10000 }).catch(() => {});
    await page.screenshot({ path: `${SHOTS}/theme-${scheme}-${name}.png`, fullPage: true });
    const bad = await contrast(page);
    check(`T. ${scheme} ${name} (signed in): no WCAG AA color-contrast violations`, bad.length === 0, bad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | "));
  }
  // slice 3: 편집기 화면과 D-38-1 사용자 색
  await editorScreens(page, scheme, send, csrf);
  // 대화창
  await page.goto(U("/research/"), { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  const panel = page.locator("#chat-panel");
  if (!(await ready(page, "chat", scheme, [panel, panel.getByRole("heading", { name: "Codex 대화" }), panel.getByRole("button", { name: "새 대화" })]))) {
    await ctx.close();
    continue;
  }
  await page.screenshot({ path: `${SHOTS}/theme-${scheme}-chat.png`, fullPage: false });
  const chatBad = await contrast(page);
  check(`T. ${scheme} chat panel: no WCAG AA color-contrast violations`, chatBad.length === 0, chatBad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | "));
  await ctx.close();
}
await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
