"use client";

import { useDeferredValue, useState } from "react";
import { listPostMaterialOptions } from "@/lib/posts/api";
import { getMaterial } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import ErrorNotice from "@/components/research/ErrorNotice";
import { inputClass } from "@/components/research/ItemForms";
import { safeHref } from "@/components/research/SafeMarkdown";
import DocTitle from "./DocTitle";

export const MATERIAL_MAX = 50;

/** 선택 자료 ID의 현재 제목(본인 자료). 읽지 못하면(보관·삭제·권한) 그렇다고 표시한다. */
export function MaterialTitle({ id }: { id: string }) {
  const { result } = useLoad(`material:${id}`, () => getMaterial(id));
  if (!result) return <span className="text-stone-500">자료 확인 중...</span>;
  if (!result.ok) return <span className="text-stone-500">(열 수 없는 자료)</span>;
  return (
    <span>
      {result.data.title || "(제목 없음)"}
      {result.data.archived && <span className="ml-1 text-xs text-amber-700">(보관됨: 빼야 발행할 수 있습니다)</span>}
    </span>
  );
}

/** 자료 원본 주소. http(s) 주소만 새 창 링크로, 그 밖의 값은 글자로만 보인다. */
export function SourceLink({ url, className = "" }: { url: string; className?: string }) {
  if (!url) return null;
  const href = safeHref(url);
  if (!href || href.startsWith("#") || href.startsWith("mailto:")) return <span className={`break-all text-stone-500 ${className}`}>{url}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className={`break-all text-indigo-600 hover:text-indigo-800 ${className}`}>
      {url}
    </a>
  );
}

function MaterialSource({ id }: { id: string }) {
  const { result } = useLoad(`material:${id}`, () => getMaterial(id));
  return result?.ok ? <SourceLink url={result.data.sourceUrl} className="text-xs" /> : null;
}

function Search({ selected, onAdd }: { selected: string[]; onAdd: (id: string) => void }) {
  const [q, setQ] = useState("");
  const query = useDeferredValue(q.trim());
  const list = usePagedList(`post-material-options:${query}`, (cursor) => listPostMaterialOptions(query, cursor));
  const full = selected.length >= MATERIAL_MAX;
  return (
    <div className="space-y-2">
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        maxLength={200}
        placeholder="내 자료 제목으로 찾기"
        aria-label="추가할 자료 찾기"
        className={inputClass}
      />
      {list.failure && <ErrorNotice failure={list.failure} prefix="자료를 찾지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <p className="text-xs text-stone-500">찾은 자료가 없습니다.</p>}
      <ul className="max-h-48 space-y-1 overflow-auto text-sm">
        {list.items.map((m) => {
          const on = selected.includes(m.id);
          return (
            <li key={m.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate">{m.title || "(제목 없음)"}</span>
              <button
                type="button"
                onClick={() => onAdd(m.id)}
                disabled={on || full}
                aria-label={`${m.title || "(제목 없음)"} 추가`}
                className="shrink-0 text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:text-stone-400"
              >
                {on ? "담김" : "추가"}
              </button>
            </li>
          );
        })}
      </ul>
      {list.nextCursor && (
        <button type="button" onClick={() => void list.loadMore()} disabled={list.loading} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
          더 찾기
        </button>
      )}
      {full && <p className="text-xs text-amber-800">바탕이 된 자료는 {MATERIAL_MAX}개까지입니다.</p>}
    </div>
  );
}

/**
 * 글이 바탕으로 삼는 내 자료(#31 W3). 문서를 연결하면 그 문서가 가리키는 자료가 담기고(sources에 그 문서), 직접 추가할 수도 있다.
 * 어느 쪽이든 뺄 수 있으며 선택은 초안(materialIds)으로 자동 저장된다. 문서 연결을 해제해도 담긴 자료는 그대로 둔다.
 */
export default function MaterialLinks({
  ids,
  sources,
  onAdd,
  onRemove,
  notice,
  readOnly,
}: {
  ids: string[];
  // 자료 ID → 그 자료를 가리키는 연결 문서(없으면 직접 추가)
  sources: Record<string, string[]>;
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
  notice?: React.ReactNode;
  readOnly?: boolean;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <section aria-label="바탕이 된 자료" className="space-y-3 rounded-lg p-3 ring-1 ring-stone-200">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-stone-900">바탕이 된 자료 ({ids.length})</h2>
        {!readOnly && (
          <button type="button" onClick={() => setAdding(!adding)} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
            {adding ? "찾기 닫기" : "자료 추가"}
          </button>
        )}
      </div>
      <p className="text-xs text-stone-500">
        문서를 연결하면 그 문서가 가리키는 자료가 자동으로 담깁니다. 자료를 직접 추가하거나 뺄 수도 있습니다. 발행하면 자료의 <strong>제목과 원본 주소</strong>만
        공개되고 자료 본문·메모는 공개되지 않습니다.
      </p>
      {notice}
      {adding && !readOnly && <Search selected={ids} onAdd={onAdd} />}
      {ids.length > 0 && (
        <ul className="space-y-2 text-sm">
          {ids.map((id) => {
            const from = sources[id] ?? [];
            return (
              <li key={id} className="flex items-start justify-between gap-2">
                <span className="min-w-0">
                  <span className="block truncate">
                    <MaterialTitle id={id} />
                  </span>
                  <MaterialSource id={id} />
                  <span className="block text-xs text-stone-500">
                    {from.length > 0 ? (
                      <>
                        문서에서 자동 포함:{" "}
                        {from.map((d, i) => (
                          <span key={d}>
                            {i > 0 && ", "}
                            <DocTitle id={d} />
                          </span>
                        ))}
                      </>
                    ) : (
                      "직접 추가"
                    )}
                  </span>
                </span>
                {!readOnly && (
                  <button type="button" onClick={() => onRemove(id)} aria-label="자료 빼기" className="shrink-0 text-xs text-stone-500 hover:text-red-700">
                    빼기
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
