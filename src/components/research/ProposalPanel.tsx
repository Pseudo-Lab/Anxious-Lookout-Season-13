"use client";

import { useEffect, useRef, useState } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { acceptProposal, discardProposal, getProposal, listProposals } from "@/lib/research/api";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import type { Draft, Proposal, ResearchDocument } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";
import TextDiff from "@/components/research/TextDiff";

// #22 Codex 제안(D2: 수락 전 제안). 제안은 수락하기 전에는 최신 문서가 아니며, 수락하면 새 버전 하나가 된다.
// 비교 기준은 현재 문서 또는 내 초안. 버린 제안·수락한 제안의 원문은 서버에 보존된다.

const STATUS_LABELS: Record<Proposal["status"], string> = { pending: "검토 대기", accepted: "수락함", discarded: "버림" };

function Review({
  doc,
  summary,
  draft,
  editing,
  onDone,
}: {
  doc: ResearchDocument;
  summary: Proposal;
  draft: Draft | null;
  editing: boolean;
  onDone: (accepted: ResearchDocument | null) => void;
}) {
  const { result } = useLoad(`proposal:${doc.id}:${summary.id}:${summary.version}`, () => getProposal(doc.id, summary.id));
  const { run, busy } = useMutation();
  const [against, setAgainst] = useState<"document" | "draft">(draft ? "draft" : "document");
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [confirm, setConfirm] = useState(false);

  if (!result) return <p className="text-sm text-stone-500">제안을 불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="제안을 불러오지 못했습니다." />;
  const p = result.data;
  const pending = p.status === "pending";
  const useDraft = against === "draft" && !!draft;
  const before = useDraft ? { title: draft.title, content: draft.content ?? "" } : { title: doc.title, content: doc.content };
  const stale = p.baseVersionId !== doc.latestVersion.id;

  async function accept() {
    setFailure(null);
    const body = {
      expectedProposalVersion: p.version,
      expectedVersion: doc.version,
      resolvedBaseVersionId: doc.latestVersion.id,
      title: p.title,
      content: p.content ?? "",
      ...(draft ? { draftId: draft.id, expectedDraftRevision: draft.revision } : {}),
    };
    const res = await run(`proposal:accept:${p.id}:${JSON.stringify({ ...body, content: undefined })}`, (ctx) =>
      acceptProposal(doc.id, p.id, body, ctx)
    );
    if (res.ok) onDone(res.data.document);
    else setFailure(res.failure);
  }

  async function discard() {
    setFailure(null);
    const res = await run(`proposal:discard:${p.id}:${p.version}`, (ctx) => discardProposal(doc.id, p.id, p.version, ctx));
    if (res.ok) onDone(null);
    else setFailure(res.failure);
  }

  return (
    <div className="space-y-3">
      {pending && (
        <div role="group" aria-label="비교 기준" className="flex flex-wrap gap-3 text-sm">
          <label className="flex items-center gap-1">
            <input type="radio" checked={!useDraft} onChange={() => setAgainst("document")} />
            현재 문서(v{doc.latestVersion.number})와 비교
          </label>
          {draft && (
            <label className="flex items-center gap-1">
              <input type="radio" checked={useDraft} onChange={() => setAgainst("draft")} />내 초안({formatDateTime(draft.updatedAt)})과 비교
            </label>
          )}
        </div>
      )}
      {pending && stale && (
        <p className="text-xs text-amber-800">
          이 제안은 이전 문서를 기준으로 만들어졌고 그 뒤 문서가 바뀌었습니다. 수락하면 현재 문서를 아래 제안 내용으로 바꾼 새 버전이 만들어집니다.
        </p>
      )}
      <TextDiff
        key={`${p.id}:${against}`}
        before={before.content}
        after={p.content ?? ""}
        beforeTitle={before.title}
        afterTitle={p.title}
      />
      {failure && <ErrorNotice failure={failure} prefix="처리하지 못했습니다." />}
      {pending &&
        (editing ? (
          <p className="text-xs text-stone-600">편집 중에는 수락할 수 없습니다. 편집을 닫은 뒤(초안은 보관됩니다) 다시 검토하세요.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            {confirm ? (
              <>
                <span className="text-sm text-stone-700">
                  {draft
                    ? "수락하면 이 제안이 새 버전이 되고, 작성 중인 초안은 닫힙니다(초안 내용은 기록으로 보존)."
                    : "수락하면 이 제안이 새 버전이 됩니다."}
                </span>
                <button
                  type="button"
                  onClick={() => void accept()}
                  disabled={busy}
                  className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  수락 확인
                </button>
                <button type="button" onClick={() => setConfirm(false)} className="text-sm text-stone-600 underline hover:no-underline">
                  취소
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setConfirm(true)}
                disabled={busy}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                제안 수락
              </button>
            )}
            <button type="button" onClick={() => void discard()} disabled={busy} className="text-sm text-stone-600 hover:text-red-700">
              제안 버리기
            </button>
          </div>
        ))}
    </div>
  );
}

export default function ProposalPanel({
  doc,
  draft,
  editing,
  onAccepted,
}: {
  doc: ResearchDocument;
  // 이 문서의 active 초안(있으면 비교 기준·수락 시 함께 닫힘)
  draft: Draft | null;
  editing: boolean;
  onAccepted: (doc: ResearchDocument) => void;
}) {
  const [tick, setTick] = useState(0);
  const list = usePagedList(`proposals:${doc.id}:${doc.version}:${tick}`, (cursor) => listProposals(doc.id, cursor));
  const [open, setOpen] = useState<string | null>(null);
  const [history, setHistory] = useState(false);
  const pending = list.items.filter((p) => p.status === "pending");
  const closed = list.items.filter((p) => p.status !== "pending");
  const section = useRef<HTMLElement>(null);

  // 대화의 '제안 검토' 링크(#proposal-<id>)로 오면 목록을 새로 불러오고 그 제안을 펼쳐 이 영역으로 이동한다.
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => {
    const pick = () => {
      const m = /^#proposal-([0-9a-f-]+)$/i.exec(window.location.hash);
      if (!m) return false;
      setTarget(m[1]);
      setOpen(m[1]);
      return true;
    };
    pick();
    const onHash = () => {
      if (pick()) setTick((t) => t + 1);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const found = !!target && list.items.some((p) => p.id === target);
  useEffect(() => {
    if (found) section.current?.scrollIntoView({ block: "start" });
  }, [found, tick]);

  if (!list.items.length && !list.failure) return null;

  const row = (p: Proposal) => (
    <li key={p.id} className="rounded-lg ring-1 ring-stone-200">
      <button
        onClick={() => setOpen(open === p.id ? null : p.id)}
        aria-expanded={open === p.id}
        className="flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm hover:bg-stone-50"
      >
        <span className="min-w-0 break-words">
          <span className="mr-2 text-xs font-medium text-indigo-700">Codex 제안</span>
          <span className="text-stone-800">{p.title}</span>
        </span>
        <span className="shrink-0 text-xs text-stone-500">
          {STATUS_LABELS[p.status]} · {formatDateTime(p.createdAt)}
        </span>
      </button>
      {open === p.id && (
        <div className="px-4 pb-4">
          <Review
            doc={doc}
            summary={p}
            draft={draft}
            editing={editing}
            onDone={(accepted) => {
              setOpen(null);
              setTick(tick + 1);
              if (accepted) onAccepted(accepted);
            }}
          />
        </div>
      )}
    </li>
  );

  return (
    <section id="proposals" ref={section} className="scroll-mt-4">
      <h2 className="mb-1 text-lg font-semibold text-stone-800">Codex 제안</h2>
      <p className="mb-3 text-xs text-stone-500">
        대화에서 Codex가 이 문서를 고치면 제안으로 남습니다. 수락하기 전에는 문서가 바뀌지 않으며, 비교한 뒤 수락하면 새 버전이 됩니다.
      </p>
      {list.failure && <ErrorNotice failure={list.failure} prefix="제안 목록을 불러오지 못했습니다." />}
      {pending.length > 0 ? (
        <ul className="space-y-2">{pending.map(row)}</ul>
      ) : (
        !list.failure && <p className="text-sm text-stone-500">검토할 제안이 없습니다.</p>
      )}
      {closed.length > 0 && (
        <div className="mt-3">
          <button onClick={() => setHistory(!history)} aria-expanded={history} className="text-sm text-stone-600 hover:text-stone-900">
            지난 제안 {closed.length}건 {history ? "접기" : "보기"}
          </button>
          {history && <ul className="mt-2 space-y-2">{closed.map(row)}</ul>}
        </div>
      )}
      {list.nextCursor && (
        <button onClick={() => void list.loadMore()} className="mt-2 text-sm font-medium text-indigo-600 hover:text-indigo-800">
          제안 더 보기
        </button>
      )}
    </section>
  );
}
