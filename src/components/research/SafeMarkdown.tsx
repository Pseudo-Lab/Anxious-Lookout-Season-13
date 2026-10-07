import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// 사용자가 저장한 자료·문서 본문 렌더러.
// 저장소에 포함된 글(PostContent, MDX)과 달리 본문을 실행 코드로 취급하지 않는다.
// - raw HTML은 렌더하지 않는다(rehype-raw 미사용, skipHtml).
// - 링크는 http/https/mailto 절대 주소와 문서 내 #앵커만 허용하고 나머지는 텍스트로 남긴다.
// - 외부 이미지는 불러오지 않고 링크로 표시한다(방문자 요청이 외부로 새지 않게).

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
};

export default function SafeMarkdown({ content }: { content: string }) {
  return (
    <div className="prose max-w-none break-words">
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
