import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import ScrollBox from "@/components/research/ScrollBox";
import { BASE_PATH } from "@/lib/constants";

// 사용자가 저장한 자료·문서 본문 렌더러.
// 저장소에 포함된 글(PostContent, MDX)과 달리 본문을 실행 코드로 취급하지 않는다.
// - raw HTML은 렌더하지 않는다(rehype-raw 미사용, skipHtml).
// - 링크는 http/https/mailto 절대 주소와 문서 내 #앵커만 허용하고 나머지는 텍스트로 남긴다.
// - 외부 이미지는 불러오지 않고 링크로 표시한다(방문자 요청이 외부로 새지 않게).
//   글(#28)의 자체 업로드 이미지는 정확히 `{BASE_PATH}/api/media/post-images/{소문자 UUID}`만 <img>로 띄운다(ownImages).
//   서버가 발행 검증·공개 권한을 주는 형태와 같으며 origin·query·대문자 등 변형은 외부 이미지와 같이 다룬다.
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

// 이미지는 불러오지 않고 링크로 남긴다.
function ImageLink({ src, alt }: { src?: unknown; alt?: string }) {
  const safe = typeof src === "string" ? safeHref(src) : null;
  const label = alt ? `이미지: ${alt}` : "이미지";
  if (!safe || safe.startsWith("#")) return <span className="text-stone-500">[{label}]</span>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer nofollow ugc">
      [{label}]
    </a>
  );
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
    return <ImageLink src={src} alt={alt} />;
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

// BASE_PATH는 next.config에서 `/[A-Za-z0-9._~-]+` 형식으로 검증되므로 정규식 특수 문자는 '.'뿐이다.
const OWN_IMAGE = new RegExp(
  `^${BASE_PATH.replace(/\./g, "\\.")}/api/media/post-images/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`
);

/** 자체 업로드 이미지 경로인지(#28 계약의 정확한 형태만). */
export const isOwnImage = (src: string) => OWN_IMAGE.test(src);

const withOwnImages: Components = {
  ...components,
  img({ src, alt }) {
    if (typeof src === "string" && isOwnImage(src)) {
      // eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본을 그대로 보인다(최적화 서버 없음)
      return <img src={src} alt={alt ?? ""} loading="lazy" className="max-w-full rounded" />;
    }
    return <ImageLink src={src} alt={alt} />;
  },
};

/** compact: 대화창처럼 좁은 곳의 작은 글자·여백. */
export default function SafeMarkdown({
  content,
  compact = false,
  ownImages = false,
}: {
  content: string;
  compact?: boolean;
  ownImages?: boolean;
}) {
  return (
    <div className={`prose max-w-none break-words ${compact ? "prose-compact text-sm text-stone-800" : ""}`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        // 기본 변환 대신 위 components에서 허용 목록으로 걸러낸다.
        urlTransform={(url) => url}
        components={ownImages ? withOwnImages : components}
      >
        {content}
      </Markdown>
    </div>
  );
}
