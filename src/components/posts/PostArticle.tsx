import type { PublicPost } from "@/lib/posts/types";
import { isOwnImage } from "@/components/research/SafeMarkdown";
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
      <h2 className="text-sm font-semibold text-stone-700">바탕이 된 참고 문서</h2>
      <ul className="mt-2 space-y-1 text-sm">
        {documents.map((d) => (
          <li key={d.id}>
            {d.publicUrl ? (
              <a href={d.publicUrl} className="text-indigo-600 hover:text-indigo-800">
                {d.title || "(제목 없음)"}
              </a>
            ) : (
              <span className="text-stone-600">
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
      <h2 className="text-sm font-semibold text-stone-700">바탕이 된 자료</h2>
      <ul className="mt-2 space-y-2 text-sm">
        {materials.map((m) => (
          <li key={m.id}>
            <span className="block text-stone-700">{m.title || "(제목 없음)"}</span>
            <SourceLink url={m.sourceUrl} className="text-xs" />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 발행본(또는 발행 미리보기) 본문. 본문은 블록 renderer(#32)로만 그리고 자체 업로드 이미지만 띄운다. */
export default function PostArticle({ post }: { post: ArticleData }) {
  const cover = post.coverImageUrl && isOwnImage(post.coverImageUrl) ? post.coverImageUrl : null;
  return (
    <article className="mx-auto max-w-3xl">
      <header className="mb-8">
        {cover && (
          // eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본(최적화 서버 없음)
          <img src={cover} alt="" className="mb-6 max-h-96 w-full rounded-2xl object-cover ring-1 ring-stone-200" />
        )}
        <h1 className="text-3xl font-bold tracking-tight text-stone-900">{post.title}</h1>
        {post.description && <p className="mt-2 text-stone-500">{post.description}</p>}
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
