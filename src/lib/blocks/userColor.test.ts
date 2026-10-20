// #38 D-38-1 사용자 선택 색 규칙: 다크 모드 글자색 밝기 보정, 배경 있는 칸의 글자색, 저장 값 불변.
import { describe, expect, it } from "vitest";
import { getSchema } from "@tiptap/core";
import { blockExtensions } from "@/components/blocks/editor/schema";
import { cellBackgroundStyleText, cellColors, contrastRatio, darkTextColor, isLightBackground, readableOn, textColorStyleText } from "./userColor";

// 편집기 도구 막대의 글자색 8종과 칸 배경 5종(BlockEditor COLORS·CELL_BACKGROUNDS)
const COLORS = ["#1C1917", "#DC2626", "#EA580C", "#CA8A04", "#16A34A", "#2563EB", "#7C3AED", "#DB2777"];
const CELL_BACKGROUNDS = ["#FEF3C7", "#DCFCE7", "#DBEAFE", "#F3E8FF", "#F5F5F4"];
// 다크 모드에서 글자가 놓이는 면: 바탕(stone-50), 카드·편집기(white·stone-100), 가장 밝은 면(stone-200)
const DARK_SURFACES = ["#0C0A09", "#1C1917", "#292524"];

/** HSL 색상 각도(도). 무채색은 null. */
function hue(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  if (d < 0.08) return null;
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}
const hueGap = (a: number, b: number) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));

describe("darkTextColor", () => {
  it.each(COLORS)("%s reaches AA on every dark surface and keeps its hue", (hex) => {
    const dark = darkTextColor(hex);
    expect(dark).toMatch(/^#[0-9A-F]{6}$/);
    for (const s of DARK_SURFACES) expect(contrastRatio(dark, s)).toBeGreaterThanOrEqual(4.5);
    const h0 = hue(hex);
    const h1 = hue(dark);
    if (h0 !== null) {
      expect(h1).not.toBeNull();
      expect(hueGap(h0, h1!)).toBeLessThan(20);
    }
  });

  it("turns the near-black text colour into light text", () => {
    expect(contrastRatio(darkTextColor("#1C1917"), "#1C1917")).toBeGreaterThan(7);
  });

  it("leaves colours that already read well on dark surfaces unchanged", () => {
    for (const hex of ["#FACC15", "#FFFFFF", "#A5F3FC"]) expect(darkTextColor(hex)).toBe(hex);
  });

  it("reaches AA for any stored #RRGGBB (seeded sample)", () => {
    let seed = 38;
    const next = () => (seed = (seed * 1103515245 + 12345) % 2 ** 31);
    for (let i = 0; i < 2000; i++) {
      const hex = `#${(next() % 0x1000000).toString(16).padStart(6, "0").toUpperCase()}`;
      expect(contrastRatio(darkTextColor(hex), "#292524"), hex).toBeGreaterThanOrEqual(4.5);
    }
  }, 30000);

  it("accepts lower-case input and is stable", () => {
    expect(darkTextColor("#dc2626")).toBe(darkTextColor("#DC2626"));
  });
});

// 리뷰 slice3 round1: 팔레트 밖 배경(허용되는 임의 #RRGGBB)도 실제 배경 기준으로 계산해야 한다
const CUSTOM_BGS = ["#1E3A8A", "#555555", "#777777", "#000000", "#FFFFFF", "#7F7F7F", "#3B82F6"];
const seeded = (n: number, seed: number) => {
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    seed = (seed * 1103515245 + 12345) % 2 ** 31;
    out.push(`#${(seed % 0x1000000).toString(16).padStart(6, "0").toUpperCase()}`);
  }
  return out;
};

describe("cell backgrounds", () => {
  it.each(CELL_BACKGROUNDS)("%s is a light background: default dark text with AA", (hex) => {
    expect(isLightBackground(hex)).toBe(true);
    expect(cellColors(hex).fg).toBe("#1C1917");
  });

  it.each([...CELL_BACKGROUNDS, ...CUSTOM_BGS])("%s: default text, link and every palette colour reach AA on that background", (bg) => {
    const { fg, link } = cellColors(bg);
    expect(contrastRatio(fg, bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(link, bg)).toBeGreaterThanOrEqual(4.5);
    for (const c of COLORS) expect(contrastRatio(readableOn(c, bg), bg), `${c} on ${bg}`).toBeGreaterThanOrEqual(4.5);
  });

  it("reported cases: violet on #1E3A8A, and links on #555555", () => {
    expect(contrastRatio(readableOn("#7C3AED", "#1E3A8A"), "#1E3A8A")).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(readableOn("#7C3AED", "#555555"), "#555555")).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(readableOn("#7C3AED", "#777777"), "#777777")).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(cellColors("#555555").link, "#555555")).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps a colour that already reads well, and keeps its hue when it moves", () => {
    expect(readableOn("#7C3AED", "#FEF3C7")).toBe("#7C3AED");
    const moved = readableOn("#DC2626", "#DBEAFE");
    expect(moved).not.toBe("#DC2626");
    expect(hueGap(hue("#DC2626")!, hue(moved)!)).toBeLessThan(20);
  });

  it("any stored background × colour reaches AA (seeded sample)", () => {
    const bgs = seeded(200, 381);
    const colors = seeded(16, 382);
    for (const bg of bgs) {
      const { fg, link } = cellColors(bg);
      expect(contrastRatio(fg, bg), `fg on ${bg}`).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(link, bg), `link on ${bg}`).toBeGreaterThanOrEqual(4.5);
      for (const c of colors) expect(contrastRatio(readableOn(c, bg), bg), `${c} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    }
  }, 60000);
});

describe("editor rendering (same classes and values as the public renderer)", () => {
  const schema = getSchema(blockExtensions({}));

  it("a text colour renders the stored value plus the dark value, and parses back to the stored value", () => {
    const mark = schema.marks.textStyle.create({ color: "#2563EB" });
    const [tag, attrs] = schema.marks.textStyle.spec.toDOM!(mark, true) as [string, Record<string, string>];
    expect(tag).toBe("span");
    expect(attrs.class).toContain("pb-color");
    expect(attrs.style).toBe(textColorStyleText("#2563EB"));
    expect(attrs.style).toContain("color: #2563EB");
    expect(attrs.style).toContain(`--pb-cd: ${darkTextColor("#2563EB")}`);
  });

  it("a cell background renders as stored with its own text and link colours", () => {
    const cell = schema.nodes.tableCell.create({ background: "#FEF3C7" }, schema.nodes.paragraph.create());
    const [, attrs] = schema.nodes.tableCell.spec.toDOM!(cell) as [string, Record<string, string>];
    expect(attrs["data-background"]).toBe("#FEF3C7");
    expect(attrs.class).toContain("pb-cell-bg");
    expect(attrs.style).toBe(cellBackgroundStyleText("#FEF3C7"));
    expect(attrs.style).toContain("background-color: #FEF3C7");
  });
});
