// #119 B7: 날짜만은 "2026. 11. 18.", 시각 포함은 research/format의 formatDateTime("2026. 11. 17. 오후 11:45"). 모두 브라우저 시간대.
const date = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit" });
const long = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric" });

/** 발행일(날짜만). 해석할 수 없는 값은 그대로 보인다. */
export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : date.format(d);
}

/** 읽어 주는 날짜(#119 B8 <time> 이름): "2026년 11월 18일". 해석할 수 없는 값은 그대로. */
export function longDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : long.format(d);
}

/**
 * #80 F5: 다시 발행한 날짜(날짜만). 최초 발행일과 같은 날이거나 updatedAt이 없으면(이전 응답) null — 발행일만 보인다.
 */
export function revisedDate(publishedAt: string, updatedAt: string | undefined): string | null {
  if (!updatedAt) return null;
  const revised = formatDate(updatedAt);
  return revised === formatDate(publishedAt) ? null : revised;
}

/**
 * #80 F5: 다시 발행한 적이 있는지(시각까지). stamp()는 0 마이크로초면 소수부를 빼므로 글자가 아니라 시각으로 비교한다.
 * 해석할 수 없으면 글자가 다를 때만 다시 발행한 것으로 본다.
 */
export function wasRepublished(publishedAt: string, updatedAt: string | undefined): boolean {
  if (!updatedAt) return false;
  const a = Date.parse(publishedAt);
  const b = Date.parse(updatedAt);
  return Number.isNaN(a) || Number.isNaN(b) ? publishedAt !== updatedAt : a !== b;
}

/** URL 경로 조각을 한 번만 해석한다(잘못된 % 인코딩이면 원문 그대로). */
export function decodeSegment(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/**
 * 글 목록 카드의 본문 발췌(서버 excerpt가 없는 이전 API용, #31 W2 · #32 textProjection 평문).
 * #119 C1: 화면은 CSS line-clamp로 줄이므로 DOM에는 단어·문장 중간에서 자른 글을 넣지 않는다.
 * 문장 끝(. ! ? 。 … 뒤 공백·끝)까지 max자 안에서 통째로 담고, 첫 문장이 max보다 길면 hardMax까지 그대로 둔다.
 * hardMax를 넘을 때만 마지막 공백에서 끊고 "…"를 붙인다(issue119 §C 서버 규칙과 같은 모양).
 */
export function excerpt(text: string, max = 200, hardMax = 400): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (Array.from(flat).length <= max) return flat;
  const sentences: string[] = [];
  let start = 0;
  for (const m of flat.matchAll(/[.!?。…]+(?=\s|$)/g)) {
    const end = m.index + m[0].length;
    sentences.push(flat.slice(start, end));
    start = end;
  }
  if (start < flat.length) sentences.push(flat.slice(start));
  let out = "";
  for (const sentence of sentences) {
    if (Array.from(out + sentence).length > max) break;
    out += sentence;
  }
  out = out.trim();
  if (out) return out;
  const first = sentences[0].trim();
  const chars = Array.from(first);
  if (chars.length <= hardMax) return first;
  // "…"까지 합쳐 hardMax자를 넘지 않는다(issue119 r1)
  const head = chars.slice(0, hardMax - 1).join("");
  const cut = head.lastIndexOf(" ");
  return `${(cut > 0 ? head.slice(0, cut) : head).trimEnd()}…`;
}
