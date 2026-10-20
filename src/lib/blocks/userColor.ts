// #38 D-38-1 사용자 선택 색의 다크 모드 규칙. 저장 값(#RRGGBB)은 바꾸지 않고 그릴 때만 쓴다.
// - 글자색: 다크 모드에서는 같은 색상(hue)을 유지한 채 밝기만 올린 색으로 그린다(어두운 면에서 AA 4.5:1 이상).
// - 칸 배경: 저장된 배경을 그대로 쓰고, 그 칸 안은 배경에 맞는 글자색(밝은 배경이면 어두운 글자)으로 그린다.
// 편집기와 공개 renderer가 같은 함수와 같은 CSS 클래스(globals.css의 pb-color·pb-on-light·pb-on-dark)를 쓴다.

import type { CSSProperties } from "react";

/** 다크 모드에서 글자가 놓일 수 있는 가장 밝은 면(stone-200 다크 값 #292524). 이 위에서 AA를 넘기면 더 어두운 면에서도 넘는다. */
const DARK_SURFACE = "#292524";
/** 목표 대비(AA 4.5에 여유를 둔다) */
const TARGET = 5;
/** 다크 모드 기본 글자 밝기(OKLab L, --foreground 97%). 어두운 무채색을 거울처럼 뒤집을 때 이보다 밝게 하지 않는다. */
const MAX_MIRROR_L = 0.97;
const LIGHT_TEXT = "#1C1917";
const DARK_TEXT = "#FAFAF9";

type RGB = [number, number, number];

function parse(hex: string): RGB | null {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255) as RGB;
}

const toHex = (rgb: RGB) =>
  `#${rgb
    .map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase()}`;

const toLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const fromLinear = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

/** WCAG 2 상대 휘도 */
function luminance(rgb: RGB) {
  const [r, g, b] = rgb.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 대비(1..21). 형식이 틀린 값은 1. */
export function contrastRatio(a: string, b: string) {
  const x = parse(a);
  const y = parse(b);
  if (!x || !y) return 1;
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
}

// OKLab(Björn Ottosson). 밝기 L만 바꾸고 색상 각도는 유지한다.
function toOklab([r, g, b]: RGB): RGB {
  const [lr, lg, lb] = [toLinear(r), toLinear(g), toLinear(b)];
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

/** OKLab → sRGB(0..1). 범위를 벗어나면 null. */
function fromOklab([L, a, b]: RGB): RGB | null {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map(fromLinear);
  return rgb.every((v) => v >= -1e-4 && v <= 1 + 1e-4) ? (rgb as RGB) : null;
}

/** 밝기 L에서 색상 각도를 유지하며 낼 수 있는 가장 진한 색(채도는 원래 값까지). */
function atLightness(L: number, a: number, b: number): RGB {
  const inGamut = fromOklab([L, a, b]);
  if (inGamut) return inGamut;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 24; i++) {
    const k = (lo + hi) / 2;
    if (fromOklab([L, a * k, b * k])) lo = k;
    else hi = k;
  }
  return fromOklab([L, a * lo, b * lo]) ?? [L, L, L];
}

const darkCache = new Map<string, string>();

/**
 * 다크 모드에서 그릴 글자색. 이미 충분히 밝으면 그대로 두고, 어두우면 같은 색상으로 밝기만 올린다.
 * 어두운 색은 먼저 밝기를 뒤집고(검정에 가까운 글자 → 밝은 글자), 그래도 대비가 모자라면 AA를 넘는 가장 낮은 밝기까지 올린다.
 */
export function darkTextColor(hex: string): string {
  const key = hex.toUpperCase();
  const hit = darkCache.get(key);
  if (hit) return hit;
  const rgb = parse(key);
  if (!rgb) return key;
  const [L, a, b] = toOklab(rgb);
  const ok = (l: number) => contrastRatio(toHex(atLightness(l, a, b)), DARK_SURFACE) >= TARGET;
  let start = Math.max(L, Math.min(1 - L, MAX_MIRROR_L));
  let out: string;
  if (L >= start && contrastRatio(key, DARK_SURFACE) >= TARGET) out = key;
  else if (ok(start)) out = toHex(atLightness(start, a, b));
  else {
    let hi = 1;
    for (let i = 0; i < 24; i++) {
      const mid = (start + hi) / 2;
      if (ok(mid)) hi = mid;
      else start = mid;
    }
    out = toHex(atLightness(hi, a, b));
  }
  darkCache.set(key, out);
  return out;
}

/** 칸 배경이 밝은가(어두운 글자가 흰 글자보다 대비가 크면 밝은 배경). */
export function isLightBackground(hex: string) {
  return contrastRatio(hex, LIGHT_TEXT) >= contrastRatio(hex, DARK_TEXT);
}

/** 글자색 mark를 그릴 때 붙이는 클래스와 inline 값(라이트 = 저장 값 그대로, 다크 = --pb-cd). */
export const TEXT_COLOR_CLASS = "pb-color";
export function textColorStyle(hex: string): CSSProperties {
  return { color: hex, "--pb-c": hex, "--pb-cd": darkTextColor(hex) } as CSSProperties;
}
/** 편집기 renderHTML용 같은 값(style 문자열). 값은 검증된 #RRGGBB에서만 만든다. */
export function textColorStyleText(hex: string) {
  return `color: ${hex}; --pb-c: ${hex}; --pb-cd: ${darkTextColor(hex)}`;
}

/** 배경이 있는 칸의 클래스: 그 칸 안은 배경에 맞는 글자색·링크색으로 그린다(테마와 무관). */
export function cellBackgroundClass(hex: string) {
  return isLightBackground(hex) ? "pb-on-light" : "pb-on-dark";
}
