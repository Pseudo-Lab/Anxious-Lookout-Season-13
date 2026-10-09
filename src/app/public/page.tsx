"use client";

import Link from "next/link";
import { API_ENABLED } from "@/lib/constants";
import { listPublicDocuments } from "@/lib/research/api";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

function PublicList() {
  const list = usePagedList("public-documents", (cursor) => listPublicDocuments(cursor));
  return (
    <div className="space-y-3">
      {list.failure && <ErrorNotice failure={list.failure} prefix="공개 문서를 불러오지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <Notice tone="info">아직 공개된 문서가 없습니다.</Notice>}
      {list.items.length > 0 && (
        <ul className="divide-y divide-stone-100 rounded-2xl bg-white shadow-sm ring-1 ring-stone-200/60">
          {list.items.map((d) => (
            <li key={d.id}>
              <Link href={`/public/document/?id=${encodeURIComponent(d.documentId)}`} className="block px-5 py-4 hover:bg-stone-50">
                <p className="font-medium text-stone-900">{d.title || "(제목 없음)"}</p>
                <p className="mt-1 text-xs text-stone-500">
                  {d.author.login} · {formatDateTime(d.publishedAt)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {list.loading && <p className="text-sm text-stone-500">불러오는 중...</p>}
      {list.nextCursor && !list.loading && (
        <button onClick={() => void list.loadMore()} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
    </div>
  );
}

export default function PublicDocumentsPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-2 text-2xl font-bold text-stone-900">공개 문서</h1>
      <p className="mb-6 text-sm text-stone-500">작성자가 공개한 문서와, 작성자가 함께 공개하기로 고른 참고 자료입니다.</p>
      {API_ENABLED ? <PublicList /> : <Notice tone="info">이 정적 사이트에는 API 서버가 없어 공개 문서를 제공하지 않습니다.</Notice>}
    </div>
  );
}
