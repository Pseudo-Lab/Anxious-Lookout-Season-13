"use client";

import { useEffect, useRef, useState } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { commitDraft, discardDraft, researchErrorMessage, type DraftInput } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { formatDateTime } from "@/lib/research/format";
import { LIMITS, type Draft, type ResearchDocument } from "@/lib/research/types";
import { useDraftEditor, type SaveStatus } from "@/lib/research/useDraftEditor";
import ErrorNotice from "@/components/research/ErrorNotice";
import { Field, MarkdownEditor, inputClass } from "@/components/research/ItemForms";
import Notice from "@/components/research/Notice";
import TextDiff from "@/components/research/TextDiff";

// #22 문서 편집기: 서버 초안 자동 저장 + 명시 저장(새 버전). 초안은 버전이 아니고 공개본도 바꾸지 않는다.

function StatusLine({ status, onRetry }: { status: SaveStatus; onRetry: () => void }) {
  let text: string;
  switch (status.kind) {
    case "clean":
      text = "바뀐 내용이 없습니다.";
      break;
    case "dirty":
      text = "바뀐 내용이 있습니다. 곧 초안으로 자동 저장합니다.";
      break;
    case "saving":
      text = "초안 자동 저장 중...";
      break;
    case "saved":
      text = `초안 자동 저장됨 · ${formatDateTime(status.at)}`;
      break;
    case "retrying":
      text = "초안을 자동 저장하지 못해 잠시 후 다시 시도합니다. 입력은 이 화면에 그대로 있습니다.";
      break;
    default:
      return null;
  }
  return (
    <p role="status" aria-live="polite" className={`text-xs ${status.kind === "retrying" ? "text-amber-800" : "text-stone-500"}`}>
      {text}
      {status.kind === "retrying" && (
        <>
          {" "}
          <button type="button" onClick={onRetry} className="font-medium underline hover:no-underline">
            지금 다시 시도
          </button>
        </>
      )}
    </p>
  );
}

const isReason = (f: FetchFailure | null, ...reasons: string[]) =>
  !!f && f.kind === "http" && f.code === "conflict" && reasons.includes(f.reason ?? "");

export default function DocumentEditor({
  doc,
  draft: resumeDraft,
  seed,
  onDraft,
  onCommitted,
  onClose,
  onDiscarded,
  onReloadDocument,
}: {
  // 기존 문서(새 문서면 null)
  doc: ResearchDocument | null;
  // 이어 쓸 active 초안
  draft: Draft | null;
  // 편집기를 열며 넣을 내용(예: 이전 버전으로 되돌리기). 초안으로 자동 저장된다.
  seed?: DraftInput;
  onDraft?: (draft: Draft) => void;
  onCommitted: (doc: ResearchDocument) => void;
  onClose?: () => void;
  onDiscarded: () => void;
  // 문서가 바뀌었다는 응답을 받았을 때 현재 문서를 다시 불러온다
  onReloadDocument?: () => Promise<void>;
}) {
  const editor = useDraftEditor({
    initial: { title: doc?.title ?? "", content: doc?.content ?? "" },
    draft: resumeDraft,
    base: doc ? { documentId: doc.id, baseDocumentVersion: doc.version, baseVersionId: doc.latestVersion.id } : null,
    onDraft,
  });
  const { values, status } = editor;
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [comparing, setComparing] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [showServerDiff, setShowServerDiff] = useState(false);

  // 문서가 바뀌어 멈춘 자동 저장은 최신 문서가 실제로 도착한 뒤(새 base) 다시 시작한다.
  // 다시 불러오기가 실패하면 문서가 그대로라 재시작하지 않고 멈춘 상태와 안내를 유지한다.
  const resumeOnReload = useRef(false);
  const docKey = doc ? `${doc.version}:${doc.latestVersion.id}` : "";
  useEffect(() => {
    if (!resumeOnReload.current) return;
    resumeOnReload.current = false;
    editor.resume();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey]);

  // 되돌리기 등으로 받은 내용은 열 때 한 번만 넣는다.
  const seeded = useRef(false);
  useEffect(() => {
    if (seed && !seeded.current) {
      seeded.current = true;
      editor.setValues(seed);
    }
  }, [seed, editor]);

  // 편집을 시작한 기준(이어 쓰는 초안의 base, 아니면 열 때의 문서). 문서가 바뀌어 초안을 새 base로 다시 만들더라도
  // 처음 기준을 기억해, 그 뒤 바뀐 내용을 비교하지 않고 덮어쓰지 않게 한다.
  const [startBase, setStartBase] = useState(() =>
    resumeDraft ? { version: resumeDraft.baseDocumentVersion, id: resumeDraft.baseVersionId } : doc ? { version: doc.version, id: doc.latestVersion.id } : null
  );
  // 초안을 시작한 뒤 문서가 바뀌었는지(다른 저장·제안 수락·발행 등). 저장 전에 현재 버전과 비교하게 한다.
  const baseChanged = !!doc && !!startBase && (startBase.id !== doc.latestVersion.id || startBase.version !== doc.version);

  // 저장·닫기·버리기는 끝나면 편집기를 닫는다. 진행 중에 입력을 받으면 닫히면서 그 입력이 사라지므로(리뷰 F1),
  // 시작부터 끝까지 입력과 다른 동작을 잠그고, 끝내지 못한 경우(검증·비교 필요·실패)에만 다시 연다.
  const [finishing, setFinishing] = useState<null | "commit" | "close" | "discard">(null);
  async function finish(kind: "commit" | "close" | "discard", action: () => Promise<boolean>) {
    if (finishing) return;
    setFinishing(kind);
    let done = false;
    try {
      done = await action();
    } finally {
      if (!done) setFinishing(null);
    }
  }

  // 반환값: 편집기를 닫는 단계까지 갔는지
  async function commit(): Promise<boolean> {
    setFailure(null);
    setNote(null);
    if (!values.title.trim() || !values.content.trim()) {
      setNote("제목과 본문을 모두 입력해야 저장할 수 있습니다.");
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
    if (baseChanged && !comparing) {
      setComparing(true);
      setNote("초안을 시작한 뒤 문서가 바뀌었습니다. 아래 비교를 확인한 뒤 ‘비교했고 이 내용으로 저장’을 누르세요.");
      return false;
    }
    const body = doc
      ? { expectedDraftRevision: d.revision, expectedVersion: doc.version, ...(baseChanged ? { resolvedBaseVersionId: doc.latestVersion.id } : {}) }
      : { expectedDraftRevision: d.revision };
    const res = await run(`draft:commit:${d.id}:${JSON.stringify(body)}`, (ctx) => commitDraft(d.id, body, ctx));
    if (res.ok) {
      onCommitted(res.data.document);
      return true;
    }
    setFailure(res.failure);
    if (isReason(res.failure, "base_changed", "document_version")) {
      await onReloadDocument?.();
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
    const res = await run(`draft:discard:${d.id}:${d.revision}`, (ctx) => discardDraft(d.id, d.revision, ctx));
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
  return (
    <div className="space-y-4">
      <p className="text-xs text-stone-500">
        입력은 초안으로 자동 저장되어 새로고침하거나 다른 기기에서 열어도 이어 쓸 수 있습니다. 버전은 ‘{doc ? "새 버전으로 저장" : "문서 저장"}’을 누를 때만
        만들어지고, 공개 중인 내용은 다시 공개하기 전까지 바뀌지 않습니다.
      </p>
      <Field label="제목">
        <input
          value={values.title}
          onChange={(e) => editor.setValues({ ...values, title: e.target.value })}
          maxLength={LIMITS.title}
          readOnly={locked}
          className={`${inputClass} read-only:bg-stone-50`}
        />
      </Field>
      <MarkdownEditor value={values.content} onChange={(content) => editor.setValues({ ...values, content })} readOnly={locked} />
      {locked && (
        <p role="status" className="text-xs text-stone-600">
          {finishing === "commit" ? "저장하는 중에는 입력을 고칠 수 없습니다." : finishing === "discard" ? "초안을 버리는 중입니다." : "초안을 저장하고 닫는 중입니다."}
        </p>
      )}
      <StatusLine status={status} onRetry={() => void editor.flush()} />

      {status.kind === "conflict" && (
        <Notice tone="warn">
          다른 탭이나 기기에서 이 문서의 초안이 바뀌었습니다
          {status.server ? `(서버 초안 ${formatDateTime(status.server.updatedAt)})` : ""}. 자동 저장을 멈췄습니다. 어느 쪽으로 이어 쓸지 고르세요.
          {status.server ? (
            <span className="mt-2 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => {
                  const server = status.server!;
                  setStartBase({ version: server.baseDocumentVersion, id: server.baseVersionId });
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
        <TextDiff before={status.server.content ?? ""} after={values.content} beforeTitle={status.server.title} afterTitle={values.title} />
      )}

      {stopped && (
        <Notice tone="warn">
          자동 저장을 멈췄습니다. {researchErrorMessage(stopped) ?? "초안을 저장하지 못했습니다."} 입력은 이 화면에 그대로 있습니다.{" "}
          {isReason(stopped, "base_changed", "document_version") && onReloadDocument && (
            <button
              type="button"
              onClick={() => {
                resumeOnReload.current = true;
                void onReloadDocument();
              }}
              className="font-medium underline hover:no-underline"
            >
              최신 문서를 불러와 계속
            </button>
          )}
          {isReason(stopped, "draft_closed") && (
            <button type="button" onClick={() => editor.resume({ draft: null })} className="font-medium underline hover:no-underline">
              새 초안으로 계속
            </button>
          )}
          {!isReason(stopped, "base_changed", "document_version", "draft_closed", "archived") && (
            <button type="button" onClick={() => editor.resume()} className="font-medium underline hover:no-underline">
              다시 시도
            </button>
          )}
        </Notice>
      )}

      {doc && baseChanged && (
        <Notice tone="info">
          이 초안을 시작한 뒤 문서가 바뀌었습니다(현재 v{doc.latestVersion.number}). 저장하기 전에 현재 버전과 비교하세요.{" "}
          <button type="button" onClick={() => setComparing(!comparing)} className="font-medium underline hover:no-underline">
            {comparing ? "비교 닫기" : "현재 버전과 비교"}
          </button>
        </Notice>
      )}
      {doc && comparing && (
        <div className="space-y-2 rounded-lg p-3 ring-1 ring-indigo-200">
          <p className="text-xs text-stone-600">현재 문서(v{doc.latestVersion.number})에서 내 초안으로 바뀌는 내용입니다.</p>
          <TextDiff before={doc.content} after={values.content} beforeTitle={doc.title} afterTitle={values.title} />
        </div>
      )}

      {note && <p role="alert" className="text-sm text-amber-800">{note}</p>}
      {failure && <ErrorNotice failure={failure} prefix="저장하지 못했습니다." />}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void finish("commit", commit)}
          disabled={busy || locked || status.kind === "conflict"}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {finishing === "commit" ? "저장 중..." : doc ? (baseChanged && comparing ? "비교했고 이 내용으로 저장" : "새 버전으로 저장") : "문서 저장"}
        </button>
        {onClose && (
          <button type="button" onClick={() => void finish("close", close)} disabled={busy || locked} className="rounded-lg px-4 py-2 text-sm text-stone-600 hover:bg-stone-100">
            편집 닫기(초안 보관)
          </button>
        )}
        {confirmDiscard ? (
          <span className="flex items-center gap-2 text-sm">
            <span className="text-stone-600">{doc ? "초안을 버리고 저장된 문서로 돌아갈까요?" : "이 초안을 버릴까요?"}</span>
            <button type="button" onClick={() => void finish("discard", discard)} disabled={busy || locked} className="font-medium text-red-700 underline hover:no-underline">
              초안 버리기
            </button>
            <button type="button" onClick={() => setConfirmDiscard(false)} className="text-stone-600 underline hover:no-underline">
              취소
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirmDiscard(true)} disabled={busy || locked} className="ml-auto text-sm text-stone-500 hover:text-red-700">
            초안 버리기…
          </button>
        )}
      </div>
    </div>
  );
}
