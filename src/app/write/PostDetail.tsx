"use client";

import { useState } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import { archivePost, getPost, getPostDraftOf, getPostVersion, listPostVersions } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { Post, PostDraft, PostText, PostVersion } from "@/lib/posts/types";
import { textOf } from "@/lib/posts/usePostDraft";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import { VersionHistoryView, type VersionSource } from "@/components/research/VersionHistory";
import PostEditor from "@/components/posts/PostEditor";
import PostMetaDiff from "@/components/posts/PostMetaDiff";
import PostProposalPanel from "@/components/posts/PostProposals";
import PublishPanel from "@/components/posts/PublishPanel";
import TagBadge from "@/components/posts/TagBadge";

function ArchivePost({ post, onArchived }: { post: Post; onArchived: () => void }) {
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  async function archive() {
    const extra = post.publication ? " 발행 중인 공개 글도 함께 내려갑니다." : "";
    if (!window.confirm(`이 글을 보관할까요? 목록에서 숨겨지고 더 이상 수정·발행할 수 없지만 버전 기록은 보존됩니다.${extra}`)) return;
    const res = await run(`post:archive:${post.id}:${post.version}`, (ctx) => archivePost(post.id, post.version, ctx));
    if (res.ok) onArchived();
    else setFailure(res.failure);
  }
  return (
    <>
      <button onClick={() => void archive()} disabled={busy} className="text-sm text-stone-500 hover:text-red-700 disabled:opacity-50">
        보관
      </button>
      {failure && <ErrorNotice failure={failure} prefix="보관하지 못했습니다." describe={postErrorMessage} />}
    </>
  );
}

const versionMeta = (v: PostVersion) => (
  <p className="text-xs text-stone-500">
    주소 /posts/{v.slug}/{v.tags.length > 0 && ` · 태그 ${v.tags.join(", ")}`} · 연결 문서 {v.documentIds.length}개 · 자료 {v.materialIds.length}개
    {v.description && <span className="mt-1 block text-stone-600">{v.description}</span>}
  </p>
);

export default function PostDetail({ id }: { id: string }) {
  const { result, refreshFailure, reload, setResult } = useLoad(`post:${id}`, () => getPost(id));
  const draftLoad = useLoad(`post-draft:${id}`, () => getPostDraftOf(id));
  const [editing, setEditing] = useState<{ draft: PostDraft | null; seed?: PostText } | null>(null);
  const [opening, setOpening] = useState(false);

  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="글을 불러오지 못했습니다." describe={postErrorMessage} />;
  const p = result.data;
  const v = p.latestVersion;
  const activeDraft = draftLoad.result?.ok ? draftLoad.result.data.draft : null;

  async function openEditor(seed?: PostText) {
    setOpening(true);
    const r = await getPostDraftOf(p.id);
    setOpening(false);
    setEditing({ draft: r.ok ? r.data.draft : null, seed });
  }
  function finish(post?: Post) {
    if (post) setResult({ ok: true, status: 200, data: post });
    setEditing(null);
    void draftLoad.reload();
  }

  const source: VersionSource<PostVersion> = {
    key: `post:${p.id}`,
    list: (cursor) => listPostVersions(p.id, cursor),
    get: (versionId) => getPostVersion(p.id, versionId),
    meta: versionMeta,
    metaDiff: (before, after) => <PostMetaDiff before={before} after={after} />,
    ownImages: true,
  };

  return (
    <article className="space-y-10" data-editing={editing ? true : undefined}>
      <header className="space-y-2">
        <Link href="/write/" className="text-sm text-stone-500 hover:text-stone-800">
          ← 내 글
        </Link>
        {p.archived && <Notice tone="info">보관한 글입니다. 내용과 버전 기록을 볼 수 있지만 수정·발행할 수 없습니다.</Notice>}
        <h1 className="text-2xl font-bold text-stone-900">{v.title || "(제목 없음)"}</h1>
        <p className="text-xs text-stone-500">
          v{v.number} · 주소 /posts/{v.slug}/ · 수정 {formatDateTime(p.updatedAt)} · 작성 {formatDateTime(p.createdAt)}
        </p>
        {!p.archived && !editing && (
          <div className="flex gap-4 pt-1">
            <button onClick={() => void openEditor()} disabled={opening} className="text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50">
              {activeDraft ? "초안 이어서 편집" : "내용 수정"}
            </button>
            <ArchivePost post={p} onArchived={() => void reload()} />
          </div>
        )}
        {!editing && activeDraft && (
          <Notice tone="info">
            저장하지 않은 초안이 있습니다(자동 저장 {formatDateTime(activeDraft.updatedAt)}). 아래 내용은 마지막으로 저장한 버전입니다.{" "}
            {p.archived ? "보관한 글이라 초안을 이어 쓸 수 없습니다." : "‘초안 이어서 편집’으로 이어 쓸 수 있습니다."}
          </Notice>
        )}
      </header>

      {editing ? (
        <section className="space-y-3">
          <PostEditor
            post={p}
            draft={editing.draft}
            seed={editing.seed}
            onCommitted={finish}
            onClose={() => finish()}
            onDiscarded={() => finish()}
            onReloadPost={reload}
          />
          {refreshFailure && <ErrorNotice failure={refreshFailure} prefix="최신 내용을 불러오지 못했습니다. 작성 중인 입력은 그대로 있습니다." />}
        </section>
      ) : (
        <section className="space-y-3">
          {refreshFailure && <ErrorNotice failure={refreshFailure} prefix="최신 상태를 불러오지 못했습니다. 마지막으로 받은 내용을 표시합니다." />}
          {v.description && <p className="text-stone-600">{v.description}</p>}
          {v.tags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {v.tags.map((t) => (
                <TagBadge key={t} tag={t} />
              ))}
            </div>
          )}
          <SafeMarkdown content={v.content ?? ""} ownImages />
        </section>
      )}

      <PostProposalPanel post={p} draft={editing ? null : activeDraft} editing={!!editing} onAccepted={(post) => finish(post)} />
      <PublishPanel post={p} onChanged={reload} />
      <VersionHistoryView
        source={source}
        latestVersionId={v.id}
        onRestore={!p.archived && !editing ? (old) => void openEditor(textOf({ ...old, content: old.content ?? "" })) : undefined}
      />
    </article>
  );
}
