"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import { listDocuments, listMaterials, listSessions, restoreSession } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { CONTENT_KIND_LABELS, type SessionSummary } from "@/lib/research/types";
import { displayHost, formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import { useChat } from "@/components/chat/ChatProvider";
import Notice from "@/components/research/Notice";
import { documentPath, materialPath } from "@/lib/routes";

function ListFrame({
  failure,
  loading,
  loaded,
  empty,
  emptyText,
  hasMore,
  onMore,
  compact = false,
  children,
}: {
  failure: Parameters<typeof ErrorNotice>[0]["failure"] | null;
  loading: boolean;
  loaded: boolean;
  empty: boolean;
  emptyText: string;
  hasMore: boolean;
  onMore: () => void;
  /** 대화창처럼 좁은 곳: 카드 없이 구분선만 둔다. */
  compact?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="space-y-3">
      {failure && <ErrorNotice failure={failure} prefix="목록을 불러오지 못했습니다." />}
      {!failure && loaded && empty && <Notice tone="info">{emptyText}</Notice>}
      {!empty && (
        <ul className={compact ? "-mx-2 divide-y divide-stone-100" : "divide-y divide-stone-100 rounded-2xl bg-white shadow-sm ring-1 ring-stone-200/60"}>
          {children}
        </ul>
      )}
      {loading && <p className="text-sm text-stone-500">불러오는 중...</p>}
      {hasMore && !loading && (
        <button onClick={onMore} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
    </div>
  );
}

/** #119 I4: 체크박스 대신 켜고 끄는 스위치(값 "on"을 드러내지 않는다). */
function ArchivedToggle({ value, onChange, label = "보관한 항목 보기" }: { value: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      className="flex items-center gap-2 text-sm text-stone-700"
    >
      <span
        aria-hidden="true"
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${value ? "bg-indigo-600" : "bg-stone-300"}`}
      >
        <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${value ? "translate-x-4" : "translate-x-0.5"}`} />
      </span>
      {label}
    </button>
  );
}

// #119 I1: 목록 한 줄 정보를 배지로 나누고 뜻을 붙인다. 배지마다 화면 읽기 도구용 설명이 있고, 같은 설명을 "표시 설명"(키보드로 펼침)에 모아 둔다.
const KIND_HELP: Record<string, string> = {
  전체: "원문 전체를 저장한 자료",
  발췌: "원문 일부를 옮겨 적은 자료",
  요약: "원문을 내 말로 줄여 적은 자료",
};

function Badge({ children, label, tone = "stone" }: { children: ReactNode; label: string; tone?: "stone" | "indigo" | "emerald" }) {
  const tones = {
    stone: "bg-stone-100 text-stone-700",
    indigo: "bg-indigo-50 text-indigo-700",
    emerald: "bg-emerald-100 text-emerald-700",
  };
  return (
    <span title={label} className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      <span aria-hidden="true">{children}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

function Legend({ material }: { material: boolean }) {
  return (
    <details className="text-xs text-stone-600">
      <summary className="cursor-pointer select-none font-medium text-stone-700">표시 설명</summary>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {material && (
          <>
            <dt className="font-medium">전체 · 발췌 · 요약</dt>
            <dd>저장 범위. 전체 = 원문 전체, 발췌 = 일부를 옮겨 적음, 요약 = 내 말로 줄여 적음</dd>
          </>
        )}
        <dt className="font-medium">v1, v2 …</dt>
        <dd>버전 번호. 내용을 고쳐 저장할 때마다 하나씩 늘고, 이전 내용은 버전 기록에 남습니다</dd>
        <dt className="font-medium">연결 N</dt>
        <dd>이 항목과 이어 둔 다른 자료·문서 수(관계)</dd>
        {!material && (
          <>
            <dt className="font-medium">공개</dt>
            <dd>누구나 볼 수 있게 공개한 문서</dd>
          </>
        )}
      </dl>
    </details>
  );
}

const MATERIAL_SORTS = [
  { value: "updated", label: "최근 수정순" },
  { value: "created", label: "최근 추가순" },
  { value: "title", label: "제목순" },
  { value: "domain", label: "출처 도메인별" },
] as const;
const DOCUMENT_SORTS = MATERIAL_SORTS.filter((o) => o.value !== "domain");

type SortValue = (typeof MATERIAL_SORTS)[number]["value"];

const dayOf = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "short" });
};

/** #119 I2: 정렬에 맞춘 묶음 제목(도메인별·날짜별). 제목순은 묶지 않는다. 서버가 같은 순서로 주므로 페이지를 넘겨도 묶음이 이어진다. */
function groupKey<T extends { createdAt: string; updatedAt: string }>(sort: SortValue, item: T, host?: string): string | null {
  if (sort === "domain") return host || "출처 주소 없음";
  if (sort === "created") return `${dayOf(item.createdAt)} 추가`;
  if (sort === "updated") return `${dayOf(item.updatedAt)} 수정`;
  return null;
}

function ListControls({
  q,
  onQ,
  sort,
  onSort,
  sorts,
  placeholder,
}: {
  q: string;
  onQ: (q: string) => void;
  sort: SortValue;
  onSort: (s: SortValue) => void;
  sorts: readonly { value: SortValue; label: string }[];
  placeholder: string;
}) {
  const [text, setText] = useState(q);
  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        onQ(text.trim());
      }}
      className="flex flex-wrap gap-2"
    >
      <label className="min-w-0 flex-1">
        <span className="sr-only">{placeholder}</span>
        <input
          type="search"
          value={text}
          maxLength={200}
          onChange={(e) => {
            setText(e.target.value);
            // 지우면 바로 전체로 돌아간다
            if (!e.target.value) onQ("");
          }}
          placeholder={placeholder}
          className="w-full rounded-lg border border-stone-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
        />
      </label>
      <button type="submit" className="rounded-lg border border-stone-300 px-3 py-1.5 text-sm text-stone-900 hover:bg-stone-50">
        찾기
      </button>
      <label className="flex items-center gap-2 text-sm text-stone-700">
        <span className="sr-only">정렬</span>
        <select
          value={sort}
          onChange={(e) => onSort(e.target.value as SortValue)}
          className="rounded-lg border border-stone-300 px-2 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
        >
          {sorts.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    </form>
  );
}

/** #119 I3: 불러온 수와 전체 수 */
function Count({ shown, total, noun }: { shown: number; total: number | null; noun: string }) {
  if (total === null) return null;
  return (
    <p role="status" className="text-xs text-stone-600">
      {noun} {total}개{total > shown && ` 중 ${shown}개 표시`}
    </p>
  );
}

function GroupHeader({ children }: { children: ReactNode }) {
  return (
    <li role="presentation" className="bg-stone-50 px-5 py-1.5 text-xs font-semibold text-stone-600 first:rounded-t-2xl">
      {children}
    </li>
  );
}

export function MaterialList() {
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortValue>("updated");
  const list = usePagedList(`materials:${archived}:${sort}:${q}`, (cursor) => listMaterials(cursor, archived, { q: q || undefined, sort }));
  const hostOfItem = (m: (typeof list.items)[number]) => (m.sourceUrl ? displayHost(m.sourceUrl) : "");
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ArchivedToggle value={archived} onChange={setArchived} />
        <Link href="/research/material/new/" className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700">
          새 자료
        </Link>
      </div>
      <ListControls q={q} onQ={setQ} sort={sort} onSort={setSort} sorts={MATERIAL_SORTS} placeholder="자료 제목·출처 주소로 찾기" />
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Count shown={list.items.length} total={list.total} noun={q ? "찾은 자료" : "자료"} />
        <Legend material />
      </div>
      <ListFrame
        failure={list.failure}
        loading={list.loading}
        loaded={list.loaded}
        empty={list.items.length === 0}
        emptyText={q ? "찾은 자료가 없습니다." : archived ? "보관한 자료가 없습니다." : "아직 저장한 자료가 없습니다."}
        hasMore={list.nextCursor !== null}
        onMore={() => void list.loadMore()}
      >
        {list.items.flatMap((m, i) => {
          const host = hostOfItem(m);
          const group = groupKey(sort, m, host);
          const prev = i > 0 ? groupKey(sort, list.items[i - 1], hostOfItem(list.items[i - 1])) : null;
          const header = group !== null && group !== prev ? <GroupHeader key={`g:${group}:${m.id}`}>{group}</GroupHeader> : null;
          const kind = CONTENT_KIND_LABELS[m.contentKind];
          return [
            header,
            <li key={m.id}>
              <Link href={materialPath(m.id)} className="block px-5 py-4 hover:bg-stone-50">
                <p className="font-medium text-stone-900">{m.title || "(제목 없음)"}</p>
                {m.summary && <p className="mt-0.5 line-clamp-2 text-sm text-stone-900">{m.summary}</p>}
                <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-stone-600">
                  {host && sort !== "domain" && <span className="mr-1">{host}</span>}
                  <Badge label={`저장 범위: ${kind} (${KIND_HELP[kind] ?? ""})`}>{kind}</Badge>
                  <Badge label={`버전 ${m.latestVersion.number}`} tone="indigo">
                    v{m.latestVersion.number}
                  </Badge>
                  {m.relationCount !== undefined && m.relationCount > 0 && (
                    <Badge label={`연결된 자료·문서 ${m.relationCount}개`}>연결 {m.relationCount}</Badge>
                  )}
                  <span className="ml-1">
                    수집 <time dateTime={m.collectedAt}>{formatDateTime(m.collectedAt)}</time>
                  </span>
                </p>
              </Link>
            </li>,
          ];
        })}
      </ListFrame>
    </section>
  );
}

export function DocumentList() {
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortValue>("updated");
  const docSort = sort === "domain" ? "updated" : sort;
  const list = usePagedList(`documents:${archived}:${docSort}:${q}`, (cursor) => listDocuments(cursor, archived, { q: q || undefined, sort: docSort }));
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ArchivedToggle value={archived} onChange={setArchived} />
        <Link href="/research/document/new/" className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700">
          새 문서
        </Link>
      </div>
      <ListControls q={q} onQ={setQ} sort={docSort} onSort={setSort} sorts={DOCUMENT_SORTS} placeholder="문서 제목으로 찾기" />
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Count shown={list.items.length} total={list.total} noun={q ? "찾은 문서" : "문서"} />
        <Legend material={false} />
      </div>
      <ListFrame
        failure={list.failure}
        loading={list.loading}
        loaded={list.loaded}
        empty={list.items.length === 0}
        emptyText={q ? "찾은 문서가 없습니다." : archived ? "보관한 문서가 없습니다." : "아직 작성한 문서가 없습니다."}
        hasMore={list.nextCursor !== null}
        onMore={() => void list.loadMore()}
      >
        {list.items.flatMap((d, i) => {
          const group = groupKey(docSort, d);
          const prev = i > 0 ? groupKey(docSort, list.items[i - 1]) : null;
          const header = group !== null && group !== prev ? <GroupHeader key={`g:${group}:${d.id}`}>{group}</GroupHeader> : null;
          return [
            header,
            <li key={d.id}>
              <Link href={documentPath(d.id)} className="block px-5 py-4 hover:bg-stone-50">
                <p className="flex items-center gap-2 font-medium text-stone-900">
                  {d.title || "(제목 없음)"}
                  {d.publication && (
                    <Badge label="공개 문서(누구나 볼 수 있음)" tone="emerald">
                      공개
                    </Badge>
                  )}
                </p>
                {d.summary && <p className="mt-0.5 line-clamp-2 text-sm text-stone-900">{d.summary}</p>}
                <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-stone-600">
                  <Badge label={`버전 ${d.latestVersion.number}`} tone="indigo">
                    v{d.latestVersion.number}
                  </Badge>
                  {d.relationCount !== undefined && d.relationCount > 0 && (
                    <Badge label={`연결된 자료·문서 ${d.relationCount}개`}>연결 {d.relationCount}</Badge>
                  )}
                  <span className="ml-1">
                    수정 <time dateTime={d.updatedAt}>{formatDateTime(d.updatedAt)}</time>
                  </span>
                </p>
              </Link>
            </li>,
          ];
        })}
      </ListFrame>
    </section>
  );
}

// #119 N2: 상태 이름은 사용자가 할 수 있는 일로 읽히게, 뜻과 실패 시 할 일은 목록 위 "상태 설명"에 둔다.
export const SESSION_STATE_LABELS = { idle: "질문 가능", running: "답변 작성 중", failed: "실패" } as const;

function SessionLegend() {
  return (
    <details className="text-xs text-stone-600">
      <summary className="cursor-pointer select-none font-medium text-stone-700">상태 설명</summary>
      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="font-medium">질문 가능</dt>
        <dd>답변이 끝나 다음 질문을 보낼 수 있습니다.</dd>
        <dt className="font-medium">답변 작성 중</dt>
        <dd>답변을 만들고 있습니다. 대화창을 닫아도 계속됩니다.</dd>
        <dt className="font-medium">실패</dt>
        <dd>마지막 답변이 끝나지 못했습니다. 대화를 열어 같은 질문을 다시 보내거나 새 질문을 이어 보내면 됩니다. 이전 기록은 남아 있습니다.</dd>
      </dl>
    </details>
  );
}

/** #119 N4: 보관한 대화 되돌리기(목록에 다시 보이게). */
function RestoreSession({ session, onRestored }: { session: SessionSummary; onRestored: () => void }) {
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  async function restore() {
    const res = await run(`session:restore:${session.id}:${session.version}`, (ctx) => restoreSession(session.id, session.version, ctx));
    if (res.ok) onRestored();
    else setFailure(res.failure);
  }
  return (
    <>
      <button
        type="button"
        onClick={() => void restore()}
        disabled={busy}
        aria-label={`되돌리기: ${session.title || "(제목 없음)"}`}
        className="shrink-0 rounded-md border border-stone-300 px-2 py-1 text-xs text-stone-700 hover:bg-stone-50 disabled:opacity-50"
      >
        {busy ? "되돌리는 중..." : "되돌리기"}
      </button>
      {failure && <ErrorNotice failure={failure} prefix="되돌리지 못했습니다." />}
    </>
  );
}

// 대화는 공통 대화창에서 연다. 링크 주소는 기존 대화 링크(새 탭·복사)와 호환되게 유지한다.
export function SessionList({ action, compact = false }: { action?: ReactNode; compact?: boolean }) {
  const { openSession } = useChat();
  const [archived, setArchived] = useState(false);
  const [rev, setRev] = useState(0);
  const list = usePagedList(`sessions:${archived}:${rev}`, (cursor) => listSessions(cursor, archived));
  // #119 N1: 같은 제목이 여럿이면 시작 시각으로 구분한다
  const titleCount = new Map<string, number>();
  for (const s of list.items) titleCount.set(s.title, (titleCount.get(s.title) ?? 0) + 1);
  return (
    <section className="space-y-3">
      {!archived && action}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ArchivedToggle value={archived} onChange={setArchived} label="보관한 대화 보기" />
        {!archived && <SessionLegend />}
      </div>
      <ListFrame
        failure={list.failure}
        loading={list.loading}
        loaded={list.loaded}
        empty={list.items.length === 0}
        emptyText={archived ? "보관한 대화가 없습니다." : "아직 대화가 없습니다."}
        hasMore={list.nextCursor !== null}
        onMore={() => void list.loadMore()}
        compact={compact}
      >
        {list.items.map((s) => {
          const dup = (titleCount.get(s.title) ?? 0) > 1 || !s.title;
          const meta = (
            <p className="mt-1 text-xs text-stone-600">
              {SESSION_STATE_LABELS[s.state]} · {dup ? <>시작 {formatDateTime(s.createdAt)} · </> : null}최근 {formatDateTime(s.updatedAt)}
            </p>
          );
          const titleClass = compact ? "text-sm font-medium text-stone-900" : "font-medium text-stone-900";
          if (archived) {
            return (
              <li key={s.id} className={`flex items-start justify-between gap-2 ${compact ? "px-2 py-2.5" : "px-5 py-4"}`}>
                <div className="min-w-0">
                  <p className={titleClass}>{s.title || "(제목 없음)"}</p>
                  {meta}
                </div>
                <RestoreSession session={s} onRestored={() => setRev((r) => r + 1)} />
              </li>
            );
          }
          return (
            <li key={s.id}>
              <Link
                href={`/research/session/?id=${encodeURIComponent(s.id)}`}
                // #119 N3: 대화 목록은 대화창 안에서 열므로 링크마다 미리 불러오지 않는다
                prefetch={false}
                onClick={(e) => {
                  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                  e.preventDefault();
                  openSession(s.id);
                }}
                className={compact ? "block rounded-md px-2 py-2.5 hover:bg-stone-50" : "block px-5 py-4 hover:bg-stone-50"}
              >
                <p className={titleClass}>{s.title || "(제목 없음)"}</p>
                {meta}
              </Link>
            </li>
          );
        })}
      </ListFrame>
    </section>
  );
}
