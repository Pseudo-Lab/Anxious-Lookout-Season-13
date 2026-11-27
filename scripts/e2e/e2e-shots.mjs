// #119 수용 기준 2·P·B6: 화면 스크린샷(375px·1280px, 밝은/어두운 테마)과 보조 글자 대비 측정(모의 API).
// 실행: E2E_SCRIPTS="e2e-shots.mjs" sh scripts/e2e/run.sh node  → $WORK/shots/119-*.png
// 화면을 "보는" 시험이라 단언은 최소(페이지가 열리고 가로 넘침이 없는지, 보조 글자 대비 4.5:1 이상)만 한다.
import { chromium } from "playwright";
import crypto from "node:crypto";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;
const SHOTS = process.env.SHOTS_DIR;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
const setMock = (q) => fetch(`${ORIGIN}/__mock/set?${q}`).then((r) => r.json());

await setMock("me=normal&session=normal&health=normal&logout=normal&nextLogin=admin&reuse=0&as=&access=approved&codex=fixture&publish=allowed");
const browser = await chromium.launch();

async function apiFor(page) {
  const csrf = async () => (await (await page.request.get(U("/api/auth/session"))).json()).csrfToken;
  return {
    get: async (path) => (await page.request.get(U(`/api${path}`))).json(),
    send: async (method, path, data) => {
      const r = await page.request.fetch(U(`/api${path}`), {
        method,
        data,
        headers: { Origin: ORIGIN, "X-CSRF-Token": await csrf(), "Idempotency-Key": crypto.randomUUID(), "X-Post-Contract-Revision": "1.1" },
      });
      return { status: r.status(), body: await r.json().catch(() => null) };
    },
  };
}

// 준비: 관리자 계정으로 자료·문서·글·대화 하나씩
const setupCtx = await browser.newContext();
const setup = await setupCtx.newPage();
await setup.goto(U("/api/auth/github/start"), { waitUntil: "domcontentloaded" });
await setup.waitForURL(U("/"));
const api = await apiFor(setup);
const mat = (
  await api.send("POST", "/research/materials", {
    title: "화면 점검용 자료 — 한글 줄바꿈이 어절 단위로 되는지 보는 긴 제목",
    sourceUrl: "https://www.example.com/articles/2026/11/very/long/path/for/wrapping/check?utm=1",
    collectedAt: "2026-11-17T14:45:00Z",
    contentKind: "summary",
    content: "자료 본문입니다.",
    summary: "한 줄 요약",
  })
).body;
const doc = (await api.send("POST", "/research/documents", { title: "화면 점검용 문서", content: "## 소제목\n\n본문 **굵게**" })).body;
const para = (t) => ({ id: crypto.randomUUID(), type: "paragraph", runs: [{ text: t, marks: [] }], align: "left" });
const head = (t) => ({ id: crypto.randomUUID(), type: "heading", level: 2, runs: [{ text: t, marks: [] }], align: "left" });
const draft = (
  await api.send("POST", "/research/post-drafts", {
    title: "화면 점검용 글",
    content: { format: "post-blocks", version: 1, blocks: [head("시작하며"), para("가능한가 샌프란시스코 공유합니다. ".repeat(6)), head("둘째"), para("본문"), head("셋째"), para("끝")] },
    description: "",
    tags: ["점검", "UI"],
    coverImageId: null,
    documentIds: [doc?.id].filter(Boolean),
  })
).body;
const committed = draft ? (await api.send("POST", `/research/post-drafts/${draft.id}/commit`, { expectedDraftRevision: draft.revision })).body : null;
const postId = committed?.post?.id;
check("setup. material, document and post created", !!mat?.id && !!doc?.id && !!postId, JSON.stringify({ mat: !!mat?.id, doc: !!doc?.id, postId }));
// 이후 로그인은 같은 계정으로(자료·문서·글이 본인 것이어야 보인다)
const accountId = (await (await setup.request.get(U("/api/auth/session"))).json()).user?.accountId;
await setMock(`as=${accountId}`);
await setupCtx.close();

const PAGES = [
  ["home", "/"],
  ["posts", "/posts/"],
  ["post-detail", "/posts/welcome-to-observatory/"],
  ["tags", "/tags/"],
  ["tag", `/tags/${encodeURIComponent("Docker")}/`],
  ["public", "/public/"],
  ["status", "/status/"],
  ["research-materials", "/research/?tab=materials"],
  ["research-documents", "/research/?tab=documents"],
  ["material", mat?.id ? `/research/material/${mat.id}/` : null],
  ["document", doc?.id ? `/research/document/${doc.id}/` : null],
  ["material-new", "/research/material/new/"],
  ["admin", "/admin/"],
  ["tokens", "/account/tokens/"],
  ["write", "/write/"],
  ["write-new", "/write/new/"],
  ["write-post", postId ? `/write/${postId}/` : null],
].filter(([, p]) => p);

// 보조 글자(작성자·날짜·메타 줄 등 회색 글자)의 대비. 실제 그려진 바탕(투명하지 않은 가장 가까운 조상)과 비교한다.
const CONTRAST = () => {
  const lum = ([r, g, b]) => {
    const f = (c) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  // 계산된 색은 lab()/oklch()로 올 수 있다(리뷰: 정규식으로 읽으면 Lab 성분을 RGB로 착각). 캔버스에 칠한 픽셀의 sRGB와 알파를 읽는다.
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const toRgba = (color) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "rgba(0,0,0,0)";
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    // 반투명 픽셀은 premultiplied에서 되돌린 값이므로 그대로 쓴다
    return [r, g, b, a / 255];
  };
  const over = (top, under) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat(1);
  };
  // 실제 바탕: 페이지 바탕(html/body, 불투명) 위에 조상들의 배경을 바깥부터 차례로 합성한다
  const bgOf = (el) => {
    const layers = [];
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) layers.push(toRgba(getComputedStyle(e).backgroundColor));
    let base = toRgba(getComputedStyle(document.documentElement).backgroundColor);
    if (base[3] < 1) base = over(base, [255, 255, 255, 1]);
    for (let i = layers.length - 1; i >= 0; i--) if (layers[i][3] > 0) base = over(layers[i], base);
    return base;
  };
  const out = [];
  for (const el of document.querySelectorAll("main [class*='text-stone-500'], main [class*='text-stone-600'], footer [class*='text-stone-600']")) {
    if (!el.checkVisibility() || !el.textContent.trim()) continue;
    const bg = bgOf(el);
    const fgRaw = toRgba(getComputedStyle(el).color);
    const fg = fgRaw[3] < 1 ? over(fgRaw, bg) : fgRaw;
    const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    out.push({ text: el.textContent.trim().slice(0, 30), fg: fg.slice(0, 3).map(Math.round), bg: bg.slice(0, 3).map(Math.round), ratio: Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100 });
  }
  return out;
};

for (const scheme of ["light", "dark"]) {
  for (const [vw, vh, tag] of [
    [375, 812, "375"],
    [1280, 900, "1280"],
  ]) {
    const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, colorScheme: scheme, isMobile: vw < 500, hasTouch: vw < 500 });
    const page = await ctx.newPage();
    await page.goto(U("/api/auth/github/start"), { waitUntil: "domcontentloaded" });
    await page.waitForURL(U("/"));
    let worst = null;
    for (const [name, path] of PAGES) {
      await page.goto(U(path), { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(1200);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      check(`${scheme} ${tag} ${name}: no horizontal page scroll`, overflow <= 1, `overflow=${overflow}px`);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/119-${scheme}-${tag}-${name}.png`, fullPage: true }).catch(() => {});
      const ratios = await page.evaluate(CONTRAST);
      for (const r of ratios) if (!worst || r.ratio < worst.ratio) worst = { ...r, page: name };
      const low = ratios.filter((r) => r.ratio < 4.5);
      check(`${scheme} ${tag} ${name}: secondary text ≥ 4.5:1 (B6)`, low.length === 0, low.length ? JSON.stringify(low.slice(0, 3)) : `min ${Math.min(...ratios.map((r) => r.ratio), 99)}`);
    }
    // 대화창(열린 상태)
    await page.goto(U("/"), { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "대화창 열기" }).click().catch(() => {});
    await page.waitForTimeout(1200);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/119-${scheme}-${tag}-chat.png` }).catch(() => {});
    // 계정 메뉴(넓은 화면)·모바일 메뉴(좁은 화면)
    await page.keyboard.press("Escape");
    if (vw < 768) await page.getByRole("button", { name: "메뉴 열기" }).click().catch(() => {});
    else await page.getByRole("button", { name: /계정 메뉴/ }).first().click().catch(() => {});
    await page.waitForTimeout(300);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/119-${scheme}-${tag}-menu.png` }).catch(() => {});
    console.log(`B6 ${scheme} ${tag}: lowest secondary-text contrast ${JSON.stringify(worst)}`);
    await ctx.close();
  }
}

await setMock("as=&reuse=0");
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
