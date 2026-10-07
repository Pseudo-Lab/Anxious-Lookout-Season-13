"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { listDocuments, listMaterials, listSessions } from "@/lib/research/api";
import { usePagedList } from "@/lib/research/usePagedList";
import { CONTENT_KIND_LABELS } from "@/lib/research/types";
import { formatDateTime, hostOf } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

function ListFrame({
  failure,
  loading,
  loaded,
  empty,
  emptyText,
  hasMore,
  onMore,
  children,
}: {
  failure: Parameters<typeof ErrorNotice>[0]["failure"] | null;
  loading: boolean;
  loaded: boolean;
  empty: boolean;
  emptyText: string;
  hasMore: boolean;
  onMore: () => void;
  children: ReactNode;
}) {
  return (
    <div className="space-y-3">
      {failure && <ErrorNotice failure={failure} prefix="목록을 불러오지 못했습니다." />}
      {!failure && loaded && empty && <Notice tone="info">{emptyText}</Notice>}
      {!empty && <ul className="divide-y divide-stone-100 rounded-2xl bg-white shadow-sm ring-1 ring-stone-200/60">{children}</ul>}
      {loading && <p className="text-sm text-stone-500">불러오는 중...</p>}
      {hasMore && !loading && (
        <button onClick={onMore} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
    </div>
  );
}

function ArchivedToggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-stone-500">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      보관한 항목 보기
    </label>
  );
}

export function MaterialList() {
  const [archived, setArchived] = useState(false);
  const list = usePagedList(`materials:${archived}`, (cursor) => listMaterials(cursor, archived));
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ArchivedToggle value={archived} onChange={setArchived} />
        <Link href="/research/material/new/" className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700">
          새 자료
        </Link>
      </div>
      <ListFrame
        failure={list.failure}
        loading={list.loading}
        loaded={list.loaded}
        empty={list.items.length === 0}
        emptyText={archived ? "보관한 자료가 없습니다." : "아직 저장한 자료가 없습니다."}
        hasMore={list.nextCursor !== null}
        onMore={() => void list.loadMore()}
      >
        {list.items.map((m) => (
          <li key={m.id}>
            <Link href={`/research/material/?id=${encodeURIComponent(m.id)}`} className="block px-5 py-4 hover:bg-stone-50">
              <p className="font-medium text-stone-900">{m.title || "(제목 없음)"}</p>
              <p className="mt-1 text-xs text-stone-500">
                {hostOf(m.sourceUrl)} · {CONTENT_KIND_LABELS[m.contentKind]} · 수집 {formatDateTime(m.collectedAt)} · v
                {m.latestVersion.number}
              </p>
            </Link>
          </li>
        ))}
      </ListFrame>
    </section>
  );
}

export function DocumentList() {
  const [archived, setArchived] = useState(false);
  const list = usePagedList(`documents:${archived}`, (cursor) => listDocuments(cursor, archived));
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ArchivedToggle value={archived} onChange={setArchived} />
        <Link href="/research/document/new/" className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700">
          새 문서
        </Link>
      </div>
      <ListFrame
        failure={list.failure}
        loading={list.loading}
        loaded={list.loaded}
        empty={list.items.length === 0}
        emptyText={archived ? "보관한 문서가 없습니다." : "아직 작성한 문서가 없습니다."}
        hasMore={list.nextCursor !== null}
        onMore={() => void list.loadMore()}
      >
        {list.items.map((d) => (
          <li key={d.id}>
            <Link href={`/research/document/?id=${encodeURIComponent(d.id)}`} className="block px-5 py-4 hover:bg-stone-50">
              <p className="flex items-center gap-2 font-medium text-stone-900">
                {d.title || "(제목 없음)"}
                {d.publication && (
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">공개</span>
                )}
              </p>
              <p className="mt-1 text-xs text-stone-500">
                수정 {formatDateTime(d.updatedAt)} · v{d.latestVersion.number}
              </p>
            </Link>
          </li>
        ))}
      </ListFrame>
    </section>
  );
}

export const SESSION_STATE_LABELS = { idle: "대기", running: "진행 중", failed: "실패" } as const;

export function SessionList({ action }: { action?: ReactNode }) {
  const list = usePagedList("sessions", (cursor) => listSessions(cursor));
  return (
    <section className="space-y-3">
      {action}
      <ListFrame
        failure={list.failure}
        loading={list.loading}
        loaded={list.loaded}
        empty={list.items.length === 0}
        emptyText="아직 대화가 없습니다."
        hasMore={list.nextCursor !== null}
        onMore={() => void list.loadMore()}
      >
        {list.items.map((s) => (
          <li key={s.id}>
            <Link href={`/research/session/?id=${encodeURIComponent(s.id)}`} className="block px-5 py-4 hover:bg-stone-50">
              <p className="font-medium text-stone-900">{s.title || "(제목 없음)"}</p>
              <p className="mt-1 text-xs text-stone-500">
                {SESSION_STATE_LABELS[s.state]} · 최근 {formatDateTime(s.updatedAt)}
              </p>
            </Link>
          </li>
        ))}
      </ListFrame>
    </section>
  );
}
