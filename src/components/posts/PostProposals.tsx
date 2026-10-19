"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { acceptPostProposal, comparePostProposal, discardPostProposal, getPostDraft, getPostProposal, listPostProposals } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { Post, PostDraft, PostProposal, PostText } from "@/lib/posts/types";
import { EMPTY_TEXT, textOf } from "@/lib/posts/usePostDraft";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import BodyDiff from "@/components/blocks/BodyDiff";
import PostMetaDiff from "./PostMetaDiff";

// #28 Codex 글 제안(D1: 언제나 제안 → 사람의 수락). 수락하기 전에는 글·초안이 바뀌지 않으며, 수락하면 새 버전 하나가 된다.
// 새 글 제안은 수락할 때 처음 글이 만들어진다.
// #32: 본문 비교는 글자(projection)와 서식·배치를 따로 보인다. 현재 글(또는 새 글 연결 초안)과 비교할 때는
// 서버 comparison API로 지금 기준에서 다시 계산한 서식 변화를 쓴다(수락 CAS는 그대로).

export const NEW_PROPOSAL_PATH = "/write/new/";
export const newProposalHref = (id: string) => `${NEW_PROPOSAL_PATH}?proposal=${encodeURIComponent(id)}`;

const STATUS_LABELS: Record<PostProposal["status"], string> = { pending: "검토 대기", accepted: "수락함", discarded: "버림" };

type Target =
  // 기존 글. draft: 이 글의 active 초안(있으면 비교 기준이며 수락 때 함께 닫힌다)
  | { kind: "post"; post: Post; draft: PostDraft | null; editing: boolean }
  // 새 글 제안
  | { kind: "new" };

// 새 글 제안이 묶인 초안(사람이 쓰던 새 글). 수락하려면 그 초안이 아직 작성 중이어야 한다.
function useBaseDraft(p: PostProposal | null) {
  const id = p && p.postId === null ? p.baseDraftId : null;
  const load = useLoad(`post-draft-by-id:${id ?? "none"}`, () =>
    id ? getPostDraft(id) : Promise.resolve({ ok: true as const, status: 200, data: null as PostDraft | null })
  );
  return load.result;
}

const WARNING_TEXT: Record<string, string> = {
  code_language_downgraded: "코드 언어 표기를 알아보지 못한 코드 블록은 일반 텍스트(plain)로 바꿨습니다. 코드 내용은 그대로입니다.",
};

/** 제안 본문 비교. serverBase면(현재 글 head·새 글 연결 초안 기준) 서버 비교의 서식 변화를 쓴다. */
function ProposalBody({ p, before, proposed, serverBase }: { p: PostProposal; before: PostText; proposed: PostText; serverBase: string | null }) {
  const { csrfToken } = useAuth();
  const key = serverBase && csrfToken ? `post-proposal-comparison:${p.id}:${p.version}:${serverBase}` : "post-proposal-comparison:none";
  const { result } = useLoad(key, () =>
    serverBase && csrfToken
      ? comparePostProposal(p.id, proposed.content, csrfToken)
      : Promise.resolve({ ok: true as const, status: 200, data: null })
  );
  const server = result?.ok ? result.data : null;
  return (
    <div className="space-y-2">
      {(p.conversionWarnings ?? []).map((w) => (
        <p key={w.code} className="text-xs text-amber-800">
          {WARNING_TEXT[w.code] ?? "제안을 블록으로 바꾸며 일부 표기를 바꿨습니다."}
        </p>
      ))}
      {serverBase && result && !result.ok && (
        <p className="text-xs text-amber-800">서버 비교를 불러오지 못해 화면에서 같은 규칙으로 계산한 서식 변화를 보입니다.</p>
      )}
      <BodyDiff before={before.content} after={proposed.content} beforeTitle={before.title} afterTitle={proposed.title} changes={server?.formatChanges} />
    </div>
  );
}

export function ProposalReview({
  summary,
  target,
  onDone,
}: {
  summary: Pick<PostProposal, "id" | "version">;
  target: Target;
  onDone: (accepted: Post | null) => void;
}) {
  const { result } = useLoad(`post-proposal:${summary.id}:${summary.version}`, () => getPostProposal(summary.id));
  const p = result?.ok ? result.data : null;
  const base = useBaseDraft(p);
  const { run, busy } = useMutation();
  const draft = target.kind === "post" ? target.draft : null;
  const [against, setAgainst] = useState<"head" | "draft">(draft ? "draft" : "head");
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [confirm, setConfirm] = useState(false);

  if (!result) return <p className="text-sm text-stone-500">제안을 불러오는 중...</p>;
  if (!result.ok || !p) return <ErrorNotice failure={result.ok ? { kind: "invalid", status: 200 } : result.failure} prefix="제안을 불러오지 못했습니다." describe={postErrorMessage} />;
  const pending = p.status === "pending";
  const proposed = textOf(p);

  // 새 글: 묶인 초안이 있으면 그 초안의 현재 내용과, 없으면 빈 글과 비교한다.
  let baseDraft: PostDraft | null = null;
  let baseClosed = false;
  if (target.kind === "new" && p.baseDraftId) {
    if (!base) return <p className="text-sm text-stone-500">제안의 기준 초안을 확인하는 중...</p>;
    if (base.ok && base.data && base.data.state === "active") baseDraft = base.data;
    else baseClosed = true;
  }
  const headText: PostText =
    target.kind === "post" ? textOf(target.post.latestVersion) : EMPTY_TEXT;
  const useDraft = target.kind === "post" && against === "draft" && !!draft;
  const before: PostText = useDraft ? textOf(draft!) : baseDraft ? textOf(baseDraft) : headText;
  const stale = target.kind === "post" && p.baseVersionId !== target.post.latestVersion.id;

  async function accept() {
    if (!p) return;
    setFailure(null);
    // 주소는 보내지 않는다(#31 W1): 새 글은 서버가 정하고, 기존 글은 저장된 주소를 그대로 쓴다.
    const text = proposed;
    const draftPair = target.kind === "post" ? draft : baseDraft;
    const body = {
      ...text,
      expectedProposalVersion: p.version,
      ...(target.kind === "post" ? { expectedVersion: target.post.version, resolvedBaseVersionId: target.post.latestVersion.id } : {}),
      ...(draftPair ? { draftId: draftPair.id, expectedDraftRevision: draftPair.revision } : {}),
    };
    const res = await run(`post-proposal:accept:${p.id}:${JSON.stringify({ ...body, content: undefined })}`, (ctx) => acceptPostProposal(p.id, body, ctx));
    if (res.ok) onDone(res.data.post);
    else setFailure(res.failure);
  }

  async function discard() {
    if (!p) return;
    setFailure(null);
    const res = await run(`post-proposal:discard:${p.id}:${p.version}`, (ctx) => discardPostProposal(p.id, p.version, ctx));
    if (res.ok) onDone(null);
    else setFailure(res.failure);
  }

  const editing = target.kind === "post" && target.editing;
  return (
    <div className="space-y-3">
      {pending && target.kind === "post" && (
        <div role="group" aria-label="비교 기준" className="flex flex-wrap gap-3 text-sm">
          <label className="flex items-center gap-1">
            <input type="radio" checked={!useDraft} onChange={() => setAgainst("head")} />
            현재 글(v{target.post.latestVersion.number})과 비교
          </label>
          {draft && (
            <label className="flex items-center gap-1">
              <input type="radio" checked={useDraft} onChange={() => setAgainst("draft")} />내 초안({formatDateTime(draft.updatedAt)})과 비교
            </label>
          )}
        </div>
      )}
      {pending && target.kind === "new" && (
        <p className="text-xs text-stone-600">
          {baseDraft
            ? `작성 중인 새 글 초안(${formatDateTime(baseDraft.updatedAt)} 자동 저장)에서 이 제안으로 바뀌는 내용입니다. 수락하면 이 제안으로 새 글이 만들어지고 그 초안은 닫힙니다(초안 내용은 기록으로 보존).`
            : "Codex가 새 글로 제안한 내용입니다. 수락하면 이 내용으로 새 글과 첫 버전이 만들어지고 공개 주소가 자동으로 정해집니다(발행은 따로 합니다)."}
        </p>
      )}
      {pending && baseClosed && (
        <Notice tone="warn">이 제안이 기준으로 삼은 새 글 초안이 이미 저장되었거나 버려져 수락할 수 없습니다. 내용을 확인한 뒤 버리고, 필요하면 대화에서 다시 요청하세요.</Notice>
      )}
      {pending && stale && (
        <p className="text-xs text-amber-800">
          이 제안은 이전 글을 기준으로 만들어졌고 그 뒤 글이 바뀌었습니다. 수락하면 현재 글을 아래 제안 내용으로 바꾼 새 버전이 만들어집니다.
        </p>
      )}
      <PostMetaDiff before={before} after={proposed} />
      <ProposalBody
        key={`${p.id}:${against}`}
        p={p}
        before={before}
        proposed={proposed}
        serverBase={
          !pending || useDraft
            ? null
            : target.kind === "post"
              ? `post:${target.post.version}:${target.post.latestVersion.id}`
              : baseDraft
                ? `draft:${baseDraft.id}:${baseDraft.revision}`
                : "new"
        }
      />
      {failure && <ErrorNotice failure={failure} prefix="처리하지 못했습니다." describe={postErrorMessage} />}
      {pending &&
        (editing ? (
          <p className="text-xs text-stone-600">편집 중에는 수락할 수 없습니다. 편집을 닫은 뒤(초안은 보관됩니다) 다시 검토하세요.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            {!baseClosed &&
              (confirm ? (
                <>
                  <span className="text-sm text-stone-700">
                    {target.kind === "new"
                      ? "수락하면 이 내용으로 새 글이 만들어집니다."
                      : draft
                        ? "수락하면 이 제안이 새 버전이 되고, 작성 중인 초안은 닫힙니다(초안 내용은 기록으로 보존)."
                        : "수락하면 이 제안이 새 버전이 됩니다."}
                  </span>
                  <button
                    type="button"
                    onClick={() => void accept()}
                    disabled={busy}
                    className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    수락 확인
                  </button>
                  <button type="button" onClick={() => setConfirm(false)} className="text-sm text-stone-600 underline hover:no-underline">
                    취소
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirm(true)}
                  disabled={busy}
                  className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  제안 수락
                </button>
              ))}
            <button type="button" onClick={() => void discard()} disabled={busy} className="text-sm text-stone-600 hover:text-red-700">
              제안 버리기
            </button>
          </div>
        ))}
      {!pending && p.acceptedPostId && target.kind === "new" && (
        <Link href={`/write/?id=${encodeURIComponent(p.acceptedPostId)}`} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          수락해 만든 글 보기 →
        </Link>
      )}
    </div>
  );
}

/** 기존 글의 제안 목록(글 화면). 대화의 '제안 검토' 링크(#proposal-<id>)로 오면 그 제안을 펼친다. */
export default function PostProposalPanel({
  post,
  draft,
  editing,
  onAccepted,
}: {
  post: Post;
  draft: PostDraft | null;
  editing: boolean;
  onAccepted: (post: Post) => void;
}) {
  const [tick, setTick] = useState(0);
  const list = usePagedList(`post-proposals:${post.id}:${post.version}:${tick}`, (cursor) => listPostProposals(cursor, post.id));
  const [open, setOpen] = useState<string | null>(null);
  const [history, setHistory] = useState(false);
  const pending = list.items.filter((p) => p.status === "pending");
  const closed = list.items.filter((p) => p.status !== "pending");
  const section = useRef<HTMLElement>(null);

  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => {
    const pick = () => {
      const m = /^#proposal-([0-9a-f-]+)$/i.exec(window.location.hash);
      if (!m) return false;
      setTarget(m[1]);
      setOpen(m[1]);
      return true;
    };
    pick();
    const onHash = () => {
      if (pick()) setTick((t) => t + 1);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const found = !!target && list.items.some((p) => p.id === target);
  useEffect(() => {
    if (found) section.current?.scrollIntoView({ block: "start" });
  }, [found, tick]);

  if (!list.items.length && !list.failure) return null;
  const row = (p: PostProposal) => (
    <li key={p.id} className="rounded-lg ring-1 ring-stone-200">
      <button
        onClick={() => setOpen(open === p.id ? null : p.id)}
        aria-expanded={open === p.id}
        className="flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm hover:bg-stone-50"
      >
        <span className="min-w-0 break-words">
          <span className="mr-2 text-xs font-medium text-indigo-700">Codex 제안</span>
          <span className="text-stone-800">{p.title}</span>
        </span>
        <span className="shrink-0 text-xs text-stone-500">
          {STATUS_LABELS[p.status]} · {formatDateTime(p.createdAt)}
        </span>
      </button>
      {open === p.id && (
        <div className="px-4 pb-4">
          <ProposalReview
            summary={p}
            target={{ kind: "post", post, draft, editing }}
            onDone={(accepted) => {
              setOpen(null);
              setTick(tick + 1);
              if (accepted) onAccepted(accepted);
            }}
          />
        </div>
      )}
    </li>
  );

  return (
    <section id="proposals" ref={section} className="scroll-mt-4">
      <h2 className="mb-1 text-lg font-semibold text-stone-800">Codex 제안</h2>
      <p className="mb-3 text-xs text-stone-500">
        대화에서 Codex가 이 글을 고치면 제안으로 남습니다. 수락하기 전에는 글이 바뀌지 않으며, 비교한 뒤 수락하면 새 버전이 됩니다(발행은 따로).
      </p>
      {list.failure && <ErrorNotice failure={list.failure} prefix="제안 목록을 불러오지 못했습니다." describe={postErrorMessage} />}
      {pending.length > 0 ? <ul className="space-y-2">{pending.map(row)}</ul> : !list.failure && <p className="text-sm text-stone-500">검토할 제안이 없습니다.</p>}
      {closed.length > 0 && (
        <div className="mt-3">
          <button onClick={() => setHistory(!history)} aria-expanded={history} className="text-sm text-stone-600 hover:text-stone-900">
            지난 제안 {closed.length}건 {history ? "접기" : "보기"}
          </button>
          {history && <ul className="mt-2 space-y-2">{closed.map(row)}</ul>}
        </div>
      )}
      {list.nextCursor && (
        <button onClick={() => void list.loadMore()} className="mt-2 text-sm font-medium text-indigo-600 hover:text-indigo-800">
          제안 더 보기
        </button>
      )}
    </section>
  );
}

/** 검토할 새 글 제안(아직 글이 아닌 것). baseDraftId를 주면 그 초안에 대한 제안만. */
export function NewPostProposals({ baseDraftId, reload = "" }: { baseDraftId?: string | null; reload?: string }) {
  const list = usePagedList(`new-post-proposals:${reload}`, (cursor) => listPostProposals(cursor));
  const items = list.items.filter((p) => p.postId === null && p.status === "pending" && (baseDraftId === undefined || p.baseDraftId === baseDraftId));
  // 앞 페이지에 해당 제안이 없으면 남은 페이지를 이어서 찾는다(목록은 모든 글·상태를 함께 준다).
  const { loaded, loading, nextCursor, loadMore } = list;
  const searching = loaded && items.length === 0 && !!nextCursor && !list.failure;
  useEffect(() => {
    if (searching && !loading) void loadMore();
  }, [searching, loading, loadMore]);
  if (!items.length && !list.failure) return null;
  return (
    <section aria-label="Codex 새 글 제안" className="space-y-2 rounded-lg bg-indigo-50/50 p-3 ring-1 ring-indigo-200">
      <h2 className="text-sm font-semibold text-indigo-900">{baseDraftId ? "이 초안에 대한 Codex 제안" : "Codex가 제안한 새 글"}</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="제안 목록을 불러오지 못했습니다." describe={postErrorMessage} />}
      <ul className="space-y-1 text-sm">
        {items.map((p) => (
          <li key={p.id} className="flex flex-wrap items-baseline justify-between gap-2">
            <Link href={newProposalHref(p.id)} className="text-indigo-700 hover:text-indigo-900">
              {p.title || "(제목 없음)"} — 검토
            </Link>
            <span className="text-xs text-stone-500">{formatDateTime(p.createdAt)}</span>
          </li>
        ))}
      </ul>
      {!searching && nextCursor && (
        <button type="button" onClick={() => void loadMore()} disabled={loading} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
          제안 더 보기
        </button>
      )}
    </section>
  );
}
