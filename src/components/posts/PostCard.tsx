"use client";

import Link from "next/link";
import { getPublicPost } from "@/lib/posts/api";
import type { PublicPost } from "@/lib/posts/types";
import { useLoad } from "@/lib/research/hooks";
import { excerpt } from "@/lib/posts/format";
import { PostDates } from "./PostArticle";
import TagBadge from "./TagBadge";

// #119 C1·D-2: 요약은 DOM에 문장 단위로 넣고 화면에서만 두 줄로 줄인다. 요약이 없어도 두 줄 자리를 잡아 카드 높이를 맞춘다.
const summaryClass = "mt-2 line-clamp-2 min-h-[2lh] text-stone-700";

// 이전 API(목록에 excerpt·본문 없음)일 때만 그 글을 따로 읽어 본문 앞부분을 보인다(#44).
// 읽지 못하면 빈 자리만 남는다(제목·날짜만으로도 카드는 성립한다).
function BodyExcerpt({ post }: { post: PublicPost }) {
  const { result } = useLoad(`public-post-excerpt:${post.slug}:${post.versionId}`, () => getPublicPost(post.slug));
  const text = result?.ok ? (result.data.excerpt ?? excerpt(result.data.textProjection ?? "")) : "";
  return <p className={summaryClass}>{text}</p>;
}

function Summary({ post }: { post: PublicPost }) {
  // #119 C2: 서버 excerpt(소제목·코드·표 제외)를 먼저 쓴다.
  if (post.excerpt !== undefined) return <p className={summaryClass}>{post.excerpt}</p>;
  if (post.textProjection !== undefined) return <p className={summaryClass}>{excerpt(post.textProjection)}</p>;
  return <BodyExcerpt post={post} />;
}

const MAX_TAGS = 3;

// #44: 표지 이미지 없이 글만 보인다. #119 D-2: 태그는 날짜 아래 한 줄(없어도 자리 유지, 넘치면 "+N").
export default function PostCard({ post }: { post: PublicPost }) {
  const shown = post.tags.slice(0, MAX_TAGS);
  const rest = post.tags.length - shown.length;
  return (
    <article className="group flex flex-col rounded-xl bg-white p-6 shadow-sm ring-1 ring-stone-200/60 transition-all hover:shadow-md hover:ring-indigo-200">
      <Link href={`/posts/${encodeURIComponent(post.slug)}/`} className="block">
        <h2 className="line-clamp-2 text-xl font-semibold tracking-tight text-stone-900 transition-colors group-hover:text-indigo-600">
          {post.title}
        </h2>
        <Summary post={post} />
      </Link>
      <div className="mt-4">
        <PostDates name={post.author.name} publishedAt={post.publishedAt} updatedAt={post.updatedAt} />
      </div>
      {post.tags.length === 0 ? (
        <div aria-hidden="true" className="mt-2 min-h-7" />
      ) : (
      <ul aria-label="태그" className="mt-2 flex min-h-7 flex-wrap gap-2 overflow-hidden">
        {shown.map((tag) => (
          <li key={tag}>
            <TagBadge tag={tag} />
          </li>
        ))}
        {rest > 0 && (
          <li className="self-center text-xs text-stone-600">
            +{rest}
            <span className="sr-only">개 태그 더 있음</span>
          </li>
        )}
      </ul>
      )}
    </article>
  );
}
