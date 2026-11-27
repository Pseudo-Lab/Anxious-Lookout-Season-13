"use client";

import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { API_ENABLED } from "@/lib/constants";
import { listPostAuthors, listPostTags, listPublicPosts } from "@/lib/posts/api";
import { useLoad } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import { inputClass } from "@/components/research/ItemForms";
import PostList, { EmptyPosts } from "./PostList";
import { PostListSkeleton } from "./PostSkeleton";
import { NO_API_POSTS } from "./PublicPostFeed";

// #119 A7: 글 목록 찾기(제목·본문 검색, 태그·작성자 필터, 10편씩 나눠 보기, 전체 건수). 조건은 주소(?q=&tag=&author=)에 남아 공유된다.
const PAGE = 10;

function useFilters() {
  const params = useSearchParams();
  return { q: params.get("q")?.trim() ?? "", tag: params.get("tag") ?? "", author: params.get("author") ?? "" };
}

function Filters() {
  const router = useRouter();
  const pathname = usePathname();
  const current = useFilters();
  const [q, setQ] = useState(current.q);
  // 주소가 바뀌면(뒤로 가기 등) 입력도 맞춘다
  const [seen, setSeen] = useState(current.q);
  if (seen !== current.q) {
    setSeen(current.q);
    setQ(current.q);
  }
  const tags = useLoad("post-browser-tags", () => listPostTags(null));
  const authors = useLoad("post-browser-authors", () => listPostAuthors());

  function apply(next: Partial<typeof current>) {
    const merged = { ...current, ...next };
    const sp = new URLSearchParams();
    if (merged.q) sp.set("q", merged.q.slice(0, 200));
    if (merged.tag) sp.set("tag", merged.tag);
    if (merged.author) sp.set("author", merged.author);
    const query = sp.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  const tagOptions = tags.result?.ok ? tags.result.data.items : [];
  const authorOptions = authors.result?.ok ? authors.result.data : [];
  const any = current.q || current.tag || current.author;

  return (
    <form
      role="search"
      aria-label="글 찾기"
      onSubmit={(e) => {
        e.preventDefault();
        apply({ q: q.trim() });
      }}
      className="mb-6 grid gap-3 rounded-xl bg-white p-4 shadow-sm ring-1 ring-stone-200/60 sm:grid-cols-[1fr_auto_auto]"
    >
      <div className="flex gap-2 sm:col-span-3">
        <label htmlFor="post-search" className="sr-only">
          제목·본문 검색
        </label>
        <input
          id="post-search"
          type="search"
          value={q}
          maxLength={200}
          onChange={(e) => setQ(e.target.value)}
          placeholder="제목·본문에서 찾기"
          className={inputClass}
        />
        <button type="submit" className="shrink-0 rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white hover:bg-stone-800">
          찾기
        </button>
      </div>
      <label className="flex items-center gap-2 text-sm text-stone-700">
        <span className="shrink-0">태그</span>
        <select value={current.tag} onChange={(e) => apply({ tag: e.target.value })} className={inputClass}>
          <option value="">전체</option>
          {/* 고른 태그가 첫 100개 밖이어도 보이게 */}
          {current.tag && !tagOptions.some((t) => t.tag === current.tag) && <option value={current.tag}>{current.tag}</option>}
          {tagOptions.map((t) => (
            <option key={t.tag} value={t.tag}>
              {t.tag} ({t.count})
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm text-stone-700">
        <span className="shrink-0">작성자</span>
        <select value={current.author} onChange={(e) => apply({ author: e.target.value })} className={inputClass}>
          <option value="">전체</option>
          {current.author && !authorOptions.some((a) => a.login === current.author) && <option value={current.author}>{current.author}</option>}
          {authorOptions.map((a) => (
            <option key={a.login} value={a.login}>
              {a.login} ({a.count})
            </option>
          ))}
        </select>
      </label>
      {any && (
        <button
          type="button"
          onClick={() => {
            setQ("");
            apply({ q: "", tag: "", author: "" });
          }}
          className="self-center text-sm font-medium text-indigo-600 hover:text-indigo-800"
        >
          조건 지우기
        </button>
      )}
    </form>
  );
}

function Results() {
  const { q, tag, author } = useFilters();
  const list = usePagedList(`public-posts-browse:${q}\u0000${tag}\u0000${author}`, (cursor) =>
    listPublicPosts(cursor, { q: q || undefined, tag: tag || undefined, author: author || undefined, limit: PAGE })
  );
  const filtered = !!(q || tag || author);

  if (list.failure && list.items.length === 0) return <ErrorNotice failure={list.failure} prefix="글을 불러오지 못했습니다." />;
  if (!list.loaded) return <PostListSkeleton />;
  return (
    <div className="space-y-4">
      {/* 조건을 바꾸면 초점은 그대로 두고 건수만 화면 읽기 도구에 알린다 */}
      {list.total !== null && (
        <p role="status" className="text-sm text-stone-600">
          {filtered ? "찾은 글" : "전체"} {list.total}편{list.total > list.items.length && <> 중 {list.items.length}편 표시</>}
        </p>
      )}
      {list.items.length === 0 ? (
        <EmptyPosts text={filtered ? "조건에 맞는 글이 없습니다" : undefined} />
      ) : (
        <PostList posts={list.items} />
      )}
      {list.failure && <ErrorNotice failure={list.failure} prefix="다음 글을 불러오지 못했습니다." />}
      {list.nextCursor && (
        <button
          onClick={() => void list.loadMore()}
          disabled={list.loading}
          className="w-full rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-900 hover:bg-stone-50 disabled:opacity-50"
        >
          {list.loading ? "불러오는 중..." : `다음 ${PAGE}편 더 보기`}
        </button>
      )}
    </div>
  );
}

export default function PostBrowser() {
  if (!API_ENABLED) return <Notice tone="info">{NO_API_POSTS}</Notice>;
  return (
    <Suspense fallback={<PostListSkeleton />}>
      <Filters />
      <Results />
    </Suspense>
  );
}
