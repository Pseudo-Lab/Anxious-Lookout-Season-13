"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { getDraft, listDrafts } from "@/lib/research/api";
import { usePagedList } from "@/lib/research/usePagedList";
import { useAddressedDraft, type DraftLookup } from "@/lib/research/useAddressedDraft";
import { formatDateTime } from "@/lib/research/format";
import type { Draft } from "@/lib/research/types";
import AuthGate from "@/components/research/AuthGate";
import DocumentEditor from "@/components/research/DocumentEditor";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import { NewDocumentProposalReview, NewDocumentProposals } from "@/components/research/NewDocumentProposals";

// #22 새 문서: 입력은 서버 초안(documentId 없음)으로 자동 저장되고 주소(?draft=)로 복구된다. ‘문서 저장’만 문서와 첫 버전을 만든다.
// 초안은 계정별 서버 자원이라 다른 계정에는 보이지 않는다(브라우저 저장소에 본문을 두지 않는다).

const NEW_PATH = "/research/document/new/";

// 이어 쓸 수 있는 다른 새 문서 초안. 목록 API는 기존 문서 초안도 함께 주므로 걸러 내고, 다음 페이지도 이어서 찾는다.
// reload: 편집기를 새로 시작할 때(버리기 등)마다 바뀌어 목록을 다시 불러온다.
function OtherDrafts({ current, reload }: { current: string | null; reload: string }) {
  const list = usePagedList(`new-document-drafts:${reload}`, (cursor) => listDrafts(cursor));
  const drafts = list.items.filter((d) => d.documentId === null && d.id !== current);
  // 지금까지 받은 페이지에 보일 새 문서 초안이 없으면 남은 페이지를 이어서 찾는다(앞 페이지가 기존 문서 초안뿐인 경우).
  const { loaded, loading, nextCursor, loadMore } = list;
  const searching = loaded && drafts.length === 0 && !!nextCursor && !list.failure;
  useEffect(() => {
    if (searching && !loading) void loadMore();
  }, [searching, loading, loadMore]);
  if (!drafts.length && !searching && !list.failure) return null;
  return (
    <section className="rounded-lg p-3 ring-1 ring-stone-200">
      <h2 className="mb-2 text-sm font-semibold text-stone-900">작성 중인 새 문서 초안</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="초안 목록을 불러오지 못했습니다." />}
      <ul className="space-y-1 text-sm">
        {drafts.map((d) => (
          <li key={d.id}>
            <Link href={`${NEW_PATH}?draft=${encodeURIComponent(d.id)}`} className="text-indigo-600 hover:text-indigo-800">
              {d.title || "(제목 없음)"}
            </Link>
            <span className="ml-2 text-xs text-stone-500">자동 저장 {formatDateTime(d.updatedAt)}</span>
          </li>
        ))}
      </ul>
      {searching && <p className="text-xs text-stone-500">초안을 찾는 중...</p>}
      {!searching && nextCursor && (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={loading}
          className="mt-2 text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
        >
          초안 더 보기
        </button>
      )}
    </section>
  );
}

// 주소의 초안 확인: 새 문서 초안만 이어 쓰고, 이미 저장된 초안은 그 문서로 간다.
async function lookup(id: string): Promise<DraftLookup<Draft>> {
  const r = await getDraft(id);
  if (r.ok && r.data.state === "active" && r.data.documentId === null) return { kind: "active", draft: r.data };
  if (r.ok && r.data.state === "committed" && r.data.documentId)
    return { kind: "committed", href: `/research/document/?id=${encodeURIComponent(r.data.documentId)}` };
  return { kind: "missing" };
}

// #28 D1 새 문서 제안 검토(?proposal=). 수락하면 그 문서로, 버리면 문서 목록으로 간다.
function ReviewProposal({ id }: { id: string }) {
  const router = useRouter();
  return (
    <NewDocumentProposalReview
      id={id}
      onDone={(doc) => (doc ? router.push(`/research/document/?id=${encodeURIComponent(doc.id)}`) : router.replace("/research/?tab=documents"))}
    />
  );
}

function NewDocumentPageInner() {
  const search = useSearchParams();
  const proposal = search.get("proposal");
  if (proposal) return <ReviewProposal key={proposal} id={proposal} />;
  return <NewDocument param={search.get("draft")} />;
}

function NewDocument({ param }: { param: string | null }) {
  const router = useRouter();
  const { view, onDraft, onDiscarded } = useAddressedDraft({ param, newPath: NEW_PATH, lookup });
  if (!view) return <p className="text-sm text-stone-500">초안을 불러오는 중...</p>;
  const key = view.key;
  return (
    <div className="space-y-4">
      {view.note && <Notice tone="info">{view.note}</Notice>}
      <DocumentEditor
        key={key}
        doc={null}
        draft={view.draft}
        onDraft={(d) => onDraft(key, d)}
        onCommitted={(doc) => router.push(`/research/document/?id=${encodeURIComponent(doc.id)}`)}
        onDiscarded={() => onDiscarded("초안을 버렸습니다.")}
      />
      <NewDocumentProposals reload={key} />
      <OtherDrafts current={view.bound} reload={key} />
    </div>
  );
}

export default function NewDocumentPage() {
  return (
    // 편집기 나란히 보기를 위해 넓게 쓴다.
    <div className="mx-auto max-w-6xl">
      <Link href="/research/?tab=documents" className="text-sm text-stone-500 hover:text-stone-800">
        ← 문서 목록
      </Link>
      <h1 className="mb-6 mt-2 text-2xl font-bold text-stone-900">새 문서</h1>
      <AuthGate>
        <Suspense>
          <NewDocumentPageInner />
        </Suspense>
      </AuthGate>
    </div>
  );
}
