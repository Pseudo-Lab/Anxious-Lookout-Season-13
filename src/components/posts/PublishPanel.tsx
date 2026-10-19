"use client";

import { useState } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import { getPostPublicationPreview, listPostVersions, publishPost, unpublishPost } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { Post, PostPublicationPreview, PublicPost } from "@/lib/posts/types";
import { parseBlockDocument } from "@/lib/blocks/document";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import { isReason } from "@/components/research/DocumentEditor";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import TextDiff from "@/components/research/TextDiff";
import BodyDiff from "@/components/blocks/BodyDiff";
import PostArticle from "./PostArticle";
import PostMetaDiff from "./PostMetaDiff";

// #28 발행: 버전을 골라 미리보기 → 현재 공개본과 비교 → 발행. 발행본은 그 버전의 스냅샷이며 이후 편집과 무관하다.

function Preview({ preview }: { preview: PostPublicationPreview }) {
  const [mode, setMode] = useState<"diff" | "page">(preview.currentPublication ? "diff" : "page");
  const cur = preview.currentPublication;
  const privateDocs = preview.documents.filter((d) => !d.publicUrl);
  return (
    <div className="space-y-3">
      {preview.documents.length > 0 && (
        <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">
          <p className="font-medium">함께 공개되는 문서 제목 ({preview.documents.length})</p>
          <p className="mt-1 text-xs">
            연결 문서의 <strong>제목</strong>은 공개 글의 ‘바탕이 된 참고 문서’에 보입니다. 문서 본문은 공개되지 않으며, 공개 문서만 링크됩니다.
            {privateDocs.length > 0 && ` 공개하지 않은 문서 ${privateDocs.length}개도 제목은 보입니다.`}
          </p>
          <ul className="mt-2 space-y-0.5 text-xs">
            {preview.documents.map((d) => (
              <li key={d.id}>
                · {d.title || "(제목 없음)"} — {d.publicUrl ? "공개 문서로 링크" : "제목만 표시(비공개 문서)"}
              </li>
            ))}
          </ul>
        </div>
      )}
      {preview.materials.length > 0 && (
        <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">
          <p className="font-medium">함께 공개되는 자료 ({preview.materials.length})</p>
          <p className="mt-1 text-xs">
            바탕이 된 자료는 <strong>제목과 원본 주소</strong>만 공개 글의 ‘바탕이 된 자료’에 보입니다. 자료 본문·메모는 공개되지 않습니다.
          </p>
          <ul className="mt-2 space-y-0.5 text-xs">
            {preview.materials.map((m) => (
              <li key={m.id}>
                · {m.title || "(제목 없음)"} — {m.sourceUrl || "원본 주소 없음"}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div role="group" aria-label="미리보기 방식" className="flex gap-1 text-xs">
        {cur && (
          <button type="button" aria-pressed={mode === "diff"} onClick={() => setMode("diff")} className={`rounded px-2 py-1 ${mode === "diff" ? "bg-indigo-100 font-medium text-indigo-800" : "text-stone-600"}`}>
            현재 공개본과 비교
          </button>
        )}
        <button type="button" aria-pressed={mode === "page"} onClick={() => setMode("page")} className={`rounded px-2 py-1 ${mode === "page" ? "bg-indigo-100 font-medium text-indigo-800" : "text-stone-600"}`}>
          공개 화면 미리보기
        </button>
      </div>
      {mode === "diff" && cur ? (
        <div className="space-y-2">
          <p className="text-xs text-stone-600">현재 공개본({formatDateTime(cur.publishedAt)} 발행)에서 이번 발행으로 바뀌는 내용입니다.</p>
          <PostMetaDiff
            before={{
              ...cur,
              coverImageId: cur.coverImageUrl,
              documentIds: cur.documents.map((d) => d.id),
              materialIds: cur.materials.map((m) => m.id),
              materialTitles: Object.fromEntries(cur.materials.map((m) => [m.id, m.title])),
            }}
            after={{
              ...preview,
              coverImageId: preview.images.find((i) => i.id === preview.coverImageId)?.url ?? null,
              materialIds: preview.materials.map((m) => m.id),
              materialTitles: Object.fromEntries(preview.materials.map((m) => [m.id, m.title])),
            }}
          />
          <PublishedBodyDiff current={cur} preview={preview} />
        </div>
      ) : (
        <div className="max-h-[70vh] overflow-auto rounded-lg bg-white p-4 ring-1 ring-stone-200">
          <PostArticle
            post={{
              title: preview.title,
              description: preview.description,
              content: preview.content,
              tags: preview.tags,
              coverImageUrl: preview.images.find((i) => i.id === preview.coverImageId)?.url ?? null,
              documents: preview.documents,
              materials: preview.materials,
              author: { name: preview.author.login },
              publishedAt: null,
            }}
          />
        </div>
      )}
    </div>
  );
}

// 현재 공개본 → 이번 발행 본문 비교. 둘 다 블록 문서로 읽히면 서식 변화까지, 아니면 서버 projection 글자만 비교한다.
function PublishedBodyDiff({ current, preview }: { current: PublicPost; preview: PostPublicationPreview }) {
  const before = parseBlockDocument(current.content);
  const after = parseBlockDocument(preview.content);
  if (before.ok && after.ok) return <BodyDiff before={before.doc} after={after.doc} beforeTitle={current.title} afterTitle={preview.title} />;
  return <TextDiff before={current.textProjection ?? ""} after={preview.textProjection} beforeTitle={current.title} afterTitle={preview.title} />;
}

export default function PublishPanel({ post, onChanged }: { post: Post; onChanged: () => Promise<void> | void }) {
  const versions = usePagedList(`post-versions:${post.id}:${post.latestVersion.id}`, (cursor) => listPostVersions(post.id, cursor));
  const [versionId, setVersionId] = useState(post.latestVersion.id);
  const [preview, setPreview] = useState<PostPublicationPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirmUnpublish, setConfirmUnpublish] = useState(false);
  const { run, busy } = useMutation();
  const pub = post.publication;
  const pubVersion = pub && versions.items.find((v) => v.id === pub.versionId);

  // keepFailure: 발행 충돌 안내를 남긴 채 새 상태로 다시 미리본다.
  async function loadPreview(id = versionId, keepFailure = false) {
    setLoading(true);
    if (!keepFailure) setFailure(null);
    setNote(null);
    const r = await getPostPublicationPreview(post.id, id);
    setLoading(false);
    if (r.ok) setPreview(r.data);
    else {
      setPreview(null);
      setFailure(r.failure);
    }
  }

  async function publish() {
    if (!preview) return;
    setFailure(null);
    const res = await run(`post:publish:${preview.postId}:${preview.versionId}:${preview.previewToken}`, (ctx) => publishPost(preview, ctx));
    if (res.ok) {
      setPreview(null);
      setNote(`발행했습니다. 공개 주소: /posts/${res.data.slug}/`);
      await onChanged();
      return;
    }
    setFailure(res.failure);
    // 미리보기 뒤 상태가 바뀌었으면 새 미리보기를 보여 다시 확인하게 한다(자동으로 발행하지 않는다).
    if (isReason(res.failure, "preview_changed", "post_version")) {
      await onChanged();
      await loadPreview(preview.versionId, true);
    }
  }

  async function unpublish() {
    setFailure(null);
    const res = await run(`post:unpublish:${post.id}:${post.version}`, (ctx) => unpublishPost(post.id, post.version, ctx));
    setConfirmUnpublish(false);
    if (res.ok) {
      setPreview(null);
      setNote("발행을 취소했습니다. 공개 주소는 더 이상 열리지 않으며, 같은 주소로 다시 발행할 수 있습니다.");
      await onChanged();
    } else setFailure(res.failure);
  }

  return (
    <section aria-label="발행" className="space-y-3 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-stone-200/60">
      <h2 className="font-semibold text-stone-800">발행</h2>
      {pub ? (
        <p className="text-sm text-stone-700">
          <span className="font-medium text-emerald-700">발행 중</span> · {pubVersion ? `v${pubVersion.number}` : "발행한 버전"} · {formatDateTime(pub.publishedAt)} ·{" "}
          <Link href={`/posts/${encodeURIComponent(pub.slug)}/`} className="text-indigo-600 hover:text-indigo-800">
            공개 글 보기
          </Link>
          {pub.versionId !== post.latestVersion.id && <span className="ml-1 text-xs text-amber-800">(최신 버전 v{post.latestVersion.number}은 아직 발행하지 않았습니다)</span>}
        </p>
      ) : (
        <p className="text-sm text-stone-600">발행하지 않은 글입니다. 내 글 목록에서만 보입니다.</p>
      )}
      {post.archived ? (
        <p className="text-xs text-stone-500">보관한 글은 발행할 수 없습니다.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1">
            <span className="text-stone-600">발행할 버전</span>
            <select
              value={versionId}
              onChange={(e) => {
                setVersionId(e.target.value);
                setPreview(null);
              }}
              className="rounded border border-stone-300 px-2 py-1"
            >
              {versions.items.length === 0 && <option value={post.latestVersion.id}>v{post.latestVersion.number} (최신)</option>}
              {versions.items.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.number}
                  {v.id === post.latestVersion.id ? " (최신)" : ""}
                  {pub?.versionId === v.id ? " · 발행 중" : ""} · {formatDateTime(v.createdAt)}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={() => void loadPreview()} disabled={loading || busy} className="font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50">
            {loading ? "미리보기 불러오는 중..." : preview ? "미리보기 새로 고침" : "발행 미리보기"}
          </button>
          {versions.nextCursor && (
            <button type="button" onClick={() => void versions.loadMore()} className="text-xs text-stone-500 underline">
              이전 버전 더 불러오기
            </button>
          )}
        </div>
      )}
      {preview && preview.versionId === versionId && (
        <div className="space-y-3">
          <Preview preview={preview} />
          {!preview.publishable && (
            <Notice tone="warn">이 버전은 지금 발행할 수 없습니다(연결 문서·이미지 확인 필요). 연결을 고친 새 버전을 저장한 뒤 다시 미리보세요.</Notice>
          )}
          <button
            type="button"
            onClick={() => void publish()}
            disabled={busy || !preview.publishable}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {pub ? "이 버전으로 다시 발행" : "이 버전 발행"}
          </button>
        </div>
      )}
      {pub &&
        (confirmUnpublish ? (
          <span className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-stone-600">발행을 취소할까요? 공개 주소가 바로 닫힙니다(글과 버전은 그대로).</span>
            <button type="button" onClick={() => void unpublish()} disabled={busy} className="font-medium text-red-700 underline hover:no-underline">
              발행 취소
            </button>
            <button type="button" onClick={() => setConfirmUnpublish(false)} className="text-stone-600 underline hover:no-underline">
              그대로 두기
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirmUnpublish(true)} className="text-sm text-stone-500 hover:text-red-700">
            발행 취소…
          </button>
        ))}
      {note && <Notice tone="success">{note}</Notice>}
      {failure && <ErrorNotice failure={failure} prefix="발행 작업을 하지 못했습니다." describe={postErrorMessage} />}
    </section>
  );
}
