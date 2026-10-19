// #38 흰 바탕 + 시스템 다크 모드. 라이트·다크(prefers-color-scheme) 각각 주요 화면을 열어
// - 바탕이 단색(라이트 흰색, 다크 어두운 색)이고 그라데이션이 없는지,
// - axe-core color-contrast(WCAG AA) 위반이 없는지 확인하고,
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
const PUBLIC = [
  ["home", "/"],
  ["posts", "/posts/"],
  ["post", "/posts/welcome-to-observatory/"],
  ["post-table", "/posts/understanding-transformer-architecture/"],
  ["tags", "/tags/"],
];

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
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "rule", values: ["color-contrast"] }, resultTypes: ["violations"] });
    return r.violations.flatMap((v) =>
      v.nodes.map((n) => ({ target: n.target.join(" "), ratio: n.any[0]?.data?.contrastRatio, fg: n.any[0]?.data?.fgColor, bg: n.any[0]?.data?.bgColor, text: (n.html ?? "").slice(0, 80) }))
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

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  for (const [name, path] of PUBLIC) {
    await page.goto(U(path), { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
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
  const card = page.getByRole("button", { name: /눌러서 재생/ });
  const shown = await card.waitFor({ timeout: 10000 }).then(() => true, () => false);
  await page.screenshot({ path: `${SHOTS}/theme-${scheme}-post-embed.png`, fullPage: true });
  const bad = await contrast(page);
  check(`T. ${scheme} post with a YouTube card: card shown, no AA color-contrast violations`, shown && bad.length === 0, `shown=${shown} ${bad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | ")}`);
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // slice 2: 로그인 화면(내 글·글 화면·연구 목록·문서·자료·상태·대화창). 계정마다 자료를 만든다(모의 로그인은 새 계정일 수 있다).
  await page.goto(U("/api/auth/github/start"), { waitUntil: "domcontentloaded" });
  await page.locator("header").getByText("mock-user").first().waitFor({ timeout: 15000 });
  const csrf = (await (await page.request.get(U("/api/auth/me"))).json()).csrfToken;
  const send = async (method, path, data) =>
    (await page.request.fetch(U(`/api${path}`), { method, data, headers: { Origin: ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID(), "X-Post-Contract-Revision": "1.1" } })).json();
  const mat = await send("POST", "/research/materials", { title: "대비 자료", sourceUrl: "https://example.com/m", collectedAt: new Date().toISOString(), contentKind: "summary", content: "자료 본문" });
  const doc = await send("POST", "/research/documents", { title: "대비 문서", content: "# 제목\n\n본문 **굵게**와 `코드`\n\n> 인용" });
  const para = (t) => ({ id: crypto.randomUUID(), type: "paragraph", runs: [{ text: t, marks: [] }], align: "left" });
  const draft = await send("POST", "/research/post-drafts", { title: "대비 글", content: { format: "post-blocks", version: 1, blocks: [para("첫 문단"), para("둘째 문단")] }, description: "요약", tags: ["테마"], coverImageId: null, documentIds: [] });
  const committed = await send("POST", `/research/post-drafts/${draft.id}/commit`, { expectedDraftRevision: draft.revision });
  const postId = committed.post?.id ?? committed.id;
  const PRIVATE = [
    ["write", "/write/"],
    ["write-post", `/write/?id=${postId}`],
    ["research", "/research/"],
    ["document", `/research/document/?id=${doc.id}`],
    ["material", `/research/material/?id=${mat.id}`],
    ["status", "/status/"],
  ];
  for (const [name, path] of PRIVATE) {
    await page.goto(U(path), { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
    await page.locator("main").getByText("불러오는 중...").first().waitFor({ state: "detached", timeout: 10000 }).catch(() => {});
    await page.screenshot({ path: `${SHOTS}/theme-${scheme}-${name}.png`, fullPage: true });
    const bad = await contrast(page);
    check(`T. ${scheme} ${name} (signed in): no WCAG AA color-contrast violations`, bad.length === 0, bad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | "));
  }
  // 대화창
  await page.goto(U("/research/"), { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Codex 대화창 열기" }).click();
  await page.locator("#chat-panel").waitFor({ timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}/theme-${scheme}-chat.png`, fullPage: false });
  const chatBad = await contrast(page);
  check(`T. ${scheme} chat panel: no WCAG AA color-contrast violations`, chatBad.length === 0, chatBad.slice(0, 4).map((b) => `${b.target} ${b.ratio} ${b.fg}/${b.bg}`).join(" | "));
  await ctx.close();
}
await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
