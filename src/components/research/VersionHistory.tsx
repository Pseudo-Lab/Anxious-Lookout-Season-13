"use client";

import { useState } from "react";
import { getVersion, listVersions } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { CONTENT_KIND_LABELS, type ItemType } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import SafeMarkdown from "@/components/research/SafeMarkdown";

function VersionBody({ type, itemId, versionId }: { type: ItemType; itemId: string; versionId: string }) {
  const { result } = useLoad(`${type}:${itemId}:${versionId}`, () => getVersion(type, itemId, versionId));
  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="버전을 불러오지 못했습니다." />;
  const v = result.data;
  return (
    <div className="space-y-3 rounded-lg bg-stone-50 p-4">
      <p className="font-medium text-stone-900">{v.title}</p>
      {v.sourceUrl && (
        <p className="break-all text-xs text-stone-500">
          출처 {v.sourceUrl}
          {v.collectedAt && ` · 수집 ${formatDateTime(v.collectedAt)}`}
          {v.contentKind && ` · ${CONTENT_KIND_LABELS[v.contentKind]}`}
        </p>
      )}
      <SafeMarkdown content={v.content} />
    </div>
  );
}

/** 이전 내용 버전 목록. 각 버전은 저장 당시의 전체 스냅샷이다. */
export default function VersionHistory({
  type,
  itemId,
  latestVersionId,
}: {
  type: ItemType;
  itemId: string;
  // 새 버전이 생기면 목록을 다시 불러오기 위한 key
  latestVersionId: string;
}) {
  const list = usePagedList(`${type}:${itemId}:${latestVersionId}`, (cursor) => listVersions(type, itemId, cursor));
  const [open, setOpen] = useState<string | null>(null);

  return (
    <section>
      <h2 className="mb-3 text-lg font-semibold text-stone-800">버전 기록</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="버전 목록을 불러오지 못했습니다." />}
      <ul className="space-y-2">
        {list.items.map((v) => (
          <li key={v.id} className="rounded-lg ring-1 ring-stone-200">
            <button
              onClick={() => setOpen(open === v.id ? null : v.id)}
              aria-expanded={open === v.id}
              className="flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm hover:bg-stone-50"
            >
              <span>
                <span className="font-mono text-xs text-stone-500">v{v.number}</span>{" "}
                <span className="text-stone-800">{v.title}</span>
                {v.id === latestVersionId && <span className="ml-2 text-xs text-indigo-600">현재</span>}
              </span>
              <span className="shrink-0 text-xs text-stone-500">{formatDateTime(v.createdAt)}</span>
            </button>
            {open === v.id && (
              <div className="px-4 pb-4">
                <VersionBody type={type} itemId={itemId} versionId={v.id} />
              </div>
            )}
          </li>
        ))}
      </ul>
      {list.loading && <p className="mt-2 text-sm text-stone-500">불러오는 중...</p>}
      {list.nextCursor && !list.loading && (
        <button onClick={() => void list.loadMore()} className="mt-2 text-sm font-medium text-indigo-600 hover:text-indigo-800">
          이전 버전 더 보기
        </button>
      )}
    </section>
  );
}
