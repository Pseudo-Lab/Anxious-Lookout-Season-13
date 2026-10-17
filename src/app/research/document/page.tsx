"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { getDocument, getDocumentDraft, type DraftInput } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import type { Draft, ResearchDocument } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import AuthGate from "@/components/research/AuthGate";
import ArchiveButton from "@/components/research/ArchiveButton";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import DocumentEditor from "@/components/research/DocumentEditor";
import ProposalPanel from "@/components/research/ProposalPanel";
import PublicationPanel from "@/components/research/PublicationPanel";
import RelationPanel from "@/components/research/RelationPanel";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import VersionHistory from "@/components/research/VersionHistory";

function DocumentDetail({ id }: { id: string }) {
  const { result, refreshFailure, reload, setResult } = useLoad(`document:${id}`, () => getDocument(id));
  // #22 이 문서의 active 서버 초안(없으면 null). 편집을 열 때마다 다시 확인한다.
  const draftLoad = useLoad(`document-draft:${id}`, () => getDocumentDraft(id));
  const [editing, setEditing] = useState<{ draft: Draft | null; seed?: DraftInput } | null>(null);
  const [opening, setOpening] = useState(false);

  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="문서를 불러오지 못했습니다." />;
  const d = result.data;
  const activeDraft = draftLoad.result?.ok ? draftLoad.result.data.draft : null;

  // 편집 열기: 다른 기기에서 만든 초안이 있을 수 있어 먼저 다시 확인하고, 있으면 그 초안을 이어 쓴다.
  async function openEditor(seed?: DraftInput) {
    setOpening(true);
    const r = await getDocumentDraft(d.id);
    setOpening(false);
    setEditing({ draft: r.ok ? r.data.draft : null, seed });
  }
  function finish(doc?: ResearchDocument) {
    if (doc) setResult({ ok: true, status: 200, data: doc });
    setEditing(null);
    void draftLoad.reload();
  }

  return (
    // 편집 중에는 나란히 보기를 위해 페이지 폭을 넓힌다(바깥 컨테이너가 data-editing을 보고 넓어진다).
    <article className="space-y-10" data-editing={editing ? true : undefined}>
      <header className="space-y-2">
        {d.archived && <Notice tone="info">보관한 문서입니다. 내용과 버전 기록을 볼 수 있지만 수정·연결·새로 공개할 수 없습니다. 공개 중이라면 아래에서 철회할 수 있습니다.</Notice>}
        <h1 className="text-2xl font-bold text-stone-900">{d.title || "(제목 없음)"}</h1>
        <p className="text-xs text-stone-500">
          v{d.latestVersion.number} · 수정 {formatDateTime(d.updatedAt)} · 작성 {formatDateTime(d.createdAt)}
        </p>
        {!d.archived && !editing && (
          <div className="flex gap-4 pt-1">
            <button onClick={() => void openEditor()} disabled={opening} className="text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50">
              {activeDraft ? "초안 이어서 편집" : "내용 수정"}
            </button>
            <ArchiveButton type="document" id={d.id} version={d.version} onArchived={() => void reload()} />
          </div>
        )}
        {!editing && activeDraft && (
          <Notice tone="info">
            저장하지 않은 초안이 있습니다(자동 저장 {formatDateTime(activeDraft.updatedAt)}). 아래 본문은 마지막으로 저장한 버전입니다.{" "}
            {d.archived ? "보관한 문서라 초안을 이어 쓸 수 없습니다." : "‘초안 이어서 편집’으로 이어 쓸 수 있습니다."}
          </Notice>
        )}
      </header>

      {editing ? (
        <section className="space-y-3">
          <DocumentEditor
            doc={d}
            draft={editing.draft}
            seed={editing.seed}
            onCommitted={finish}
            onClose={() => finish()}
            onDiscarded={() => finish()}
            onReloadDocument={reload}
          />
          {refreshFailure && (
            <ErrorNotice failure={refreshFailure} prefix="최신 내용을 불러오지 못했습니다. 작성 중인 입력은 그대로 있습니다." />
          )}
        </section>
      ) : (
        <>
          {refreshFailure && <ErrorNotice failure={refreshFailure} prefix="최신 상태를 불러오지 못했습니다. 마지막으로 받은 내용을 표시합니다." />}
          <SafeMarkdown content={d.content} />
        </>
      )}

      <ProposalPanel
        doc={d}
        draft={editing ? null : activeDraft}
        editing={!!editing}
        onAccepted={(doc) => finish(doc)}
      />
      <PublicationPanel doc={d} onChanged={() => void reload()} />
      <RelationPanel self={{ type: "document", id: d.id, title: d.title }} editable={!d.archived} />
      <VersionHistory
        type="document"
        itemId={d.id}
        latestVersionId={d.latestVersion.id}
        onRestore={!d.archived && !editing ? (v) => void openEditor({ title: v.title, content: v.content }) : undefined}
      />
    </article>
  );
}

function DocumentPageInner() {
  const id = useSearchParams().get("id");
  if (!id) return <Notice tone="error">문서 ID가 없습니다.</Notice>;
  return <DocumentDetail key={id} id={id} />;
}

export default function DocumentPage() {
  return (
    <div className="mx-auto max-w-3xl has-[[data-editing]]:max-w-6xl">
      <Link href="/research/?tab=documents" className="mb-4 inline-block text-sm text-stone-500 hover:text-stone-800">
        ← 문서 목록
      </Link>
      <AuthGate>
        <Suspense>
          <DocumentPageInner />
        </Suspense>
      </AuthGate>
    </div>
  );
}
