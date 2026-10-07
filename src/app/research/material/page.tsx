"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { getMaterial, updateMaterial, type MaterialInput } from "@/lib/research/api";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { CONTENT_KIND_LABELS } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import AuthGate from "@/components/research/AuthGate";
import ArchiveButton from "@/components/research/ArchiveButton";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import { MaterialForm } from "@/components/research/ItemForms";
import RelationPanel from "@/components/research/RelationPanel";
import SafeMarkdown, { safeHref } from "@/components/research/SafeMarkdown";
import VersionHistory from "@/components/research/VersionHistory";

function MaterialDetail({ id }: { id: string }) {
  const { result, refreshFailure, reload, setResult } = useLoad(`material:${id}`, () => getMaterial(id));
  const { run, busy } = useMutation();
  const [editing, setEditing] = useState(false);
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="자료를 불러오지 못했습니다." />;
  const m = result.data;
  const source = safeHref(m.sourceUrl);

  async function save(input: MaterialInput) {
    const res = await run(`material:update:${m.id}:${m.version}:${JSON.stringify(input)}`, (ctx) =>
      updateMaterial(m.id, input, m.version, ctx)
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
        {m.archived && <Notice tone="info">보관한 자료입니다. 내용과 버전 기록을 볼 수 있지만 수정·연결할 수 없습니다.</Notice>}
        <h1 className="text-2xl font-bold text-stone-900">{m.title || "(제목 없음)"}</h1>
        <p className="break-all text-sm text-stone-500">
          출처{" "}
          {source ? (
            <a href={source} target="_blank" rel="noopener noreferrer nofollow ugc" className="text-indigo-600 hover:text-indigo-800">
              {m.sourceUrl}
            </a>
          ) : (
            m.sourceUrl
          )}
        </p>
        <p className="text-xs text-stone-500">
          {CONTENT_KIND_LABELS[m.contentKind]} · 수집 {formatDateTime(m.collectedAt)} · v{m.latestVersion.number} · 수정{" "}
          {formatDateTime(m.updatedAt)}
        </p>
        {!m.archived && !editing && (
          <div className="flex gap-4 pt-1">
            <button onClick={() => setEditing(true)} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
              내용 수정
            </button>
            <ArchiveButton type="material" id={m.id} version={m.version} onArchived={() => void reload()} />
          </div>
        )}
      </header>

      {editing ? (
        <section className="space-y-3">
          <p className="text-xs text-stone-500">저장하면 새 버전이 만들어지고 이전 내용은 버전 기록에 남습니다.</p>
          <MaterialForm
            initial={m}
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
          <SafeMarkdown content={m.content} />
        </>
      )}

      <RelationPanel self={{ type: "material", id: m.id, title: m.title }} editable={!m.archived} />
      <VersionHistory type="material" itemId={m.id} latestVersionId={m.latestVersion.id} />
    </article>
  );
}

function MaterialPageInner() {
  const id = useSearchParams().get("id");
  if (!id) return <Notice tone="error">자료 ID가 없습니다.</Notice>;
  return <MaterialDetail key={id} id={id} />;
}

export default function MaterialPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <Link href="/research/?tab=materials" className="mb-4 inline-block text-sm text-stone-500 hover:text-stone-800">
        ← 자료 목록
      </Link>
      <AuthGate>
        <Suspense>
          <MaterialPageInner />
        </Suspense>
      </AuthGate>
    </div>
  );
}
