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
  await ctx.close();
}
await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
