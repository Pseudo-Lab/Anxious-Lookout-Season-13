"use client";

import type { PublicPost } from "@/lib/posts/types";
import BlockRenderer from "@/components/blocks/BlockRenderer";
import { formatDate, longDate, revisedDate } from "@/lib/posts/format";
import { safeHref } from "@/components/research/SafeMarkdown";
import { displayHost } from "@/lib/research/format";
import TableOfContents from "./TableOfContents";
import TagBadge from "./TagBadge";

type ArticleData = Pick<PublicPost, "title" | "description" | "content" | "tags" | "coverImageUrl" | "documents" | "materials"> & {
  author: { name: string };
  publishedAt: string | null;
  updatedAt?: string;
};

// 바탕이 된 문서. 공개본이 있는 문서만 링크한다(서버가 준 publicUrl, basePath 포함).
// #119 F4: 공개되지 않은 문서는 독자에게 보이지 않는다(눌러 볼 수 없는 항목). 작성자(발행 미리보기·본인 글)에게만 안내와 함께 보인다.
function BasedOn({ documents, viewerIsAuthor }: { documents: ArticleData["documents"]; viewerIsAuthor: boolean }) {
  const shown = viewerIsAuthor ? documents : documents.filter((d) => d.publicUrl);
  if (shown.length === 0) return null;
  return (
    <section aria-labelledby="based-on-documents" className="mt-10 rounded-xl bg-stone-50 p-5 ring-1 ring-stone-200">
      <h2 id="based-on-documents" className="text-sm font-semibold text-stone-900">바탕이 된 참고 문서</h2>
      <ul className="mt-2 space-y-1 text-sm">
        {shown.map((d) => (
          <li key={d.id}>
            {d.publicUrl ? (
              <a href={d.publicUrl} className="text-indigo-600 hover:text-indigo-800">
                {d.title || "(제목 없음)"}
              </a>
            ) : (
              <span className="text-stone-900">
                {d.title || "(제목 없음)"}{" "}
                <span className="text-xs text-stone-600">(비공개 문서 · 내 연구에서 공개하면 링크됩니다. 독자에게는 보이지 않습니다)</span>
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// 바탕이 된 자료(#31 W3). 공개본에는 제목과 원본 주소만 있다(자료 본문·메모는 공개하지 않는다).
// #119 F3: 긴 주소를 그대로 늘어놓지 않고 제목에 링크를 걸고 도메인만 작게 보인다. http(s)가 아니면 링크하지 않는다.
function BasedOnMaterials({ materials }: { materials: ArticleData["materials"] }) {
  if (materials.length === 0) return null;
  return (
    <section aria-labelledby="based-on-materials" className="mt-6 rounded-xl bg-stone-50 p-5 ring-1 ring-stone-200">
      <h2 id="based-on-materials" className="text-sm font-semibold text-stone-900">바탕이 된 자료</h2>
      <ul className="mt-2 space-y-2 text-sm">
        {materials.map((m) => {
          const href = m.sourceUrl ? safeHref(m.sourceUrl) : null;
          const external = href && /^https?:/i.test(href) ? href : null;
          const title = m.title || "(제목 없음)";
          return (
            <li key={m.id}>
              {external ? (
                <a href={external} target="_blank" rel="noopener noreferrer nofollow" className="text-indigo-600 hover:text-indigo-800">
                  {title}
                  <span className="sr-only"> (새 창)</span>
                </a>
              ) : (
                <span className="text-stone-900">{title}</span>
              )}
              {m.sourceUrl && <span className="ml-2 text-xs text-stone-600">{external ? displayHost(external) : m.sourceUrl}</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 글쓴이 · 최초 발행일 · (다른 날 다시 발행했으면) 수정일. 발행 미리보기는 날짜 없이 이름만(#80 F5). */
export function PostDates({ name, publishedAt, updatedAt }: { name: string; publishedAt: string | null; updatedAt?: string }) {
  const revised = publishedAt ? revisedDate(publishedAt, updatedAt) : null;
  const parts = [
    name ? <span>{name}</span> : null,
    publishedAt ? (
      <time dateTime={publishedAt} title={`최초 발행일 ${longDate(publishedAt)}`}>
        {formatDate(publishedAt)}
      </time>
    ) : null,
    revised && updatedAt ? (
      <time dateTime={updatedAt} title={`마지막으로 다시 발행한 날 ${longDate(updatedAt)}`}>
        {revised} 수정
      </time>
    ) : null,
  ].filter((p) => p !== null);
  if (parts.length === 0) return null;
  return (
    <span className="text-sm text-stone-600">
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 && <> &middot; </>}
          {p}
        </span>
      ))}
    </span>
  );
}

/**
 * 발행본(또는 발행 미리보기) 본문. 본문은 블록 renderer(#32)로만 그리고 자체 업로드 이미지만 띄운다.
 * #44: 표지·요약은 편집 UI가 없어 보이지 않는다(저장된 값은 데이터로 남는다).
 * #119 B5: 읽기 폭은 약 720px. F1: 소제목이 3개 이상이면 목차·읽기 진행 표시(toc). viewerIsAuthor: 비공개 바탕 문서를 안내와 함께 보인다.
 */
export default function PostArticle({
  post,
  viewerIsAuthor = false,
  toc = false,
  actions,
}: {
  post: ArticleData;
  viewerIsAuthor?: boolean;
  toc?: boolean;
  actions?: React.ReactNode;
}) {
  return (
    <article className="mx-auto max-w-[45rem]">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-stone-900">{post.title}</h1>
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
          <PostDates name={post.author.name} publishedAt={post.publishedAt} updatedAt={post.updatedAt} />
          {actions}
        </div>
        {post.tags.length > 0 && (
          <ul aria-label="태그" className="mt-3 flex flex-wrap gap-2">
            {post.tags.map((tag) => (
              <li key={tag}>
                <TagBadge tag={tag} />
              </li>
            ))}
          </ul>
        )}
      </header>
      {toc && <TableOfContents content={post.content} />}
      <BlockRenderer content={post.content} />
      <BasedOn documents={post.documents} viewerIsAuthor={viewerIsAuthor} />
      <BasedOnMaterials materials={post.materials} />
    </article>
  );
}
