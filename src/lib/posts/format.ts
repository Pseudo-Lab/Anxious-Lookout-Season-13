const date = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit" });

/** 발행일(날짜만). 해석할 수 없는 값은 그대로 보인다. */
export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : date.format(d);
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
