"use client";

import Link from "next/link";
import { listPostDrafts, listPosts } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import { NewPostProposals } from "@/components/posts/PostProposals";

export const NEW_POST_PATH = "/write/new/";

// 작성 중인 새 글 초안(아직 글이 아닌 것). 목록 API는 기존 글 초안도 함께 주므로 걸러 낸다.
function NewDrafts() {
  const list = usePagedList("write:new-drafts", (cursor) => listPostDrafts(cursor));
  const drafts = list.items.filter((d) => d.postId === null);
  if (!list.failure && !drafts.length && !list.nextCursor) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-stone-700">작성 중인 새 글</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="초안 목록을 불러오지 못했습니다." describe={postErrorMessage} />}
      <ul className="divide-y divide-stone-100 rounded-xl bg-white text-sm ring-1 ring-stone-200">
        {drafts.map((d) => (
          <li key={d.id} className="flex items-center justify-between gap-3 px-4 py-2">
            <Link href={`${NEW_POST_PATH}?draft=${encodeURIComponent(d.id)}`} className="min-w-0 truncate text-indigo-600 hover:text-indigo-800">
              {d.title || "(제목 없음)"}
            </Link>
            <span className="shrink-0 text-xs text-stone-500">자동 저장 {formatDateTime(d.updatedAt)}</span>
          </li>
        ))}
      </ul>
      {list.nextCursor && (
        <button type="button" onClick={() => void list.loadMore()} disabled={list.loading} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
          초안 더 보기
        </button>
      )}
    </section>
  );
}

function MyPosts() {
  const list = usePagedList("write:posts", (cursor) => listPosts(cursor));
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-stone-700">내 글</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="글 목록을 불러오지 못했습니다." describe={postErrorMessage} />}
      {list.loaded && !list.failure && list.items.length === 0 && <Notice tone="info">아직 저장한 글이 없습니다. ‘새 글 쓰기’로 시작하세요.</Notice>}
      {list.items.length > 0 && (
        <ul className="divide-y divide-stone-100 rounded-xl bg-white ring-1 ring-stone-200">
          {list.items.map((p) => (
            <li key={p.id}>
              <Link href={`/write/?id=${encodeURIComponent(p.id)}`} className="block px-4 py-3 hover:bg-stone-50">
                <p className="font-medium text-stone-900">{p.latestVersion.title || "(제목 없음)"}</p>
                <p className="mt-1 text-xs text-stone-500">
                  v{p.latestVersion.number} · 수정 {formatDateTime(p.updatedAt)} ·{" "}
                  {p.publication ? <span className="text-emerald-700">발행 중</span> : <span>발행 안 함</span>}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {!list.loaded && <p className="text-sm text-stone-500">불러오는 중...</p>}
      {list.nextCursor && (
        <button type="button" onClick={() => void list.loadMore()} disabled={list.loading} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
    </section>
  );
}

export default function PostIndex() {
  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-stone-900">글 쓰기</h1>
        <Link href={NEW_POST_PATH} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
          새 글 쓰기
        </Link>
      </div>
      <NewPostProposals />
      <NewDrafts />
      <MyPosts />
    </div>
  );
}
