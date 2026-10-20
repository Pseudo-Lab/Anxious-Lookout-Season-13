"use client";

import Link from "next/link";
import { getPublicPost } from "@/lib/posts/api";
import type { PublicPost } from "@/lib/posts/types";
import { useLoad } from "@/lib/research/hooks";
import { excerpt, formatDate } from "@/lib/posts/format";
import TagBadge from "./TagBadge";

const summaryClass = "mt-2 line-clamp-2 text-stone-900";

// 카드는 본문 앞부분을 보인다(#44: 요약 입력을 없앴다. 저장된 요약은 보이지 않는다). 목록 응답에는 본문이 없어(계약 그대로) 그 글만 따로 읽는다.
// 읽지 못하면 아무것도 보이지 않는다(제목·날짜만으로도 카드는 성립한다).
function BodyExcerpt({ post }: { post: PublicPost }) {
  const { result } = useLoad(`public-post-excerpt:${post.slug}:${post.versionId}`, () => getPublicPost(post.slug));
  const text = result?.ok ? excerpt(result.data.textProjection ?? "") : "";
  return text ? <p className={summaryClass}>{text}</p> : null;
}

function Summary({ post }: { post: PublicPost }) {
  if (post.textProjection !== undefined) {
    const text = excerpt(post.textProjection);
    return text ? <p className={summaryClass}>{text}</p> : null;
  }
  return <BodyExcerpt post={post} />;
}

// #44: 표지 이미지 없이 글만 보인다
export default function PostCard({ post }: { post: PublicPost }) {
  return (
    <article className="group rounded-xl bg-white p-6 shadow-sm ring-1 ring-stone-200/60 transition-all hover:shadow-md hover:ring-indigo-200">
      <Link href={`/posts/${encodeURIComponent(post.slug)}/`} className="flex gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-xl font-semibold tracking-tight text-stone-900 transition-colors group-hover:text-indigo-600">
            {post.title}
          </h2>
          <Summary post={post} />
        </div>
      </Link>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <span className="text-sm text-stone-500">
          {post.author.name ? `${post.author.name} · ` : ""}
          {formatDate(post.publishedAt)}
        </span>
        <div className="flex flex-wrap gap-2">
          {post.tags.map((tag) => (
            <TagBadge key={tag} tag={tag} />
          ))}
        </div>
      </div>
    </article>
  );
}
