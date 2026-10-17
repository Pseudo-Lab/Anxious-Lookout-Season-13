"use client";

import { API_ENABLED } from "@/lib/constants";
import { listPublicPosts } from "@/lib/posts/api";
import { usePagedList } from "@/lib/research/usePagedList";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import PostList from "./PostList";

export const NO_API_POSTS = "이 정적 사이트에는 API 서버가 없어 글을 제공하지 않습니다.";

// 현재 발행본 목록(#28 GET /api/public/posts). 서버 HTML에는 글이 없고 브라우저가 조회한다.
function Feed({ tag, limit, more }: { tag?: string; limit?: number; more: boolean }) {
  const list = usePagedList(`public-posts:${tag ?? ""}:${limit ?? ""}`, (cursor) => listPublicPosts(cursor, { tag, limit }));
  if (list.failure && list.items.length === 0) return <ErrorNotice failure={list.failure} prefix="글을 불러오지 못했습니다." />;
  if (!list.loaded) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  return (
    <div className="space-y-4">
      <PostList posts={list.items} />
      {list.failure && <ErrorNotice failure={list.failure} prefix="다음 글을 불러오지 못했습니다." />}
      {more && list.nextCursor && (
        <button
          onClick={() => void list.loadMore()}
          disabled={list.loading}
          className="text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:text-stone-400"
        >
          {list.loading ? "불러오는 중..." : "더 보기"}
        </button>
      )}
    </div>
  );
}

/** tag: 정확히 이 태그가 달린 글만. limit+more=false: 첫 장만(홈 최근 글). */
export default function PublicPostFeed({ tag, limit, more = true }: { tag?: string; limit?: number; more?: boolean }) {
  if (!API_ENABLED) return <Notice tone="info">{NO_API_POSTS}</Notice>;
  return <Feed tag={tag} limit={limit} more={more} />;
}
