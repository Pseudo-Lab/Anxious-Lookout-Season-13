const date = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit" });

/** 발행일(날짜만). 해석할 수 없는 값은 그대로 보인다. */
export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : date.format(d);
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

/** 글 목록 카드의 본문 발췌(#31 W2, 요약이 빈 글). #32: 서버 textProjection(평문)의 앞부분이다. */
export function excerpt(text: string, max = 160): string {
  const flat = text.replace(/\s+/g, " ").trim();
  const chars = Array.from(flat);
  return chars.length <= max ? flat : `${chars.slice(0, max).join("").trimEnd()}…`;
}
