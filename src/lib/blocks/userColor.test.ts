// #38 D-38-1 사용자 선택 색 규칙: 다크 모드 글자색 밝기 보정, 배경 있는 칸의 글자색, 저장 값 불변.
import { describe, expect, it } from "vitest";
import { getSchema } from "@tiptap/core";
import { blockExtensions } from "@/components/blocks/editor/schema";
import { cellBackgroundClass, contrastRatio, darkTextColor, isLightBackground, textColorStyleText } from "./userColor";

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
  });

  it("accepts lower-case input and is stable", () => {
    expect(darkTextColor("#dc2626")).toBe(darkTextColor("#DC2626"));
  });
});

describe("cell backgrounds", () => {
  it.each(CELL_BACKGROUNDS)("%s is a light background: dark text with AA", (hex) => {
    expect(isLightBackground(hex)).toBe(true);
    expect(cellBackgroundClass(hex)).toBe("pb-on-light");
    expect(contrastRatio("#1C1917", hex)).toBeGreaterThanOrEqual(4.5);
  });

  it("a dark stored background gets light text", () => {
    expect(cellBackgroundClass("#1C1917")).toBe("pb-on-dark");
    expect(cellBackgroundClass("#1E3A8A")).toBe("pb-on-dark");
  });
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

  it("a cell background renders as stored with the light-background class", () => {
    const cell = schema.nodes.tableCell.create({ background: "#FEF3C7" }, schema.nodes.paragraph.create());
    const [, attrs] = schema.nodes.tableCell.spec.toDOM!(cell) as [string, Record<string, string>];
    expect(attrs["data-background"]).toBe("#FEF3C7");
    expect(attrs.class).toContain("pb-on-light");
    expect(attrs.style).toContain("background-color: #FEF3C7");
  });
});
