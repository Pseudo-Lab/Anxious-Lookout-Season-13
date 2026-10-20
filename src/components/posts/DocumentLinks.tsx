"use client";

import { useDeferredValue, useState } from "react";
import { listPostDocumentOptions } from "@/lib/posts/api";
import { getDocument } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import ErrorNotice from "@/components/research/ErrorNotice";
import { inputClass } from "@/components/research/ItemForms";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import DocTitle from "./DocTitle";

export const DOCUMENT_MAX = 50;

// 연결 문서 본문(현재 버전). 글에 복제되지 않고 열람만 한다.
function DocumentBody({ id }: { id: string }) {
  const { result } = useLoad(`document:${id}`, () => getDocument(id));
  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="문서를 열지 못했습니다." />;
  return (
    <div className="max-h-[50vh] overflow-auto rounded-lg bg-white px-4 py-3 ring-1 ring-stone-200">
      <p className="mb-2 text-xs text-stone-500">현재 v{result.data.latestVersion.number} · 이 화면에서는 읽기만 합니다.</p>
      <SafeMarkdown content={result.data.content} />
    </div>
  );
}

function Search({ linked, onAdd }: { linked: string[]; onAdd: (id: string) => void }) {
  const [q, setQ] = useState("");
  const query = useDeferredValue(q.trim());
  const list = usePagedList(`post-document-options:${query}`, (cursor) => listPostDocumentOptions(query, cursor));
  const full = linked.length >= DOCUMENT_MAX;
  return (
    <div className="space-y-2">
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        maxLength={200}
        placeholder="내 문서 제목으로 찾기"
        aria-label="연결할 문서 찾기"
        className={inputClass}
      />
      {list.failure && <ErrorNotice failure={list.failure} prefix="문서를 찾지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <p className="text-xs text-stone-500">찾은 문서가 없습니다.</p>}
      <ul className="max-h-48 space-y-1 overflow-auto text-sm">
        {list.items.map((d) => {
          const on = linked.includes(d.id);
          return (
            <li key={d.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate">{d.title || "(제목 없음)"}</span>
              <button
                type="button"
                onClick={() => onAdd(d.id)}
                disabled={on || full}
                aria-label={`${d.title || "(제목 없음)"} 연결`}
                className="shrink-0 text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:text-stone-400"
              >
                {on ? "연결됨" : "연결"}
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
      {full && <p className="text-xs text-amber-800">연결 문서는 {DOCUMENT_MAX}개까지입니다.</p>}
    </div>
  );
}

/** 글이 바탕으로 삼는 내 문서. 연결·해제는 초안(documentIds)으로 자동 저장되고, 저장(새 버전)할 때 버전에 함께 남는다. */
export default function DocumentLinks({
  ids,
  onChange,
  readOnly,
}: {
  ids: string[];
  onChange: (ids: string[]) => void;
  readOnly?: boolean;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  return (
    <section aria-label="연결 문서" className="space-y-3 rounded-lg p-3 ring-1 ring-stone-200">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-stone-900">바탕이 된 문서 ({ids.length})</h2>
        {!readOnly && (
          <button type="button" onClick={() => setAdding(!adding)} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
            {adding ? "찾기 닫기" : "문서 연결"}
          </button>
        )}
      </div>
      <p className="text-xs text-stone-500">
        발행하면 연결 문서의 제목이 글에 함께 공개되고, 공개된 문서만 링크됩니다. 문서 본문은 공개되지 않습니다. Codex도 연결 문서를 읽고 제안할 수 있습니다.
      </p>
      {adding && !readOnly && <Search linked={ids} onAdd={(id) => onChange(ids.includes(id) ? ids : [...ids, id])} />}
      {ids.length > 0 && (
        <ul className="space-y-2 text-sm">
          {ids.map((id) => (
            <li key={id} className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate">
                  <DocTitle id={id} />
                </span>
                <span className="flex shrink-0 gap-3 text-xs">
                  <button type="button" onClick={() => setOpen(open === id ? null : id)} aria-expanded={open === id} className="font-medium text-indigo-600 hover:text-indigo-800">
                    {open === id ? "닫기" : "열람"}
                  </button>
                  {!readOnly && (
                    <button type="button" onClick={() => onChange(ids.filter((x) => x !== id))} className="text-stone-500 hover:text-red-700">
                      연결 해제
                    </button>
                  )}
                </span>
              </div>
              {open === id && <DocumentBody id={id} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
