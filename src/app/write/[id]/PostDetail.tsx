"use client";

import { useState } from "react";
import type { FetchFailure } from "@/lib/api/client";
import Link from "next/link";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { archivePost, getPost, getPostDraftOf, getPostVersion, listPostVersions } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { Post, PostDraft, PostText, PostVersion } from "@/lib/posts/types";
import { textOf } from "@/lib/posts/usePostDraft";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import BlockRenderer from "@/components/blocks/BlockRenderer";
import BodyDiff from "@/components/blocks/BodyDiff";
import { VersionHistoryView, type VersionSource } from "@/components/research/VersionHistory";
import PostEditor from "@/components/posts/PostEditor";
import PostMetaDiff from "@/components/posts/PostMetaDiff";
import PostProposalPanel from "@/components/posts/PostProposals";
import PublishPanel from "@/components/posts/PublishPanel";
import TagBadge from "@/components/posts/TagBadge";

// #37: 글의 보관(archive API)은 되돌리는 화면이 없어 사용자에게는 삭제와 같으므로 '삭제'라고 부른다.
// 동작은 그대로다(발행 해제 + 내 글 목록에서 숨김). 끝나면 이 화면에서 완료와 목록 링크를 보인다(글을 다시 불러와 삭제 상태로).
function DeletePost({ post, onDeleted }: { post: Post; onDeleted: () => void }) {
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  async function remove() {
    const pub = post.publication ? " 발행 중인 글이라 발행도 함께 취소됩니다." : " 발행 중이었다면 발행도 함께 취소됩니다.";
    if (!window.confirm(`이 글을 삭제할까요? 내 글 목록에서 사라지고 더 이상 수정·발행할 수 없습니다.${pub}`)) return;
    const res = await run(`post:archive:${post.id}:${post.version}`, (ctx) => archivePost(post.id, post.version, ctx));
    if (res.ok) onDeleted();
    else setFailure(res.failure);
  }
  return (
    <>
      {/* #119 O5: 파괴적 동작은 붉은 테두리로 구분하고 '내용 수정'과 떨어뜨린다(오른쪽 끝) */}
      <button
        onClick={() => void remove()}
        disabled={busy}
        className="rounded-lg border border-red-200 px-3 py-1 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50"
      >
        삭제
      </button>
      {failure && <ErrorNotice failure={failure} prefix="삭제하지 못했습니다." describe={postErrorMessage} />}
    </>
  );
}

const LIST_HREF = "/write/";

function PublicAddress({ slug, published }: { slug: string; published: boolean }) {
  const path = `/posts/${encodeURIComponent(slug)}/`;
  return published ? (
    <p className="text-xs text-stone-600">
      공개 주소:{" "}
      <Link href={path} className="font-medium text-indigo-600 hover:text-indigo-800">
        공개 글 보기
      </Link>{" "}
      <span className="font-mono">{path}</span>
    </p>
  ) : (
    <p className="text-xs text-stone-600">
      발행하면 이 주소로 공개됩니다: <span className="font-mono">{path}</span>
    </p>
  );
}

const versionMeta = (v: PostVersion) => (
  <p className="text-xs text-stone-500">
    {v.tags.length > 0 && `태그 ${v.tags.join(", ")} · `}연결 문서 {v.documentIds.length}개 · 자료 {v.materialIds.length}개
  </p>
);

export default function PostDetail({ id }: { id: string }) {
  const { result, refreshFailure, reload, setResult } = useLoad(`post:${id}`, () => getPost(id));
  const draftLoad = useLoad(`post-draft:${id}`, () => getPostDraftOf(id));
  const [editing, setEditing] = useState<{ draft: PostDraft | null; seed?: PostText } | null>(null);
  const [opening, setOpening] = useState(false);
  const [deleted, setDeleted] = useState(false);
  // #119 A3: 공개 글 탭(글 제목만)과 구분되게 "· 내 글"
  useDocumentTitle(result?.ok ? `${result.data.latestVersion.title || "(제목 없음)"} · 내 글` : null);

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
    body: (version) => <BlockRenderer content={version.content} />,
    bodyDiff: (before, after) => <BodyDiff before={textOf(before).content} after={textOf(after).content} beforeTitle={before.title} afterTitle={after.title} />,
  };

  return (
    <article className="space-y-10" data-editing={editing ? true : undefined}>
      <header className="space-y-2">
        {/* #39: 예전 글 화면(/write/?id=)은 목록과 같은 경로라 직접 열거나 새로고침한 뒤 next/link 이동이 되지 않아 문서 전체를 다시 열었다.
            #119 B2로 글 화면이 /write/<id>/(다른 경로)가 되어 next/link로 돌아간다. */}
        <Link href={LIST_HREF} className="text-sm text-stone-500 hover:text-stone-800">
          ← 내 글
        </Link>
        {deleted && (
          <Notice tone="success">
            글을 삭제했습니다. 발행 중이었다면 공개 주소도 닫혔습니다.{" "}
            <Link href={LIST_HREF} className="font-medium underline hover:no-underline">
              내 글 목록으로
            </Link>
          </Notice>
        )}
        {p.archived && <Notice tone="info">삭제한 글입니다. 내 글 목록에는 보이지 않으며 수정·발행할 수 없습니다(내용과 버전 기록은 볼 수 있습니다).</Notice>}
        <h1 className="text-2xl font-bold text-stone-900">{v.title || "(제목 없음)"}</h1>
        {/* #109: 공개 주소는 발행 패널의 '공개 글 보기' 링크(발행 중일 때만)로 보여 주므로 여기서는 버전·수정 시각만 둔다. */}
        <p className="text-xs text-stone-500">
          v{v.number} · 수정 {formatDateTime(p.updatedAt)}
        </p>
        {/* #119 O7: 공개 주소. 발행 중이면 보기 링크, 아니면 발행할 때 나갈 주소(편집 중에는 숨긴다, #37) */}
        {!p.archived && !editing && v.slug && <PublicAddress slug={v.slug} published={!!p.publication} />}
        {!p.archived && !editing && (
          <div className="flex flex-wrap items-center justify-between gap-4 pt-1">
            <button onClick={() => void openEditor()} disabled={opening} className="text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50">
              {activeDraft ? "초안 이어서 편집" : "내용 수정"}
            </button>
            <DeletePost
              post={p}
              onDeleted={() => {
                setDeleted(true);
                void reload();
              }}
            />
          </div>
        )}
        {!editing && activeDraft && (
          <Notice tone="info">
            저장하지 않은 초안이 있습니다(자동 저장 {formatDateTime(activeDraft.updatedAt)}). 아래 내용은 마지막으로 저장한 버전입니다.{" "}
            {p.archived ? "삭제한 글이라 초안을 이어 쓸 수 없습니다." : "‘초안 이어서 편집’으로 이어 쓸 수 있습니다."}
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
          {v.tags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {v.tags.map((t) => (
                <TagBadge key={t} tag={t} />
              ))}
            </div>
          )}
          <BlockRenderer content={v.content} />
        </section>
      )}

      <PostProposalPanel post={p} draft={editing ? null : activeDraft} editing={!!editing} onAccepted={(post) => finish(post)} />
      <PublishPanel post={p} onChanged={reload} />
      <VersionHistoryView
        source={source}
        latestVersionId={v.id}
        onRestore={!p.archived && !editing ? (old) => void openEditor(textOf(old)) : undefined}
      />
    </article>
  );
}
