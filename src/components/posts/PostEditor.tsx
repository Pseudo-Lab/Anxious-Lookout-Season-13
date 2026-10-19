"use client";

import { useEffect, useRef, useState } from "react";
import type { Editor } from "@tiptap/react";
import type { FetchFailure } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { commitPostDraft, discardPostDraft, getPost, getPostDraft, resolvePostMaterials } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { Post, PostDraft, PostMaterialSource, PostText } from "@/lib/posts/types";
import { EMPTY_TEXT, textOf, usePostDraft } from "@/lib/posts/usePostDraft";
import { useMutation } from "@/lib/research/hooks";
import { formatDateTime } from "@/lib/research/format";
import { LIMITS } from "@/lib/research/types";
import { StatusLine, isReason } from "@/components/research/DocumentEditor";
import ErrorNotice from "@/components/research/ErrorNotice";
import { hasVisibleContent } from "@/lib/blocks/projection";
import BodyDiff from "@/components/blocks/BodyDiff";
import PostBodyEditor, { blockProblemText, type BodyProblem } from "@/components/blocks/PostBodyEditor";
import { bufferTarget, clearBodyBuffer, loadBodyBuffer, saveBodyBuffer } from "@/lib/posts/bodyBuffer";
import { Field, inputClass } from "@/components/research/ItemForms";
import Notice from "@/components/research/Notice";
import DocumentLinks from "./DocumentLinks";
import ImagePanel from "./ImagePanel";
import MaterialLinks, { MATERIAL_MAX } from "./MaterialLinks";
import PostMetaDiff from "./PostMetaDiff";
import TagInput from "./TagInput";

// #28 글 편집기: 서버 초안 자동 저장 + 명시 저장(새 버전). 초안·버전은 발행본을 바꾸지 않는다(발행은 따로).
// #31: 주소(slug)는 입력하지 않는다(서버가 첫 저장 때 정함). 요약은 접어 둔 선택 입력, 바탕이 된 자료를 함께 고른다.
// #32: 본문은 블록 편집기(post-blocks v1)다. 이미지는 imageId로 넣고, 비교는 글자(projection)와 서식·배치를 따로 보인다.

export const DESCRIPTION_MAX = 1000;

/** 저장 전에 화면에서 확인할 것(서버가 최종 검증). 문제가 없으면 null. */
export function textProblem(v: PostText): string | null {
  if (!v.title.trim() || !hasVisibleContent(v.content)) return "제목과 본문을 모두 입력해야 저장할 수 있습니다.";
  return null;
}

const sourceMap = (list: PostMaterialSource[]) => Object.fromEntries(list.map((s) => [s.materialId, s.documentIds]));

// 요약은 선택 입력이라 비어 있으면 접어 둔다(#31 W2). 비우면 글 목록 카드가 본문 앞부분을 대신 보인다.
function DescriptionField({ value, onChange, readOnly }: { value: string; onChange: (v: string) => void; readOnly: boolean }) {
  const [open, setOpen] = useState(false);
  if (!open && !value) {
    return (
      <div className="text-sm">
        <button type="button" onClick={() => setOpen(true)} disabled={readOnly} aria-expanded={false} className="font-medium text-indigo-600 hover:text-indigo-800 disabled:text-stone-400">
          요약 추가(선택)
        </button>
        <span className="ml-2 text-xs text-stone-500">비워 두면 글 목록에 본문 앞부분이 보입니다.</span>
      </div>
    );
  }
  return (
    <Field label="요약(선택)" hint={`글 목록과 글 머리에 보입니다. 비워 두면 목록에는 글 앞부분을 대신 보입니다. ${DESCRIPTION_MAX}자까지.`}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={DESCRIPTION_MAX}
        rows={2}
        readOnly={readOnly}
        autoFocus={open && !value}
        className={`${inputClass} read-only:bg-stone-50`}
      />
    </Field>
  );
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
  const head = post ? textOf(post.latestVersion) : EMPTY_TEXT;
  // 화면의 본문이 계약을 넘어(상한 등) 마지막 유효 본문만 저장된 상태(리뷰 M1), 또는 서버가 새 문서 계약을 요구해 저장을
  // 거절한 상태(#36 리뷰 M1)에서는 저장하지 못한 입력을 이 탭의 메모리에 보관해 두고(bodyBuffer), 화면 이동·뒤로가기 뒤
  // 같은 초안을 다시 열면 되살린다. 새로고침·탭 닫기는 떠날 때 확인으로 막는다(브라우저 저장소에 남기지 않는다).
  const { user, csrfToken } = useAuth();
  const accountId = user?.accountId ?? null;
  const [restored] = useState(() => {
    if (!accountId) return null;
    const target = bufferTarget(resumeDraft?.id, post?.id);
    const b = loadBodyBuffer(accountId, target);
    if (!b) return null;
    // 그 뒤 서버 초안이 바뀌었으면(다른 탭·기기) 오래된 버퍼로 덮지 않는다
    if (b.revision !== (resumeDraft?.revision ?? null)) {
      clearBodyBuffer(accountId, target);
      return null;
    }
    return { target, body: b.editor, input: b.input ?? null };
  });
  const editor = usePostDraft({
    initial: head,
    draft: resumeDraft,
    base: post ? { postId: post.id, basePostVersion: post.version, baseVersionId: post.latestVersion.id } : null,
    onDraft,
    start: restored?.input,
  });
  const { values, status } = editor;
  // 모든 변경은 최신 입력에 덧붙인다(렌더 시점 값을 캡처하지 않는다).
  const set = (patch: Partial<PostText>) => editor.update((v) => ({ ...v, ...patch }));
  // 이미지 패널의 이미지를 본문 커서 위치에 블록으로 넣는다(편집기가 아직 없으면 넣지 않는다)
  const bodyEditor = useRef<Editor | null>(null);
  function insertImage(image: { id: string; alt: string }) {
    bodyEditor.current?.chain().focus().insertPostImage({ imageId: image.id, alt: image.alt }).run();
  }
  // 이미지 업로드 중에는 저장·닫기·버리기를 잠근다(완료 결과가 닫힌 편집기나 저장된 버전 밖에 남지 않게).
  // 이미지 패널과 본문 편집기(붙여넣기·끌어놓기)의 업로드를 함께 센다.
  const [panelUploading, setPanelUploading] = useState(false);
  const [bodyUploading, setBodyUploading] = useState(false);
  const uploading = panelUploading || bodyUploading;
  // 본문 문제가 있는 동안 새 버전 저장·닫기를 막는다(리뷰 M1).
  const buffer = bufferTarget(editor.draft?.id ?? resumeDraft?.id, post?.id);
  const [bodyProblemState, setBodyProblemState] = useState<BodyProblem | null>(null);
  const bodyProblem = bodyProblemState?.error ?? null;
  function onBodyProblem(p: BodyProblem | null) {
    setBodyProblemState(p);
    if (!p && accountId) clearBodyBuffer(accountId, buffer);
  }
  // #36: 서버가 이 화면보다 새 문서 계약을 요구해 저장을 거절했다. 저장·닫기를 막고 새로고침을 안내한다(입력은 화면과 버퍼에 남는다).
  const upgradeRequired = status.kind === "stopped" && status.failure.kind === "http" && status.failure.code === "post_contract_upgrade_required";
  // 문제가 있는 동안 버퍼를 지금 초안(마지막 유효 본문의 자동 저장이 늦게 끝나 revision·ID가 바뀌어도 그 기준)과 지금 입력에 맞춰 둔다
  const draftRevision = editor.draft?.revision ?? null;
  // 이 화면이 버퍼를 둔 대상. 되살려 열었으면 그 대상이다(다시 저장되면 지운다).
  const savedTarget = useRef<string | null>(restored?.target ?? null);
  useEffect(() => {
    if (!accountId || (!bodyProblemState && !upgradeRequired)) return;
    if (savedTarget.current && savedTarget.current !== buffer) clearBodyBuffer(accountId, savedTarget.current);
    savedTarget.current = buffer;
    saveBodyBuffer(accountId, buffer, { revision: draftRevision, editor: bodyProblemState?.editor ?? null, ...(upgradeRequired ? { input: values } : {}) });
  }, [accountId, buffer, bodyProblemState, draftRevision, upgradeRequired, values]);
  // 보관했던 입력이 서버에 저장되면(문제가 풀리고 저장 완료) 버퍼를 지운다
  useEffect(() => {
    if (!accountId || bodyProblemState || upgradeRequired || !savedTarget.current) return;
    if (status.kind !== "saved" && status.kind !== "clean") return;
    clearBodyBuffer(accountId, savedTarget.current);
    savedTarget.current = null;
  }, [accountId, bodyProblemState, upgradeRequired, status]);
  useEffect(() => {
    if (!bodyProblem && !upgradeRequired) return;
    // 떠나면 화면의 입력(저장되지 않은 본문)을 잃으므로 브라우저 확인을 띄운다
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [bodyProblem, upgradeRequired]);

  // 바탕이 된 자료(#31 W3). 문서를 연결하면 그 문서가 가리키는 자료를 서버에 물어 선택에 더한다(선택은 언제나 명시 materialIds).
  // 자료를 찾는 동안에도 저장·닫기·버리기를 잠근다(결과가 저장된 버전 밖에 남지 않게).
  const [sources, setSources] = useState<Record<string, string[]>>(() =>
    sourceMap(resumeDraft?.materialSources ?? post?.latestVersion.materialSources ?? [])
  );
  const [resolving, setResolving] = useState(0);
  const [materialNote, setMaterialNote] = useState<React.ReactNode>(null);
  const alive = useRef(true);
  // 자료 찾기 요청 번호와, 자료마다 '뺄 때 이미 보낸 마지막 요청 번호'. 그 요청(과 그 전 요청)의 늦은 응답은 뺀 자료를 다시 넣지 않는다.
  // 뺀 뒤에 새로 연결한 문서의 요청은 번호가 더 커서 그 자료를 다시 담을 수 있다(리뷰 F1).
  const resolveSeq = useRef(0);
  const removedAt = useRef(new Map<string, number>());
  function removeMaterial(id: string) {
    removedAt.current.set(id, resolveSeq.current);
    editor.update((v) => ({ ...v, materialIds: v.materialIds.filter((x) => x !== id) }));
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  async function linkDocuments(next: string[]) {
    const added = next.filter((id) => !values.documentIds.includes(id));
    set({ documentIds: next });
    if (added.length === 0) return;
    setMaterialNote(null);
    if (!csrfToken) {
      setMaterialNote(<p className="text-xs text-amber-800">로그인 상태를 확인하지 못해 연결 문서의 자료를 담지 못했습니다. 필요하면 자료를 직접 추가하세요.</p>);
      return;
    }
    const seq = ++resolveSeq.current;
    setResolving((n) => n + 1);
    const res = await resolvePostMaterials(added, csrfToken);
    if (!alive.current) return;
    setResolving((n) => n - 1);
    if (!res.ok) {
      setMaterialNote(<ErrorNotice failure={res.failure} prefix="연결 문서의 자료를 담지 못했습니다. 필요하면 자료를 직접 추가하세요." describe={postErrorMessage} />);
      return;
    }
    setSources((prev) => {
      const out = { ...prev };
      for (const m of res.data.items) out[m.id] = [...new Set([...(out[m.id] ?? []), ...m.documentIds])];
      return out;
    });
    let full = 0;
    editor.update((v) => {
      const ids = [...v.materialIds];
      // 기다리는 사이 연결을 해제한 문서의 자료와, 이 요청을 보낸 뒤 사람이 뺀 자료는 담지 않는다
      for (const m of res.data.items) {
        if (ids.includes(m.id) || !m.documentIds.some((d) => v.documentIds.includes(d))) continue;
        if ((removedAt.current.get(m.id) ?? 0) >= seq) continue;
        if (ids.length >= MATERIAL_MAX) full++;
        else ids.push(m.id);
      }
      return { ...v, materialIds: ids };
    });
    const notes: string[] = [];
    if (full > 0) notes.push(`자료가 ${MATERIAL_MAX}개를 넘어 ${full}개는 담지 못했습니다. 다른 자료를 뺀 뒤 직접 추가하세요.`);
    if (res.data.excludedArchivedIds.length > 0) notes.push(`보관한 자료 ${res.data.excludedArchivedIds.length}개는 담지 않았습니다.`);
    if (notes.length) setMaterialNote(<p className="text-xs text-amber-800">{notes.join(" ")}</p>);
  }
  const pending = uploading || resolving > 0;
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
      editor.setValues(seed);
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
    if (finishing || pending) return;
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
    if (bodyProblem) {
      setNote(`본문을 고친 뒤 저장하세요. ${blockProblemText(bodyProblem)}`);
      return false;
    }
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
    // 초안을 버리면 이 탭에 보관한 저장 못 한 본문도 함께 버린다(명시적 폐기)
    if (!d) {
      if (accountId) clearBodyBuffer(accountId, buffer);
      onDiscarded();
      return true;
    }
    const res = await run(`post-draft:discard:${d.id}:${d.revision}`, (ctx) => discardPostDraft(d.id, d.revision, ctx));
    if (res.ok || isReason(res.failure, "draft_closed")) {
      if (accountId) clearBodyBuffer(accountId, buffer);
      onDiscarded();
      return true;
    }
    setFailure(res.failure);
    return false;
  }

  async function close(): Promise<boolean> {
    if (bodyProblem) {
      setNote("저장할 수 없는 본문이 화면에 있어 닫지 않았습니다. 본문을 고치거나 초안을 버리세요.");
      return false;
    }
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
  // 연결이 남아 있는 문서만 출처로 보인다(문서를 해제해도 담긴 자료는 직접 빼기 전까지 남는다)
  const linkedSources = Object.fromEntries(
    values.materialIds.map((id) => [id, (sources[id] ?? []).filter((d) => values.documentIds.includes(d))])
  );
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
          <Field label="공개 주소" hint="처음 저장할 때 정해졌고 바꿀 수 없습니다.">
            <input value={`/posts/${fixedSlug}/`} readOnly className={`${inputClass} bg-stone-50 font-mono`} />
          </Field>
        ) : (
          <div>
            <span className="mb-1 block text-sm font-medium text-stone-700">공개 주소</span>
            <p className="text-xs text-stone-500">처음 저장할 때 자동으로 정해지며 그 뒤에는 바뀌지 않습니다. 따로 입력하지 않습니다.</p>
          </div>
        )}
        <TagInput tags={values.tags} onChange={(tags) => set({ tags })} readOnly={locked} />
      </div>
      <DescriptionField value={values.description} onChange={(description) => set({ description })} readOnly={locked} />
      <DocumentLinks ids={values.documentIds} onChange={(documentIds) => void linkDocuments(documentIds)} readOnly={locked} />
      <MaterialLinks
        ids={values.materialIds}
        sources={linkedSources}
        onAdd={(id) => editor.update((v) => (v.materialIds.includes(id) || v.materialIds.length >= MATERIAL_MAX ? v : { ...v, materialIds: [...v.materialIds, id] }))}
        onRemove={removeMaterial}
        notice={
          <>
            {resolving > 0 && (
              <p role="status" className="text-xs text-stone-600">
                연결한 문서의 자료를 찾는 중입니다. 그동안 저장·닫기·버리기는 잠깁니다.
              </p>
            )}
            {materialNote}
          </>
        }
        readOnly={locked}
      />
      <ImagePanel
        coverImageId={values.coverImageId}
        onInsert={insertImage}
        onCover={(coverImageId) => set({ coverImageId })}
        onUploading={setPanelUploading}
        readOnly={locked}
      />
      <PostBodyEditor
        value={values.content}
        onChange={(content) => set({ content })}
        onEditor={(e) => {
          bodyEditor.current = e;
        }}
        onUploading={setBodyUploading}
        onProblem={onBodyProblem}
        restore={restored?.body ?? null}
        readOnly={locked}
      />
      {locked && (
        <p role="status" className="text-xs text-stone-600">
          {finishing === "commit" ? "저장하는 중에는 입력을 고칠 수 없습니다." : finishing === "discard" ? "초안을 버리는 중입니다." : "초안을 저장하고 닫는 중입니다."}
        </p>
      )}
      <StatusLine status={status} onRetry={() => void editor.flush()} />
      {restored?.input && status.kind !== "saved" && status.kind !== "clean" && (
        <Notice tone="info">저장하지 못하고 떠났던 입력(제목·본문·글 정보)을 되살렸습니다. 이 탭에만 있던 내용이며, 저장되면 이 안내는 사라집니다.</Notice>
      )}
      {uploading && (
        <p role="status" className="text-xs text-stone-600">
          이미지를 올리는 중입니다. 끝나면 본문에 넣으며, 그동안 저장·닫기·버리기는 잠깁니다. 입력은 계속할 수 있습니다.
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
          <PostMetaDiff before={textOf(status.server)} after={values} />
          <BodyDiff before={textOf(status.server).content} after={values.content} beforeTitle={status.server.title} afterTitle={values.title} />
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
          {upgradeRequired ? (
            <button type="button" onClick={() => window.location.reload()} className="font-medium underline hover:no-underline">
              새로고침
            </button>
          ) : (
            !isReason(stopped, "base_changed", "post_version", "draft_closed", "archived") && (
              <button type="button" onClick={() => editor.resume()} className="font-medium underline hover:no-underline">
                다시 시도
              </button>
            )
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
          <BodyDiff before={head.content} after={values.content} beforeTitle={head.title} afterTitle={values.title} />
        </div>
      )}

      {note && <p role="alert" className="text-sm text-amber-800">{note}</p>}
      {failure && <ErrorNotice failure={failure} prefix="저장하지 못했습니다." describe={postErrorMessage} />}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void finish("commit", commit)}
          disabled={busy || locked || pending || !!bodyProblem || upgradeRequired || status.kind === "conflict"}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {finishing === "commit" ? "저장 중..." : post ? (contentMoved && comparing ? "비교했고 이 내용으로 저장" : "새 버전으로 저장") : "글 저장"}
        </button>
        {onClose && (
          <button type="button" onClick={() => void finish("close", close)} disabled={busy || locked || pending || !!bodyProblem || upgradeRequired} className="rounded-lg px-4 py-2 text-sm text-stone-600 hover:bg-stone-100">
            편집 닫기(초안 보관)
          </button>
        )}
        {confirmDiscard ? (
          <span className="flex items-center gap-2 text-sm">
            <span className="text-stone-600">{post ? "초안을 버리고 저장된 글로 돌아갈까요?" : "이 초안을 버릴까요?"}</span>
            <button type="button" onClick={() => void finish("discard", discard)} disabled={busy || locked || pending} className="font-medium text-red-700 underline hover:no-underline">
              초안 버리기
            </button>
            <button type="button" onClick={() => setConfirmDiscard(false)} className="text-stone-600 underline hover:no-underline">
              취소
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirmDiscard(true)} disabled={busy || locked || pending} className="ml-auto text-sm text-stone-500 hover:text-red-700">
            초안 버리기…
          </button>
        )}
      </div>
    </div>
  );
}
