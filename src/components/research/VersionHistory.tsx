"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { FetchResult } from "@/lib/api/client";
import { getVersion, listVersions } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { CONTENT_KIND_LABELS, type ItemType, type Page, type Version } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import TextDiff from "@/components/research/TextDiff";

/** 버전 목록·본문을 가져오는 곳(자료·문서 #22, 글 #28). 렌더마다 새로 만들어도 key가 같으면 다시 부르지 않는다. */
export interface VersionSource<V extends VersionLike> {
  // 캐시·다시 불러오기 key 앞부분(대상 종류·ID)
  key: string;
  // 목록 항목에는 본문이 없어도 된다
  list: (cursor: string | null) => Promise<FetchResult<Page<VersionLike>>>;
  get: (versionId: string) => Promise<FetchResult<V>>;
  // 본문 위 메타 표시(출처 등)
  meta?: (v: V) => ReactNode;
  // 본문 비교 위에 보일 메타 변경(슬러그·태그 등)
  metaDiff?: (before: V, after: V) => ReactNode;
  ownImages?: boolean;
  // 본문 표시·비교를 바꾼다(#32 글은 블록 문서). 없으면 Markdown 본문으로 다룬다(자료·문서).
  body?: (v: V) => ReactNode;
  bodyDiff?: (before: V, after: V) => ReactNode;
}
interface VersionLike {
  id: string;
  number: number;
  title: string;
  content?: unknown;
  createdAt: string;
}

const markdownOf = (v: VersionLike) => (typeof v.content === "string" ? v.content : "");

function VersionBody<V extends VersionLike>({
  source,
  versionId,
  onRestore,
}: {
  source: VersionSource<V>;
  versionId: string;
  onRestore?: (v: V) => void;
}) {
  const { result } = useLoad(`${source.key}:${versionId}`, () => source.get(versionId));
  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="버전을 불러오지 못했습니다." />;
  const v = result.data;
  return (
    <div className="space-y-3 rounded-lg bg-stone-50 p-4">
      <p className="font-medium text-stone-900">{v.title}</p>
      {source.meta?.(v)}
      {source.body ? source.body(v) : <SafeMarkdown content={markdownOf(v)} ownImages={source.ownImages} />}
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
function VersionCompare<V extends VersionLike>({ source, beforeId, afterId }: { source: VersionSource<V>; beforeId: string; afterId: string }) {
  const before = useLoad(`${source.key}:${beforeId}`, () => source.get(beforeId)).result;
  const after = useLoad(`${source.key}:${afterId}`, () => source.get(afterId)).result;
  if (!before || !after) return <p className="text-sm text-stone-500">비교할 버전을 불러오는 중...</p>;
  if (!before.ok) return <ErrorNotice failure={before.failure} prefix="기준 버전을 불러오지 못했습니다." />;
  if (!after.ok) return <ErrorNotice failure={after.failure} prefix="비교할 버전을 불러오지 못했습니다." />;
  return (
    <>
      {source.metaDiff?.(before.data, after.data)}
      {source.bodyDiff ? (
        source.bodyDiff(before.data, after.data)
      ) : (
        <TextDiff
          key={`${beforeId}:${afterId}`}
          before={markdownOf(before.data)}
          after={markdownOf(after.data)}
          beforeTitle={before.data.title}
          afterTitle={after.data.title}
        />
      )}
    </>
  );
}

/** 이전 내용 버전 목록(자료·문서). 각 버전은 저장 당시의 전체 스냅샷이다. */
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
  const source: VersionSource<Version> = {
    key: `${type}:${itemId}`,
    list: (cursor) => listVersions(type, itemId, cursor),
    get: (versionId) => getVersion(type, itemId, versionId),
    meta: (v) =>
      v.sourceUrl ? (
        <p className="break-all text-xs text-stone-500">
          출처 {v.sourceUrl}
          {v.collectedAt && ` · 수집 ${formatDateTime(v.collectedAt)}`}
          {v.contentKind && ` · ${CONTENT_KIND_LABELS[v.contentKind]}`}
        </p>
      ) : null,
  };
  return <VersionHistoryView source={source} latestVersionId={latestVersionId} onRestore={onRestore} />;
}

export function VersionHistoryView<V extends VersionLike>({
  source,
  latestVersionId,
  onRestore,
}: {
  source: VersionSource<V>;
  latestVersionId: string;
  onRestore?: (v: V) => void;
}) {
  const list = usePagedList(`${source.key}:${latestVersionId}`, (cursor) => source.list(cursor));
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
            <VersionCompare source={source} beforeId={compare.before} afterId={compare.after} />
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
                <VersionBody source={source} versionId={v.id} onRestore={v.id === latestVersionId ? undefined : onRestore} />
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
