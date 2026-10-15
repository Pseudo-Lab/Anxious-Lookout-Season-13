import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import ScrollBox from "@/components/research/ScrollBox";

// 사용자가 저장한 자료·문서 본문 렌더러.
// 저장소에 포함된 글(PostContent, MDX)과 달리 본문을 실행 코드로 취급하지 않는다.
// - raw HTML은 렌더하지 않는다(rehype-raw 미사용, skipHtml).
// - 링크는 http/https/mailto 절대 주소와 문서 내 #앵커만 허용하고 나머지는 텍스트로 남긴다.
// - 외부 이미지는 불러오지 않고 링크로 표시한다(방문자 요청이 외부로 새지 않게).
// - 넓은 표·코드 블록은 그 안에서만 가로로 스크롤한다(좁은 대화창에서도 마지막 열까지 읽을 수 있게).

const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

export function safeHref(url: string): string | null {
  const trimmed = url.trim();
  if (trimmed.startsWith("#")) return trimmed;
  try {
    const parsed = new URL(trimmed);
    return ALLOWED_PROTOCOLS.has(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

const components: Components = {
  a({ href, children }) {
    const safe = href ? safeHref(href) : null;
    if (!safe) return <span className="text-stone-500">{children}</span>;
    if (safe.startsWith("#")) return <a href={safe}>{children}</a>;
    return (
      <a href={safe} target="_blank" rel="noopener noreferrer nofollow ugc">
        {children}
      </a>
    );
  },
  img({ src, alt }) {
    const safe = typeof src === "string" ? safeHref(src) : null;
    const label = alt ? `이미지: ${alt}` : "이미지";
    if (!safe || safe.startsWith("#")) return <span className="text-stone-500">[{label}]</span>;
    return (
      <a href={safe} target="_blank" rel="noopener noreferrer nofollow ugc">
        [{label}]
      </a>
    );
  },
  table({ children }) {
    return (
      <ScrollBox label="표" className="table-scroll">
        <table>{children}</table>
      </ScrollBox>
    );
  },
  // 칸 내용은 읽을 수 있는 폭까지만 넓히고 그 이상은 줄바꿈한다(globals.css .cell). 열 정렬(style)은 그대로 둔다.
  th({ children, style }) {
    return (
      <th style={style}>
        <span className="cell">{children}</span>
      </th>
    );
  },
  td({ children, style }) {
    return (
      <td style={style}>
        <span className="cell">{children}</span>
      </td>
    );
  },
  pre({ children }) {
    return <ScrollBox as="pre" label="코드">{children}</ScrollBox>;
  },
};

/** compact: 대화창처럼 좁은 곳의 작은 글자·여백. */
export default function SafeMarkdown({ content, compact = false }: { content: string; compact?: boolean }) {
  return (
    <div className={`prose max-w-none break-words ${compact ? "prose-compact text-sm text-stone-800" : ""}`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        // 기본 변환 대신 위 components에서 허용 목록으로 걸러낸다.
        urlTransform={(url) => url}
        components={components}
      >
        {content}
      </Markdown>
    </div>
  );
}
