"use client";

import { useEffect, useRef, useState } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { commitPostDraft, discardPostDraft, getPost, getPostDraft } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { Post, PostDraft, PostText } from "@/lib/posts/types";
import { EMPTY_TEXT, textOf, usePostDraft } from "@/lib/posts/usePostDraft";
import { useMutation } from "@/lib/research/hooks";
import { formatDateTime } from "@/lib/research/format";
import { LIMITS } from "@/lib/research/types";
import { StatusLine, isReason } from "@/components/research/DocumentEditor";
import ErrorNotice from "@/components/research/ErrorNotice";
import { Field, MarkdownEditor, inputClass } from "@/components/research/ItemForms";
import Notice from "@/components/research/Notice";
import TextDiff from "@/components/research/TextDiff";
import DocumentLinks from "./DocumentLinks";
import ImagePanel from "./ImagePanel";
import PostMetaDiff from "./PostMetaDiff";
import TagInput from "./TagInput";

// #28 글 편집기: 서버 초안 자동 저장 + 명시 저장(새 버전). 초안·버전은 발행본을 바꾸지 않는다(발행은 따로).

export const SLUG_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;
export const DESCRIPTION_MAX = 1000;
const SLUG_MAX = 100;

/** 주소 입력 정리: 소문자로 바꾸고 공백·밑줄은 하이픈으로, 그 밖의 허용되지 않는 문자는 뺀다(입력 중 끝·연속 하이픈은 둔다). */
export const normalizeSlug = (raw: string) =>
  raw
    .toLowerCase()
    .replace(/[\s_]+/g, "-")
    .replace(/[^a-z0-9-]/g, "")
    .slice(0, SLUG_MAX);

/** 저장 전에 화면에서 확인할 것(서버가 최종 검증). 문제가 없으면 null. */
export function textProblem(v: PostText): string | null {
  if (!v.title.trim() || !v.content.trim()) return "제목과 본문을 모두 입력해야 저장할 수 있습니다.";
  if (!SLUG_RE.test(v.slug)) return "주소(slug)를 소문자 영문·숫자와 중간 하이픈으로 입력해야 저장할 수 있습니다(예: docker-basics).";
  return null;
}

export default function PostEditor({
  post,
  draft: resumeDraft,
  seed,
  onDraft,
  onCommitted,
  onClose,
  onDiscarded,
  onReloadPost,
}: {
  // 기존 글(새 글이면 null)
  post: Post | null;
  draft: PostDraft | null;
  // 편집기를 열며 넣을 내용(이전 버전으로 되돌리기 등). 초안으로 자동 저장된다.
  seed?: PostText;
  onDraft?: (draft: PostDraft) => void;
  onCommitted: (post: Post) => void;
  onClose?: () => void;
  onDiscarded: () => void;
  onReloadPost?: () => Promise<void>;
}) {
  const head = post ? textOf({ ...post.latestVersion, content: post.latestVersion.content ?? "" }) : EMPTY_TEXT;
  const editor = usePostDraft({
    initial: head,
    draft: resumeDraft,
    base: post ? { postId: post.id, basePostVersion: post.version, baseVersionId: post.latestVersion.id } : null,
    onDraft,
  });
  const { values, status } = editor;
  // 모든 변경은 최신 입력에 덧붙인다(렌더 시점 값을 캡처하지 않는다).
  const set = (patch: Partial<PostText>) => editor.update((v) => ({ ...v, ...patch }));
  // 이미지 Markdown을 본문 커서 위치에 넣는다(커서가 없으면 끝에). 앞뒤를 빈 줄로 띄운다.
  const body = useRef<HTMLTextAreaElement>(null);
  function insert(markdown: string) {
    editor.update((v) => {
      const el = body.current;
      const text = v.content;
      // 입력란이 초점을 잃어도 마지막 선택 위치는 남아 있다(업로드를 기다리는 동안 바뀐 본문 기준)
      const at = Math.min(el?.selectionEnd ?? text.length, text.length);
      const before = text.slice(0, at);
      const after = text.slice(at);
      const piece = `${before && !before.endsWith("\n\n") ? (before.endsWith("\n") ? "\n" : "\n\n") : ""}${markdown}${after.startsWith("\n") ? "" : "\n"}`;
      return { ...v, content: before + piece + after };
    });
  }
  // 이미지 업로드 중에는 저장·닫기·버리기를 잠근다(완료 결과가 닫힌 편집기나 저장된 버전 밖에 남지 않게).
  const [uploading, setUploading] = useState(false);
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [comparing, setComparing] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [showServerDiff, setShowServerDiff] = useState(false);

  // 글이 바뀌어 멈춘 자동 저장은 최신 글이 실제로 도착한 뒤(새 base) 다시 시작한다(#22 R16과 같은 규칙).
  const resumeOnReload = useRef(false);
  const postKey = post ? `${post.version}:${post.latestVersion.id}` : "";
  useEffect(() => {
    if (!resumeOnReload.current) return;
    resumeOnReload.current = false;
    editor.resume();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [postKey]);

  const seeded = useRef(false);
  useEffect(() => {
    if (seed && !seeded.current) {
      seeded.current = true;
      // 주소는 기존 글에서 바꿀 수 없으므로 되돌리기 내용에서도 현재 주소를 쓴다.
      editor.setValues(post ? { ...seed, slug: post.latestVersion.slug } : seed);
    }
  }, [seed, editor, post]);

  // 편집을 시작한 기준. 글 version은 발행·발행 취소에도 오르므로(계약) 내용 기준(snapshot ID)과 version을 함께 본다.
  const [startBase, setStartBase] = useState(() =>
    resumeDraft
      ? { version: resumeDraft.basePostVersion, id: resumeDraft.baseVersionId }
      : post
        ? { version: post.version, id: post.latestVersion.id }
        : null
  );
  // 내용 기준이 바뀌었는지(다른 저장·제안 수락). 발행만 바뀐 경우는 비교 없이 저장한다(commit은 expectedVersion으로 현재 version을 보낸다).
  const contentMoved = !!post && !!startBase && startBase.id !== post.latestVersion.id;
  const baseChanged = !!post && !!startBase && (contentMoved || startBase.version !== post.version);

  const [finishing, setFinishing] = useState<null | "commit" | "close" | "discard">(null);
  async function finish(kind: "commit" | "close" | "discard", action: () => Promise<boolean>) {
    if (finishing || uploading) return;
    setFinishing(kind);
    let done = false;
    try {
      done = await action();
    } finally {
      if (!done) setFinishing(null);
    }
  }

  async function commit(): Promise<boolean> {
    setFailure(null);
    setNote(null);
    const problem = textProblem(values);
    if (problem) {
      setNote(problem);
      return false;
    }
    const flushed = await editor.flush();
    if (!flushed.ok) {
      setNote("초안을 먼저 저장하지 못해 새 버전을 만들지 않았습니다. 위 안내를 확인한 뒤 다시 시도하세요.");
      return false;
    }
    const d = flushed.draft;
    if (!d) {
      setNote("바뀐 내용이 없어 새 버전을 만들지 않았습니다.");
      return false;
    }
    if (contentMoved && !comparing) {
      setComparing(true);
      setNote("초안을 시작한 뒤 글이 바뀌었습니다. 아래 비교를 확인한 뒤 ‘비교했고 이 내용으로 저장’을 누르세요.");
      return false;
    }
    const body = post
      ? { expectedDraftRevision: d.revision, expectedVersion: post.version, ...(baseChanged ? { resolvedBaseVersionId: post.latestVersion.id } : {}) }
      : { expectedDraftRevision: d.revision };
    const res = await run(`post-draft:commit:${d.id}:${JSON.stringify(body)}`, (ctx) => commitPostDraft(d.id, body, ctx));
    if (res.ok) {
      onCommitted(res.data.post);
      return true;
    }
    // 앞선 저장 요청이 실제로는 반영됐는데 응답만 잃은 경우: 초안이 저장 완료로 닫혀 있으면 그 글로 간다.
    if (isReason(res.failure, "draft_closed")) {
      const closed = await getPostDraft(d.id);
      if (closed.ok && closed.data.state === "committed" && closed.data.postId) {
        const saved = await getPost(closed.data.postId);
        if (saved.ok) {
          onCommitted(saved.data);
          return true;
        }
      }
    }
    setFailure(res.failure);
    if (isReason(res.failure, "base_changed", "post_version")) {
      await onReloadPost?.();
      setComparing(true);
    }
    return false;
  }

  async function discard(): Promise<boolean> {
    setFailure(null);
    const flushed = await editor.flush();
    const d = flushed.draft;
    if (!d) {
      onDiscarded();
      return true;
    }
    const res = await run(`post-draft:discard:${d.id}:${d.revision}`, (ctx) => discardPostDraft(d.id, d.revision, ctx));
    if (res.ok || isReason(res.failure, "draft_closed")) {
      onDiscarded();
      return true;
    }
    setFailure(res.failure);
    return false;
  }

  async function close(): Promise<boolean> {
    const flushed = await editor.flush();
    if (flushed.ok) {
      onClose?.();
      return true;
    }
    setNote("저장되지 않은 입력이 있어 닫지 않았습니다. 자동 저장 안내를 확인하세요.");
    return false;
  }

  const locked = finishing !== null;
  const stopped = status.kind === "stopped" ? status.failure : null;
  const fixedSlug = post ? post.latestVersion.slug : null;
  return (
    <div className="space-y-4">
      <p className="text-xs text-stone-500">
        입력은 초안으로 자동 저장되어 새로고침하거나 다른 기기에서 열어도 이어 쓸 수 있습니다. 버전은 ‘{post ? "새 버전으로 저장" : "글 저장"}’을 누를 때만
        만들어지고, 발행한 글은 다시 발행하기 전까지 바뀌지 않습니다.
      </p>
      <Field label="제목">
        <input value={values.title} onChange={(e) => set({ title: e.target.value })} maxLength={LIMITS.title} readOnly={locked} className={`${inputClass} read-only:bg-stone-50`} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        {fixedSlug !== null ? (
          <Field label="주소(slug)" hint="첫 저장 뒤에는 바꿀 수 없습니다(공개 주소 유지).">
            <input value={fixedSlug} readOnly className={`${inputClass} bg-stone-50 font-mono`} />
          </Field>
        ) : (
          <Field label="주소(slug)" hint={`공개 주소: /posts/${values.slug || "…"}/ · 소문자 영문·숫자·하이픈. 첫 저장 뒤에는 바꿀 수 없습니다.`}>
            <input
              value={values.slug}
              onChange={(e) => set({ slug: normalizeSlug(e.target.value) })}
              maxLength={SLUG_MAX}
              readOnly={locked}
              placeholder="예: docker-basics"
              spellCheck={false}
              autoCapitalize="none"
              className={`${inputClass} font-mono read-only:bg-stone-50`}
            />
          </Field>
        )}
        <TagInput tags={values.tags} onChange={(tags) => set({ tags })} readOnly={locked} />
      </div>
      <Field label="요약" hint={`글 목록과 글 머리에 보입니다. ${DESCRIPTION_MAX}자까지.`}>
        <textarea
          value={values.description}
          onChange={(e) => set({ description: e.target.value })}
          maxLength={DESCRIPTION_MAX}
          rows={2}
          readOnly={locked}
          className={`${inputClass} read-only:bg-stone-50`}
        />
      </Field>
      <DocumentLinks ids={values.documentIds} onChange={(documentIds) => set({ documentIds })} readOnly={locked} />
      <ImagePanel
        coverImageId={values.coverImageId}
        onInsert={insert}
        onCover={(coverImageId) => set({ coverImageId })}
        onUploading={setUploading}
        readOnly={locked}
      />
      <MarkdownEditor value={values.content} onChange={(content) => set({ content })} readOnly={locked} ownImages textareaRef={body} />
      {locked && (
        <p role="status" className="text-xs text-stone-600">
          {finishing === "commit" ? "저장하는 중에는 입력을 고칠 수 없습니다." : finishing === "discard" ? "초안을 버리는 중입니다." : "초안을 저장하고 닫는 중입니다."}
        </p>
      )}
      <StatusLine status={status} onRetry={() => void editor.flush()} />
      {uploading && (
        <p role="status" className="text-xs text-stone-600">
          이미지를 올리는 중입니다. 끝나면 본문 커서 위치에 넣으며, 그동안 저장·닫기·버리기는 잠깁니다. 입력은 계속할 수 있습니다.
        </p>
      )}

      {status.kind === "conflict" && (
        <Notice tone="warn">
          다른 탭이나 기기에서 이 글의 초안이 바뀌었습니다
          {status.server ? `(서버 초안 ${formatDateTime(status.server.updatedAt)})` : ""}. 자동 저장을 멈췄습니다. 어느 쪽으로 이어 쓸지 고르세요.
          {status.server ? (
            <span className="mt-2 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => {
                  const server = status.server!;
                  setStartBase({ version: server.basePostVersion, id: server.baseVersionId });
                  editor.takeServer(server);
                }}
                className="font-medium underline hover:no-underline"
              >
                서버 초안 불러오기(지금 입력 버림)
              </button>
              <button type="button" onClick={() => editor.keepMine(status.server!)} className="font-medium underline hover:no-underline">
                지금 입력으로 계속(서버 초안 위에 저장)
              </button>
              <button type="button" onClick={() => setShowServerDiff(!showServerDiff)} className="underline hover:no-underline">
                {showServerDiff ? "차이 닫기" : "차이 보기"}
              </button>
            </span>
          ) : (
            <span className="mt-1 block">서버 초안을 다시 읽지 못했습니다. 새로고침한 뒤 다시 확인하세요. 지금 입력은 화면에 그대로 있습니다.</span>
          )}
        </Notice>
      )}
      {status.kind === "conflict" && status.server && showServerDiff && (
        <div className="space-y-2">
          <PostMetaDiff before={textOf({ ...status.server, content: status.server.content ?? "" })} after={values} />
          <TextDiff before={status.server.content ?? ""} after={values.content} beforeTitle={status.server.title} afterTitle={values.title} />
        </div>
      )}

      {stopped && (
        <Notice tone="warn">
          자동 저장을 멈췄습니다. {postErrorMessage(stopped) ?? "초안을 저장하지 못했습니다."} 입력은 이 화면에 그대로 있습니다.{" "}
          {isReason(stopped, "base_changed", "post_version") && onReloadPost && (
            <button
              type="button"
              onClick={() => {
                resumeOnReload.current = true;
                void onReloadPost();
              }}
              className="font-medium underline hover:no-underline"
            >
              최신 글을 불러와 계속
            </button>
          )}
          {isReason(stopped, "draft_closed") && (
            <button type="button" onClick={() => editor.resume({ draft: null })} className="font-medium underline hover:no-underline">
              새 초안으로 계속
            </button>
          )}
          {!isReason(stopped, "base_changed", "post_version", "draft_closed", "archived") && (
            <button type="button" onClick={() => editor.resume()} className="font-medium underline hover:no-underline">
              다시 시도
            </button>
          )}
        </Notice>
      )}

      {post && contentMoved && (
        <Notice tone="info">
          이 초안을 시작한 뒤 글이 바뀌었습니다(현재 v{post.latestVersion.number}). 저장하기 전에 현재 버전과 비교하세요.{" "}
          <button type="button" onClick={() => setComparing(!comparing)} className="font-medium underline hover:no-underline">
            {comparing ? "비교 닫기" : "현재 버전과 비교"}
          </button>
        </Notice>
      )}
      {post && comparing && (
        <div className="space-y-2 rounded-lg p-3 ring-1 ring-indigo-200">
          <p className="text-xs text-stone-600">현재 글(v{post.latestVersion.number})에서 내 초안으로 바뀌는 내용입니다.</p>
          <PostMetaDiff before={head} after={values} />
          <TextDiff before={head.content} after={values.content} beforeTitle={head.title} afterTitle={values.title} />
        </div>
      )}

      {note && <p role="alert" className="text-sm text-amber-800">{note}</p>}
      {failure && <ErrorNotice failure={failure} prefix="저장하지 못했습니다." describe={postErrorMessage} />}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void finish("commit", commit)}
          disabled={busy || locked || uploading || status.kind === "conflict"}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {finishing === "commit" ? "저장 중..." : post ? (contentMoved && comparing ? "비교했고 이 내용으로 저장" : "새 버전으로 저장") : "글 저장"}
        </button>
        {onClose && (
          <button type="button" onClick={() => void finish("close", close)} disabled={busy || locked || uploading} className="rounded-lg px-4 py-2 text-sm text-stone-600 hover:bg-stone-100">
            편집 닫기(초안 보관)
          </button>
        )}
        {confirmDiscard ? (
          <span className="flex items-center gap-2 text-sm">
            <span className="text-stone-600">{post ? "초안을 버리고 저장된 글로 돌아갈까요?" : "이 초안을 버릴까요?"}</span>
            <button type="button" onClick={() => void finish("discard", discard)} disabled={busy || locked || uploading} className="font-medium text-red-700 underline hover:no-underline">
              초안 버리기
            </button>
            <button type="button" onClick={() => setConfirmDiscard(false)} className="text-stone-600 underline hover:no-underline">
              취소
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirmDiscard(true)} disabled={busy || locked || uploading} className="ml-auto text-sm text-stone-500 hover:text-red-700">
            초안 버리기…
          </button>
        )}
      </div>
    </div>
  );
}
