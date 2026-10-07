"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import { listMaterials, listVersions, publishDocument, revokePublication } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import type { ResearchDocument } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";

// 공개는 (문서의 특정 버전, 명시적으로 고른 자료의 특정 버전)을 고정한다.
// 관계로 연결됐다는 이유만으로 자료를 포함하지 않는다. 개인 초안 수정은 공개본을 바꾸지 않는다.
function PublishForm({ doc, onDone, onCancel }: { doc: ResearchDocument; onDone: () => void; onCancel: () => void }) {
  const versions = usePagedList(`pub-versions:${doc.id}:${doc.latestVersion.id}`, (c) => listVersions("document", doc.id, c));
  const materials = usePagedList("pub-materials", (c) => listMaterials(c));
  const [versionId, setVersionId] = useState(doc.publication?.versionId ?? doc.latestVersion.id);
  // 자료 ID → 고정할 자료 버전 ID
  const [selected, setSelected] = useState<Record<string, string>>(() =>
    Object.fromEntries((doc.publication?.materials ?? []).map((m) => [m.id, m.versionId]))
  );
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const { run, busy } = useMutation();

  function toggle(id: string, latestVersionId: string) {
    setSelected((s) => {
      const next = { ...s };
      if (next[id]) delete next[id];
      else next[id] = latestVersionId;
      return next;
    });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const input = { versionId, materialVersionIds: Object.values(selected).sort() };
    const res = await run(`publish:${doc.id}:${doc.version}:${JSON.stringify(input)}`, (ctx) =>
      publishDocument(doc.id, input, doc.version, ctx)
    );
    if (res.ok) onDone();
    else setFailure(res.failure);
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-lg bg-stone-50 p-4">
      <label className="block text-sm">
        <span className="mb-1 block font-medium text-stone-700">공개할 문서 버전</span>
        <select
          value={versionId}
          onChange={(e) => setVersionId(e.target.value)}
          className="w-full rounded-lg border border-stone-300 px-3 py-1.5"
        >
          {!versions.items.some((v) => v.id === versionId) && <option value={versionId}>현재 선택 버전</option>}
          {versions.items.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.number} · {v.title} · {formatDateTime(v.createdAt)}
            </option>
          ))}
        </select>
      </label>
      {versions.nextCursor && !versions.loading && (
        <button type="button" onClick={() => void versions.loadMore()} className="text-xs text-indigo-600 hover:text-indigo-800">
          이전 버전 더 불러오기
        </button>
      )}
      {versions.failure && <ErrorNotice failure={versions.failure} prefix="버전 목록을 불러오지 못했습니다." />}

      <fieldset className="space-y-2 text-sm">
        <legend className="mb-1 font-medium text-stone-700">함께 공개할 참고 자료</legend>
        <p className="text-xs text-stone-500">
          직접 고른 자료만 공개됩니다. 선택한 시점의 자료 버전이 고정되며, 이후 개인 자료를 고쳐도 공개본은 바뀌지 않습니다.
        </p>
        {materials.items.map((m) => {
          const pinned = selected[m.id];
          return (
            <label key={m.id} className="flex items-start gap-2">
              <input type="checkbox" checked={!!pinned} onChange={() => toggle(m.id, m.latestVersion.id)} className="mt-1" />
              <span>
                {m.title || "(제목 없음)"}
                {pinned && pinned !== m.latestVersion.id && (
                  <span className="ml-2 text-xs text-amber-700">
                    이전 공개 버전 유지 중 ·{" "}
                    <button
                      type="button"
                      onClick={() => setSelected((s) => ({ ...s, [m.id]: m.latestVersion.id }))}
                      className="underline"
                    >
                      최신 v{m.latestVersion.number}로 바꾸기
                    </button>
                  </span>
                )}
              </span>
            </label>
          );
        })}
        {(() => {
          const unseen = Object.keys(selected).filter((id) => !materials.items.some((m) => m.id === id)).length;
          return unseen > 0 ? (
            <p className="text-xs text-stone-500">목록에 아직 보이지 않는 선택 자료 {unseen}개도 기존 버전 그대로 유지됩니다.</p>
          ) : null;
        })()}
        {materials.nextCursor && !materials.loading && (
          <button type="button" onClick={() => void materials.loadMore()} className="text-xs text-indigo-600 hover:text-indigo-800">
            자료 더 불러오기
          </button>
        )}
        {materials.failure && <ErrorNotice failure={materials.failure} prefix="자료 목록을 불러오지 못했습니다." />}
      </fieldset>

      {failure && <ErrorNotice failure={failure} prefix="공개하지 못했습니다." />}
      <div className="flex gap-2 text-sm">
        <button type="submit" disabled={busy} className="rounded-lg bg-emerald-600 px-3 py-1.5 font-medium text-white hover:bg-emerald-700 disabled:opacity-50">
          {busy ? "공개 중..." : "이 내용으로 공개"}
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg px-3 py-1.5 text-stone-600 hover:bg-stone-100">
          취소
        </button>
      </div>
    </form>
  );
}

export default function PublicationPanel({ doc, onChanged }: { doc: ResearchDocument; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const { run, busy } = useMutation();
  const pub = doc.publication;

  async function revoke() {
    if (!window.confirm("공개를 철회할까요? 개인 문서·자료·버전·관계·대화는 그대로 보존됩니다.")) return;
    const res = await run(`revoke:${doc.id}:${doc.version}`, (ctx) => revokePublication(doc.id, doc.version, ctx));
    if (res.ok) {
      setFailure(null);
      onChanged();
    } else setFailure(res.failure);
  }

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-stone-800">공개</h2>
      {pub ? (
        <div className="space-y-1 text-sm text-stone-600">
          <p>
            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">공개 중</span>{" "}
            {formatDateTime(pub.publishedAt)}에 공개 · 참고 자료 {pub.materialIds.length}개
          </p>
          {pub.versionId !== doc.latestVersion.id && (
            <p className="text-xs text-amber-700">현재 초안은 공개된 버전 이후에 수정되었습니다. 다시 공개하기 전까지 공개본은 바뀌지 않습니다.</p>
          )}
          <Link href={`/public/document/?id=${encodeURIComponent(doc.id)}`} className="text-indigo-600 hover:text-indigo-800">
            공개 페이지 보기
          </Link>
        </div>
      ) : (
        <p className="text-sm text-stone-600">비공개 문서입니다. 공개하기 전까지 본인만 볼 수 있습니다.</p>
      )}
      {!doc.archived && !editing && (
        <div className="flex gap-3 text-sm">
          <button onClick={() => setEditing(true)} className="font-medium text-emerald-700 hover:text-emerald-900">
            {pub ? "공개 내용 바꾸기" : "공개하기"}
          </button>
          {pub && (
            <button onClick={() => void revoke()} disabled={busy} className="text-red-600 hover:text-red-800 disabled:opacity-50">
              공개 철회
            </button>
          )}
        </div>
      )}
      {editing && (
        <PublishForm
          doc={doc}
          onCancel={() => setEditing(false)}
          onDone={() => {
            setEditing(false);
            onChanged();
          }}
        />
      )}
      {failure && <ErrorNotice failure={failure} prefix="공개를 철회하지 못했습니다." />}
    </section>
  );
}
