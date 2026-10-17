"use client";

import { useEffect, useRef, useState } from "react";
import { getVersion, listVersions } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { CONTENT_KIND_LABELS, type ItemType, type Version } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import TextDiff from "@/components/research/TextDiff";

function VersionBody({
  type,
  itemId,
  versionId,
  onRestore,
}: {
  type: ItemType;
  itemId: string;
  versionId: string;
  onRestore?: (v: Version) => void;
}) {
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
      {onRestore && (
        // 되돌리기 = 이 내용을 편집기에 불러와 새 버전으로 저장. 원래 버전·공개본은 지우거나 바꾸지 않는다.
        <button onClick={() => onRestore(v)} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          이 버전으로 되돌리기(편집기에 불러오기)
        </button>
      )}
    </div>
  );
}

// 두 저장 버전의 비교. 각 버전은 불변 스냅샷이라 같은 ID면 언제나 같은 결과다.
function VersionCompare({ type, itemId, beforeId, afterId }: { type: ItemType; itemId: string; beforeId: string; afterId: string }) {
  const before = useLoad(`${type}:${itemId}:${beforeId}`, () => getVersion(type, itemId, beforeId)).result;
  const after = useLoad(`${type}:${itemId}:${afterId}`, () => getVersion(type, itemId, afterId)).result;
  if (!before || !after) return <p className="text-sm text-stone-500">비교할 버전을 불러오는 중...</p>;
  if (!before.ok) return <ErrorNotice failure={before.failure} prefix="기준 버전을 불러오지 못했습니다." />;
  if (!after.ok) return <ErrorNotice failure={after.failure} prefix="비교할 버전을 불러오지 못했습니다." />;
  return (
    <TextDiff
      key={`${beforeId}:${afterId}`}
      before={before.data.content}
      after={after.data.content}
      beforeTitle={before.data.title}
      afterTitle={after.data.title}
    />
  );
}

/** 이전 내용 버전 목록. 각 버전은 저장 당시의 전체 스냅샷이다. */
export default function VersionHistory({
  type,
  itemId,
  latestVersionId,
  onRestore,
}: {
  type: ItemType;
  itemId: string;
  // 새 버전이 생기면 목록을 다시 불러오기 위한 key
  latestVersionId: string;
  // #22 문서: 이전 버전 내용을 편집기로 불러온다(현재 버전에는 표시하지 않음)
  onRestore?: (v: Version) => void;
}) {
  const list = usePagedList(`${type}:${itemId}:${latestVersionId}`, (cursor) => listVersions(type, itemId, cursor));
  const [open, setOpen] = useState<string | null>(null);
  // 비교: 기준(이전)과 대상(이후) 버전 ID. 목록은 최신순이다.
  const [compare, setCompare] = useState<{ before: string; after: string } | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  // 목록 아래쪽에서 비교를 열면 위의 비교 영역으로 이동하고 초점을 옮긴다.
  // 비교 영역이 그려진 뒤에 초점을 옮긴다(그리기 전에 옮기면 놓친다).
  const [focusRequest, setFocusRequest] = useState(0);
  const openCompare = (before: string, after: string) => {
    setCompare({ before, after });
    setFocusRequest((n) => n + 1);
  };
  useEffect(() => {
    if (focusRequest) panel.current?.focus();
  }, [focusRequest]);
  const items = list.items;
  const label = (id: string) => {
    const v = items.find((x) => x.id === id);
    return v ? `v${v.number}` : "선택한 버전";
  };
  // 목록에서 바로 아래(더 이전) 버전. 아직 불러오지 않았으면 없다.
  const previousOf = (id: string) => {
    const k = items.findIndex((x) => x.id === id);
    return k >= 0 && k + 1 < items.length ? items[k + 1].id : null;
  };

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-stone-800">버전 기록</h2>
        {items.length >= 2 && !compare && (
          <button
            onClick={() => openCompare(items[1].id, items[0].id)}
            className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
          >
            버전 비교
          </button>
        )}
      </div>
      {list.failure && <ErrorNotice failure={list.failure} prefix="버전 목록을 불러오지 못했습니다." />}
      {compare && (
        <div ref={panel} tabIndex={-1} role="region" aria-label="버전 비교" className="mb-4 scroll-mt-4 space-y-3 rounded-lg p-4 ring-1 ring-indigo-200 focus:outline-none focus-visible:ring-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <label className="flex items-center gap-1">
              <span className="text-stone-600">기준</span>
              <select
                value={compare.before}
                onChange={(e) => setCompare({ ...compare, before: e.target.value })}
                className="rounded border border-stone-300 px-2 py-1"
              >
                {items.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.number} · {formatDateTime(v.createdAt)}
                  </option>
                ))}
              </select>
            </label>
            <span aria-hidden="true" className="text-stone-400">→</span>
            <label className="flex items-center gap-1">
              <span className="text-stone-600">비교</span>
              <select
                value={compare.after}
                onChange={(e) => setCompare({ ...compare, after: e.target.value })}
                className="rounded border border-stone-300 px-2 py-1"
              >
                {items.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.number} · {formatDateTime(v.createdAt)}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => setCompare({ before: compare.after, after: compare.before })}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
            >
              방향 바꾸기
            </button>
            <button onClick={() => setCompare(null)} className="ml-auto text-xs text-stone-500 hover:text-stone-800">
              비교 닫기
            </button>
          </div>
          <p className="text-xs text-stone-500">
            {label(compare.before)}에서 {label(compare.after)}로 바뀐 내용입니다. 저장된 원문(Markdown)을 줄 단위로 비교합니다.
            {list.nextCursor && " 더 이전 버전은 아래 '이전 버전 더 보기'로 불러온 뒤 고를 수 있습니다."}
          </p>
          {compare.before === compare.after ? (
            <p className="text-sm text-stone-600">같은 버전을 골랐습니다. 다른 버전을 고르세요.</p>
          ) : (
            <VersionCompare type={type} itemId={itemId} beforeId={compare.before} afterId={compare.after} />
          )}
        </div>
      )}
      <ul className="space-y-2">
        {items.map((v) => (
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
              <div className="space-y-2 px-4 pb-4">
                <VersionBody type={type} itemId={itemId} versionId={v.id} onRestore={v.id === latestVersionId ? undefined : onRestore} />
                {previousOf(v.id) && (
                  <button
                    onClick={() => openCompare(previousOf(v.id)!, v.id)}
                    className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
                  >
                    이전 버전과 비교
                  </button>
                )}
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
