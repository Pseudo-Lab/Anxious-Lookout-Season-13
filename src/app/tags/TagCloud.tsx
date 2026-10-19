"use client";

import Link from "next/link";
import { API_ENABLED } from "@/lib/constants";
import { listPostTags } from "@/lib/posts/api";
import { usePagedList } from "@/lib/research/usePagedList";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import { NO_API_POSTS } from "@/components/posts/PublicPostFeed";

// 현재 발행본의 태그와 글 수(#28 GET /api/public/post-tags, 사전순).
function Tags() {
  const list = usePagedList("public-post-tags", (cursor) => listPostTags(cursor));
  if (list.failure && list.items.length === 0) return <ErrorNotice failure={list.failure} prefix="태그를 불러오지 못했습니다." />;
  if (!list.loaded) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (list.items.length === 0) return <p className="text-stone-500">아직 태그가 없습니다.</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        {list.items.map(({ tag, count }) => (
          <Link
            key={tag}
            href={`/tags/${encodeURIComponent(tag)}/`}
            className="rounded-lg border border-stone-200 bg-white px-4 py-2 text-sm hover:border-stone-400"
          >
            {tag} <span className="text-stone-500">({count})</span>
          </Link>
        ))}
      </div>
      {list.failure && <ErrorNotice failure={list.failure} prefix="다음 태그를 불러오지 못했습니다." />}
      {list.nextCursor && (
        <button
          onClick={() => void list.loadMore()}
          disabled={list.loading}
          className="text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:text-stone-400"
        >
          {list.loading ? "불러오는 중..." : "태그 더 보기"}
        </button>
      )}
    </div>
  );
}

export default function TagCloud() {
  if (!API_ENABLED) return <Notice tone="info">{NO_API_POSTS}</Notice>;
  return <Tags />;
}
