"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import { acceptNewDocumentProposal, discardDocumentProposal, getDocumentProposal, listDocumentProposals } from "@/lib/research/api";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import type { ResearchDocument } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";
import SafeMarkdown from "@/components/research/SafeMarkdown";

// #28 D1: Codex의 새 문서(research_document_save)는 바로 저장되지 않고 제안으로 남는다. 수락하면 문서와 첫 버전이 만들어진다.

export const newDocumentProposalHref = (id: string) => `/research/document/new/?proposal=${encodeURIComponent(id)}`;

export function NewDocumentProposalReview({ id, onDone }: { id: string; onDone: (doc: ResearchDocument | null) => void }) {
  const { result } = useLoad(`document-proposal:${id}`, () => getDocumentProposal(id));
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  if (!result) return <p className="text-sm text-stone-500">제안을 불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="제안을 불러오지 못했습니다." />;
  const p = result.data;
  if (p.documentId !== null && p.status === "pending") {
    // 기존 문서에 대한 제안은 그 문서 화면에서 검토한다.
    return (
      <Link href={`/research/document/?id=${encodeURIComponent(p.documentId)}#proposal-${encodeURIComponent(p.id)}`} className="text-sm font-medium text-indigo-600">
        이 제안은 기존 문서에 대한 것입니다. 문서 화면에서 검토하기 →
      </Link>
    );
  }

  async function accept() {
    setFailure(null);
    const body = { expectedProposalVersion: p.version, title: p.title, content: p.content ?? "" };
    const res = await run(`document-proposal:accept:${p.id}:${p.version}`, (ctx) => acceptNewDocumentProposal(p.id, body, ctx));
    if (res.ok) onDone(res.data.document);
    else setFailure(res.failure);
  }
  async function discard() {
    setFailure(null);
    const res = await run(`document-proposal:discard:${p.id}:${p.version}`, (ctx) => discardDocumentProposal(p.id, p.version, ctx));
    if (res.ok) onDone(null);
    else setFailure(res.failure);
  }

  const pending = p.status === "pending";
  return (
    <div className="space-y-3">
      <p className="text-sm text-stone-900">
        {pending
          ? "Codex가 대화에서 새 문서로 제안한 내용입니다. 수락하면 이 내용으로 문서와 첫 버전이 만들어지고, 버리면 아무것도 만들어지지 않습니다."
          : p.status === "accepted"
            ? "이미 수락한 제안입니다."
            : "버린 제안입니다(원문은 기록으로 보존)."}
      </p>
      <div className="rounded-lg bg-white p-4 ring-1 ring-stone-200">
        <p className="mb-2 font-medium text-stone-900">{p.title || "(제목 없음)"}</p>
        <SafeMarkdown content={p.content ?? ""} />
      </div>
      {failure && <ErrorNotice failure={failure} prefix="처리하지 못했습니다." />}
      {pending && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void accept()} disabled={busy} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            제안 수락(새 문서 만들기)
          </button>
          <button type="button" onClick={() => void discard()} disabled={busy} className="text-sm text-stone-900 hover:text-red-700">
            제안 버리기
          </button>
        </div>
      )}
      {p.acceptedDocumentId && (
        <Link href={`/research/document/?id=${encodeURIComponent(p.acceptedDocumentId)}`} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          만들어진 문서 보기 →
        </Link>
      )}
    </div>
  );
}

/** 검토할 새 문서 제안 목록. 목록은 기존 문서 제안·지난 제안도 함께 주므로 걸러 내고 남은 페이지도 이어서 찾는다. */
export function NewDocumentProposals({ reload = "" }: { reload?: string }) {
  const list = usePagedList(`new-document-proposals:${reload}`, (cursor) => listDocumentProposals(cursor));
  const items = list.items.filter((p) => p.documentId === null && p.status === "pending");
  const { loaded, loading, nextCursor, loadMore } = list;
  const searching = loaded && items.length === 0 && !!nextCursor && !list.failure;
  useEffect(() => {
    if (searching && !loading) void loadMore();
  }, [searching, loading, loadMore]);
  if (!items.length && !list.failure) return null;
  return (
    <section aria-label="Codex 새 문서 제안" className="space-y-2 rounded-lg bg-indigo-50/50 p-3 ring-1 ring-indigo-200">
      <h2 className="text-sm font-semibold text-indigo-900">Codex가 제안한 새 문서</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="제안 목록을 불러오지 못했습니다." />}
      <ul className="space-y-1 text-sm">
        {items.map((p) => (
          <li key={p.id} className="flex flex-wrap items-baseline justify-between gap-2">
            <Link href={newDocumentProposalHref(p.id)} className="text-indigo-700 hover:text-indigo-900">
              {p.title || "(제목 없음)"} — 검토
            </Link>
            <span className="text-xs text-stone-500">{formatDateTime(p.createdAt)}</span>
          </li>
        ))}
      </ul>
      {!searching && nextCursor && (
        <button type="button" onClick={() => void loadMore()} disabled={loading} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
          제안 더 보기
        </button>
      )}
    </section>
  );
}
