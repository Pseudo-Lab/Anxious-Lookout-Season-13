import type { PublicPost } from "@/lib/posts/types";
import BlockRenderer from "@/components/blocks/BlockRenderer";
import { formatDate } from "@/lib/posts/format";
import { SourceLink } from "./MaterialLinks";
import TagBadge from "./TagBadge";

type ArticleData = Pick<PublicPost, "title" | "description" | "content" | "tags" | "coverImageUrl" | "documents" | "materials"> & {
  author: { name: string };
  publishedAt: string | null;
};

// 바탕이 된 문서. 공개본이 있는 문서만 링크하고(서버가 준 publicUrl, basePath 포함), 나머지는 제목만 보인다.
function BasedOn({ documents }: { documents: ArticleData["documents"] }) {
  if (documents.length === 0) return null;
  return (
    <section className="mt-10 rounded-xl bg-stone-50 p-5 ring-1 ring-stone-200">
      <h2 className="text-sm font-semibold text-stone-900">바탕이 된 참고 문서</h2>
      <ul className="mt-2 space-y-1 text-sm">
        {documents.map((d) => (
          <li key={d.id}>
            {d.publicUrl ? (
              <a href={d.publicUrl} className="text-indigo-600 hover:text-indigo-800">
                {d.title || "(제목 없음)"}
              </a>
            ) : (
              <span className="text-stone-900">
                {d.title || "(제목 없음)"} <span className="text-xs text-stone-500">(공개되지 않은 문서)</span>
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// 바탕이 된 자료(#31 W3). 공개본에는 제목과 원본 주소만 있다(자료 본문·메모는 공개하지 않는다).
function BasedOnMaterials({ materials }: { materials: ArticleData["materials"] }) {
  if (materials.length === 0) return null;
  return (
    <section aria-label="바탕이 된 자료" className="mt-6 rounded-xl bg-stone-50 p-5 ring-1 ring-stone-200">
      <h2 className="text-sm font-semibold text-stone-900">바탕이 된 자료</h2>
      <ul className="mt-2 space-y-2 text-sm">
        {materials.map((m) => (
          <li key={m.id}>
            <span className="block text-stone-900">{m.title || "(제목 없음)"}</span>
            <SourceLink url={m.sourceUrl} className="text-xs" />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 발행본(또는 발행 미리보기) 본문. 본문은 블록 renderer(#32)로만 그리고 자체 업로드 이미지만 띄운다. */
// #44: 표지·요약은 편집 UI가 없어 보이지 않는다(저장된 값은 데이터로 남는다).
export default function PostArticle({ post }: { post: ArticleData }) {
  return (
    <article className="mx-auto max-w-3xl">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-stone-900">{post.title}</h1>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="text-sm text-stone-500">
            {post.author.name}
            {post.publishedAt ? <> &middot; {formatDate(post.publishedAt)}</> : null}
          </span>
          <div className="flex flex-wrap gap-2">
            {post.tags.map((tag) => (
              <TagBadge key={tag} tag={tag} />
            ))}
          </div>
        </div>
      </header>
      <BlockRenderer content={post.content} />
      <BasedOn documents={post.documents} />
      <BasedOnMaterials materials={post.materials} />
    </article>
  );
}
