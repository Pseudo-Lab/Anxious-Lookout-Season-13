"use client";

import { useState } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import {
  deleteRelation,
  getPublicationPreview,
  listVersions,
  publishDocument,
  revokePublication,
} from "@/lib/research/api";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { CONTENT_KIND_LABELS, type PreviewMaterial, type ResearchDocument } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import SafeMarkdown, { safeHref } from "@/components/research/SafeMarkdown";

// 공개 = 고른 문서 버전 + 그 글에 직접 연결된 모든 참고자료(서버가 도출해 현재 버전으로 고정).
// 자료끼리의 연결은 따라가지 않으며, 개인 초안·관계·자료 수정은 이미 공개한 내용을 바꾸지 않는다.

function PreviewMaterialRow({ m, onRelationRemoved }: { m: PreviewMaterial; onRelationRemoved: () => void }) {
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const source = safeHref(m.sourceUrl);

  // 보관된 자료는 공개를 막는다. 관계만 해제하며 자료·버전은 그대로 남는다.
  async function unlink() {
    if (!window.confirm("이 보관된 자료와 글의 연결을 해제할까요? 자료와 버전 기록은 그대로 남습니다.")) return;
    for (const r of m.relations) {
      const res = await run(`relation:delete:${r.id}:${r.version}`, (ctx) => deleteRelation(r.id, r.version, ctx));
      if (!res.ok) {
        setFailure(res.failure);
        return;
      }
    }
    onRelationRemoved();
  }

  return (
    <li className={`space-y-1 rounded-lg p-3 ring-1 ${m.archived ? "bg-amber-50 ring-amber-300" : "bg-white ring-stone-200"}`}>
      <p className="text-sm font-medium text-stone-900">
        {m.title || "(제목 없음)"} <span className="font-mono text-xs text-stone-500">v{m.number}</span>
        {m.archived && <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">보관됨</span>}
      </p>
      <p className="break-all text-xs text-stone-500">
        {CONTENT_KIND_LABELS[m.contentKind]} · 수집 {formatDateTime(m.collectedAt)} ·{" "}
        {source ? (
          <a href={source} target="_blank" rel="noopener noreferrer nofollow ugc" className="text-indigo-600">
            {m.sourceUrl}
          </a>
        ) : (
          m.sourceUrl
        )}
      </p>
      <details>
        <summary className="cursor-pointer text-xs text-stone-500">공개될 저장 내용 보기</summary>
        <div className="mt-2">
          <SafeMarkdown content={m.content} />
        </div>
      </details>
      {m.archived && (
        <p className="text-xs text-amber-800">
          보관된 자료는 공개할 수 없습니다.{" "}
          <button type="button" onClick={() => void unlink()} disabled={busy} className="underline disabled:opacity-50">
            이 글과의 연결 해제
          </button>
        </p>
      )}
      {failure && <ErrorNotice failure={failure} prefix="연결을 해제하지 못했습니다." />}
    </li>
  );
}

function PublishForm({ doc, onDone, onCancel }: { doc: ResearchDocument; onDone: () => void; onCancel: () => void }) {
  const versions = usePagedList(`pub-versions:${doc.id}:${doc.latestVersion.id}`, (c) => listVersions("document", doc.id, c));
  const [versionId, setVersionId] = useState(doc.latestVersion.id);
  const [rev, setRev] = useState(0);
  const preview = useLoad(`preview:${doc.id}:${versionId}:${rev}`, () => getPublicationPreview(doc.id, versionId));
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [refreshed, setRefreshed] = useState(false);
  const { run, busy } = useMutation();

  const p = preview.result?.ok ? preview.result.data : null;

  async function publish() {
    if (!p) return;
    const res = await run(`publish:${doc.id}:${p.expectedVersion}:${p.versionId}:${p.previewToken}`, (ctx) =>
      publishDocument(doc.id, p, ctx)
    );
    if (res.ok) {
      onDone();
      return;
    }
    setFailure(res.failure);
    if (res.failure.kind === "http" && res.failure.status === 409) {
      // 미리보기 이후 연결·자료·문서가 바뀌었다. 공개를 다시 보내지 않고 최신 미리보기만 불러온다.
      setRefreshed(true);
      setRev((r) => r + 1);
    }
  }

  const stale = failure?.kind === "http" && failure.status === 409;

  return (
    <div className="space-y-4 rounded-lg bg-stone-50 p-4">
      <label className="block text-sm">
        <span className="mb-1 block font-medium text-stone-700">공개할 문서 버전</span>
        <select
          value={versionId}
          onChange={(e) => {
            setVersionId(e.target.value);
            setFailure(null);
            setRefreshed(false);
          }}
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

      <section className="space-y-2 text-sm">
        <h3 className="font-medium text-stone-700">함께 공개될 참고 자료</h3>
        <p className="text-xs text-stone-500">
          이 글에 직접 연결된 모든 자료가 지금의 저장 내용(전체·발췌·요약)과 출처로 함께 공개됩니다. 자료끼리의 연결은 따라가지
          않습니다. 공개 후 자료나 연결을 바꿔도 공개된 내용은 다시 공개하기 전까지 그대로입니다.
        </p>
        {refreshed && (
          <Notice tone="info">
            미리보기 이후 연결이나 자료가 바뀌어 최신 미리보기를 다시 불러왔습니다. 내용을 확인한 뒤 다시 공개하세요.
          </Notice>
        )}
        {!preview.result && <p className="text-xs text-stone-500">미리보기를 불러오는 중...</p>}
        {preview.result && !preview.result.ok && (
          <ErrorNotice failure={preview.result.failure} prefix="공개 미리보기를 불러오지 못했습니다." />
        )}
        {p && p.materials.length === 0 && <p className="text-xs text-stone-500">직접 연결된 참고 자료가 없습니다. 글만 공개됩니다.</p>}
        {p && p.materials.length > 0 && (
          <ul className="space-y-2">
            {p.materials.map((m) => (
              <PreviewMaterialRow key={m.id} m={m} onRelationRemoved={() => setRev((r) => r + 1)} />
            ))}
          </ul>
        )}
        {p && !p.publishable && (
          <Notice tone="warn">보관된 자료와의 연결이 남아 있어 공개할 수 없습니다. 연결을 해제한 뒤 다시 확인하세요.</Notice>
        )}
      </section>

      {failure && !stale && <ErrorNotice failure={failure} prefix="공개하지 못했습니다." />}
      <div className="flex gap-2 text-sm">
        <button
          type="button"
          onClick={() => void publish()}
          disabled={busy || !p || !p.publishable}
          className="rounded-lg bg-emerald-600 px-3 py-1.5 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? "공개 중..." : p ? `글과 참고 자료 ${p.materials.length}개 공개` : "공개"}
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg px-3 py-1.5 text-stone-600 hover:bg-stone-100">
          취소
        </button>
      </div>
    </div>
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
            <p className="text-xs text-amber-700">
              현재 초안은 공개된 버전 이후에 수정되었습니다. 다시 공개하기 전까지 공개본은 바뀌지 않습니다.
            </p>
          )}
          <p className="text-xs text-stone-500">독자는 가장 최근 공개본만 볼 수 있습니다. 다시 공개하면 이전 공개본은 더 이상 열람되지 않습니다.</p>
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
            {pub ? "새 내용으로 다시 공개" : "공개하기"}
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
