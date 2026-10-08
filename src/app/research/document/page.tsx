"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { getDocument, updateDocument, type DocumentInput } from "@/lib/research/api";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { formatDateTime } from "@/lib/research/format";
import AuthGate from "@/components/research/AuthGate";
import ArchiveButton from "@/components/research/ArchiveButton";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import { DocumentForm } from "@/components/research/ItemForms";
import PublicationPanel from "@/components/research/PublicationPanel";
import RelationPanel from "@/components/research/RelationPanel";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import VersionHistory from "@/components/research/VersionHistory";

function DocumentDetail({ id }: { id: string }) {
  const { result, refreshFailure, reload, setResult } = useLoad(`document:${id}`, () => getDocument(id));
  const { run, busy } = useMutation();
  const [editing, setEditing] = useState(false);
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="문서를 불러오지 못했습니다." />;
  const d = result.data;

  async function save(input: DocumentInput) {
    const res = await run(`document:update:${d.id}:${d.version}:${JSON.stringify(input)}`, (ctx) =>
      updateDocument(d.id, input, d.version, ctx)
    );
    if (res.ok) {
      setResult(res);
      setEditing(false);
      setFailure(null);
    } else setFailure(res.failure);
  }

  const conflict = failure?.kind === "http" && failure.code === "conflict";

  return (
    <article className="space-y-10">
      <header className="space-y-2">
        {d.archived && <Notice tone="info">보관한 문서입니다. 내용과 버전 기록을 볼 수 있지만 수정·연결·새로 공개할 수 없습니다. 공개 중이라면 아래에서 철회할 수 있습니다.</Notice>}
        <h1 className="text-2xl font-bold text-stone-900">{d.title || "(제목 없음)"}</h1>
        <p className="text-xs text-stone-500">
          v{d.latestVersion.number} · 수정 {formatDateTime(d.updatedAt)} · 작성 {formatDateTime(d.createdAt)}
        </p>
        {!d.archived && !editing && (
          <div className="flex gap-4 pt-1">
            <button onClick={() => setEditing(true)} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
              내용 수정
            </button>
            <ArchiveButton type="document" id={d.id} version={d.version} onArchived={() => void reload()} />
          </div>
        )}
      </header>

      {editing ? (
        <section className="space-y-3">
          <p className="text-xs text-stone-500">
            저장하면 새 버전이 만들어지고 이전 내용은 버전 기록에 남습니다. 공개 중인 내용은 다시 공개하기 전까지 바뀌지 않습니다.
          </p>
          <DocumentForm
            initial={d}
            submitLabel="새 버전으로 저장"
            busy={busy}
            failure={failure}
            onSubmit={(i) => void save(i)}
            onCancel={() => {
              setEditing(false);
              setFailure(null);
            }}
          />
          {conflict && (
            <button onClick={() => void reload()} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
              최신 내용 다시 불러오기 (작성 중인 입력은 유지됩니다)
            </button>
          )}
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

      <PublicationPanel doc={d} onChanged={() => void reload()} />
      <RelationPanel self={{ type: "document", id: d.id, title: d.title }} editable={!d.archived} />
      <VersionHistory type="document" itemId={d.id} latestVersionId={d.latestVersion.id} />
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
    <div className="mx-auto max-w-3xl">
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
