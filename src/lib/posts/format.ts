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

/**
 * 글 목록 카드의 본문 발췌(#31 W2, 요약이 빈 글). Markdown 기호·코드 블록·이미지·HTML을 걷어 낸 평문 앞부분.
 * 표시용 근사치이며 본문 렌더 규칙(SafeMarkdown)과 무관하다.
 */
export function excerpt(markdown: string, max = 160): string {
  const text = markdown
    .replace(/^(```|~~~)[^\n]*\n[\s\S]*?(^\1[^\n]*$|(?![\s\S]))/gm, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<[^>\n]+>/g, " ")
    .replace(/!\[[^\]\n]*\]\([^)\n]*\)/g, " ")
    .replace(/\[([^\]\n]*)\]\([^)\n]*\)/g, "$1")
    .replace(/\[([^\]\n]*)\]\[[^\]\n]*\]/g, "$1")
    .replace(/^\s*\[[^\]\n]+\]:\s*\S+.*$/gm, " ")
    .replace(/^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-*:?\s*\|?\s*$/gm, " ")
    .replace(/^\s*([-*_]\s*){3,}$/gm, " ")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*(>\s?)+/gm, "")
    .replace(/^\s*([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?/gm, "")
    .replace(/`([^`\n]*)`/g, "$1")
    .replace(/(\*{1,3}|_{1,3}|~~)(\S(?:[^\n]*?\S)?)\1/g, "$2")
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const chars = Array.from(text);
  return chars.length <= max ? text : `${chars.slice(0, max).join("").trimEnd()}…`;
}
