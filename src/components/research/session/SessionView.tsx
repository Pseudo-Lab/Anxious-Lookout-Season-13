"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent, type KeyboardEvent } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import {
  archiveSession,
  getSession,
  getSessionItemRaw,
  interruptSession,
  readCodexStatus,
  researchErrorMessage,
  sendSessionMessage,
} from "@/lib/research/api";
import type { LiveItem } from "@/lib/research/stream";
import { useChatStream } from "@/components/research/session/useChatStream";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { isUncertain, settlesRetry, uuidV4 } from "@/lib/research/idempotency";
import {
  clearPendingSend,
  loadPendingSend,
  savePendingSend,
  type PendingSend,
} from "@/lib/research/pendingSend";
import { storeGeneration } from "@/lib/research/pendingStore";
import { clearDraft, loadDraft, saveDraft } from "@/lib/research/chatState";
import { useAuth } from "@/hooks/useAuth";
import type { NoticeItem, PageDescriptor, SessionDetail, SessionItem, SessionSummary, ToolCallItem, UnknownItem } from "@/lib/research/types";
import { describePage, kindText, pageUnresolved, sameDescriptor, scopeLabel, useRegisteredPage } from "@/lib/research/pageContext";
import { own } from "@/lib/research/own";
import { BASE_PATH } from "@/lib/constants";
import {
  PATCH_STATUS_LABELS,
  PATCH_TOOL,
  SHELL_STATUS_LABELS,
  SHELL_TOOL,
  formatDuration,
  patchChanges,
  patchKindLabel,
  patchNote,
  patchSummary,
  shellCwdLabel,
  shellEmptyOutput,
  shellNote,
  shellOutputLabel,
  shellView,
} from "@/lib/research/shell";
import { formatDateTime, toDisplayText } from "@/lib/research/format";
import {
  EMPTY_CONTINUITY,
  awaitingReply,
  chatStatus,
  isPendingInput,
  reconcile,
  type ChatStatus,
  type Continuity,
  type LocalSend,
  type TranscriptEntry,
} from "@/lib/research/transcript";
import { useStickToBottom } from "@/components/chat/useStickToBottom";
import {
  CodexStatusView,
  RunnerActionButton,
  codexBlocked,
  runnerState,
  useCodexStatus,
} from "@/components/research/CodexStatusBanner";
import { useCodexRunner } from "@/components/research/CodexStatusProvider";
import { useChat } from "@/components/chat/ChatProvider";
import type { FetchResult } from "@/lib/api/client";
import type { CodexStatus } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import SafeMarkdown, { StreamingMarkdown } from "@/components/research/SafeMarkdown";
import { SESSION_STATE_LABELS } from "@/components/research/ItemLists";

const POLL_MS = 2000;
// 접수된 전송을 확인하는 대화 조회가 일시 실패했을 때의 재조회 간격(늘려 가며 최대값까지).
const CONFIRM_RETRY_MS = 2000;
const CONFIRM_RETRY_MAX_MS = 15000;
// 이만큼 실행 중이면 결과 확인 중일 수 있다는 중립 안내를 보인다(서버 timeout·실패 판정이 아니다).
const SLOW_RUNNING_MS = 30000;
// 접수 종료 시각 뒤에도 서버가 아직 이용 가능이라고 답할 때의 재확인 간격(시계 차이·일시 조회 실패 대비)
const EXPIRY_RECHECK_MIN_MS = 5000;
const EXPIRY_RECHECK_MAX_MS = 30000;

// 저장 기록은 실행하지 않고 텍스트로만 보여준다.
function Inert({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-stone-900 p-3 font-mono text-xs text-stone-100">
      {toDisplayText(value)}
    </pre>
  );
}

function RawItem({ sessionId, itemId, label = "저장된 원본 전체" }: { sessionId: string; itemId: string; label?: string }) {
  const { result } = useLoad(`raw:${sessionId}:${itemId}`, () => getSessionItemRaw(sessionId, itemId));
  if (!result) return <p className="text-xs text-stone-500">원본을 불러오는 중...</p>;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="원본 기록을 불러오지 못했습니다." />;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-stone-900">{label}</p>
      <Inert value={result.data.raw} />
    </div>
  );
}

const TOOL_STATUS_LABELS: Record<string, string> = { rejected: "거부됨", inProgress: "진행 중", approval: "허가 확인 중" };

// #22 research_document_update는 문서를 바꾸지 않고 제안(Proposal)을 만든다. #28 D1: research_document_save(새 문서)와
// research_post_propose(글)도 제안이다. 도구 결과는 flat Proposal이며 출력(JSON 또는 text 조각)에서 제안 위치만 읽는다.
const PROPOSAL_TOOLS = new Set(["research_document_update", "research_document_save", "research_post_propose"]);
const enc = encodeURIComponent;
function proposalRef(item: ToolCallItem): { href: string; text: string } | null {
  if (!PROPOSAL_TOOLS.has(item.name) || item.status === "rejected") return null;
  const candidates: unknown[] = Array.isArray(item.output)
    ? item.output.map((part) => {
        const text = (part as { text?: unknown } | null)?.text;
        if (typeof text !== "string") return null;
        try {
          return JSON.parse(text) as unknown;
        } catch {
          return null;
        }
      })
    : [item.output];
  for (const c of candidates) {
    const v = c as { id?: unknown; documentId?: unknown; postId?: unknown; status?: unknown } | null;
    if (!v || typeof v.id !== "string" || v.status !== "pending") continue;
    const hash = `#proposal-${enc(v.id)}`;
    if (item.name === "research_post_propose") {
      if (typeof v.postId === "string") return { href: `/write/?id=${enc(v.postId)}${hash}`, text: "Codex가 글 수정 제안을 만들었습니다. 수락하기 전에는 글이 바뀌지 않습니다." };
      if (v.postId === null) return { href: `/write/new/?proposal=${enc(v.id)}`, text: "Codex가 새 글을 제안했습니다. 수락하기 전에는 글이 만들어지지 않습니다." };
      continue;
    }
    if (typeof v.documentId === "string")
      return { href: `/research/document/?id=${enc(v.documentId)}${hash}`, text: "Codex가 문서 수정 제안을 만들었습니다. 수락하기 전에는 문서가 바뀌지 않습니다." };
    if (v.documentId === null && item.name === "research_document_save")
      return { href: `/research/document/new/?proposal=${enc(v.id)}`, text: "Codex가 새 문서를 제안했습니다. 수락하기 전에는 문서가 만들어지지 않습니다." };
  }
  return null;
}

// 머리글에는 명령의 첫 줄만 짧게 보인다(전체는 펼쳐서 본다).
const commandPreview = (command: string | null) => {
  const line = (command ?? "").split("\n")[0];
  return line.length > 120 ? `${line.slice(0, 120)}…` : line;
};

// #90 스트림 항목을 기록 도구 항목 모양으로 옮긴다(완료 뒤 같은 id의 기록 항목으로 바뀐다). 명령 출력은 최근 조각만 있다.
function liveToolItem(item: LiveItem): ToolCallItem {
  if (item.kind === "commandExecution") {
    return {
      id: item.itemId,
      type: "tool_call",
      name: SHELL_TOOL,
      input: { command: item.command ?? null, cwd: null },
      output: item.output ? { stdout: item.output.text, stderr: null, combinedOutput: true, exitCode: null, truncated: item.output.truncated } : null,
      status: item.status,
    };
  }
  if (item.kind === "fileChange") {
    return { id: item.itemId, type: "tool_call", name: PATCH_TOOL, input: { changes: Array.isArray(item.changes) ? item.changes : [] }, output: null, status: item.status };
  }
  return { id: item.itemId, type: "tool_call", name: item.kind === "mcpToolCall" ? "MCP 도구" : "도구", input: null, output: null, status: item.status };
}

/** live: 스트림으로 받는 중인 항목(#90). 아직 기록이 없어 원본 보기를 두지 않는다. 같은 id의 기록 항목으로 바뀌어도 이어진다. */
function ToolCall({ sessionId, item, live = false }: { sessionId: string; item: ToolCallItem; live?: boolean }) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState(false);
  // #23: shell은 명령 실행으로 읽기 쉽게 보인다. 서버 기록은 그대로이며 모르는 값은 일반 도구처럼 표시한다.
  const shell = item.name === SHELL_TOOL ? shellView(item) : null;
  // #68: apply_patch(native 파일 수정)는 "파일 변경"으로 보인다. changes가 배열이 아니면 일반 도구처럼 표시한다.
  const patch = item.name === PATCH_TOOL ? patchChanges(item) : null;
  const proposal = proposalRef(item);
  const note = shell
    ? shellNote(item.status, shell)
    : patch
      ? patchNote(item.status, item.error?.code)
      : item.status === "rejected"
      ? item.error?.code === "tool_not_allowed"
        ? "이 질문을 보낸 범위(공개 글 읽기 등)에서 허용되지 않는 도구라 서버가 실행하지 않았습니다."
        : "서버가 이 도구 호출을 실행하지 않았습니다."
      : null;
  const code = shell ? shell.code : item.error?.code;
  const warn =
    item.status === "rejected" || ((shell !== null || patch !== null) && (item.status === "failed" || item.status === "timed_out"));
  const label = shell
    ? own(SHELL_STATUS_LABELS, item.status)
    : patch
      ? own(PATCH_STATUS_LABELS, item.status)
      : own(TOOL_STATUS_LABELS, item.status);
  const emptyOutput = shell ? shellEmptyOutput(item.status, shell) : null;
  return (
    <li className="rounded-md bg-stone-50 text-xs">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left hover:bg-stone-100"
      >
        <span className="min-w-0 break-words">
          <span className="mr-2 text-xs text-stone-500">{shell ? "명령 실행" : patch ? "파일 변경" : "도구"}</span>
          <span className="font-mono text-stone-900">
            {shell ? commandPreview(shell.command) || item.name : patch ? patchSummary(patch) || item.name : item.name}
          </span>
        </span>
        <span className={`shrink-0 text-xs ${warn ? "font-medium text-amber-700" : "text-stone-500"}`}>
          {label ?? item.status}
          {item.turnStatus === "interrupted" && " · 중지됨"}
        </span>
      </button>
      {note && (
        <p className="px-3 pb-2 text-xs text-amber-800">
          {note}
          {code && ` (${code})`}
        </p>
      )}
      {shell?.truncated && <p className="px-3 pb-2 text-xs text-stone-900">출력이 길어 저장 상한까지만 남았습니다.</p>}
      {proposal && (
        <p className="px-3 pb-2 text-xs text-indigo-800">
          {proposal.text}{" "}
          {/* 같은 문서·글을 보고 있으면 해당 제안으로 이동만 하고 목록을 새로 불러온다(hashchange). */}
          <a href={`${BASE_PATH}${proposal.href}`} className="font-medium underline hover:no-underline">
            제안 검토
          </a>
        </p>
      )}
      {item.modelMismatch && (
        <div className="px-3 pb-2">
          <ModelMismatchNote />
        </div>
      )}
      {open && (
        <div className="space-y-3 px-3 pb-3">
          {shell ? (
            <>
              <div className="space-y-1">
                <p className="text-xs font-medium text-stone-900">명령 ({shellCwdLabel(shell.cwd)})</p>
                <Inert value={shell.command ?? item.input} />
              </div>
              {shell.exitCode !== null && <p className="text-xs text-stone-900">종료 코드 {shell.exitCode}</p>}
              {shell.durationMs !== null && <p className="text-xs text-stone-900">실행 시간 {formatDuration(shell.durationMs)}</p>}
              {shell.stdout !== null && !(shell.combined && shell.stdout === "") && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-stone-900">{shellOutputLabel(shell)}</p>
                  <Inert value={shell.stdout} />
                </div>
              )}
              {emptyOutput && <p className="text-xs text-stone-900">{emptyOutput}</p>}
              {shell.stderr !== null && shell.stderr !== "" && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-stone-900">오류 출력</p>
                  <Inert value={shell.stderr} />
                </div>
              )}
              {shell.stdout === null && !shell.combined && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-stone-900">출력</p>
                  <Inert value={item.output} />
                </div>
              )}
            </>
          ) : patch ? (
            <ul className="space-y-3">
              {patch.map((change, i) => (
                <li key={i} className="space-y-1">
                  <p className="text-xs font-medium text-stone-900">
                    {patchKindLabel(change)} <span className="font-mono">{change.path ?? "(경로 없음)"}</span>
                    {change.movePath && <span className="font-mono"> → {change.movePath}</span>}
                  </p>
                  {change.diff ? <Inert value={change.diff} /> : <p className="text-xs text-stone-500">변경 내용이 기록되지 않았습니다.</p>}
                </li>
              ))}
              {patch.length === 0 && <li className="text-xs text-stone-500">기록된 변경이 없습니다.</li>}
            </ul>
          ) : (
            <>
              <div className="space-y-1">
                <p className="text-xs font-medium text-stone-900">입력</p>
                <Inert value={item.input} />
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium text-stone-900">출력</p>
                <Inert value={item.output} />
              </div>
            </>
          )}
          {live ? null : raw ? (
            <RawItem sessionId={sessionId} itemId={item.id} />
          ) : (
            <button onClick={() => setRaw(true)} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
              저장된 원본 기록 보기
            </button>
          )}
        </div>
      )}
    </li>
  );
}

// 실패로 남은 입력(Codex 기록에 없음)만 경고로 보인다. 정상 실행 중인 접수 항목(status pending)은 일반 말풍선이다(#89).
const NOT_RECORDED_LABEL = "미기록 입력";

// 고정 모델 확인 실패로 중단된 실행의 항목 표시(다른 모델명·정책 사유는 표시하지 않는다).
function ModelMismatchNote() {
  return (
    <p className="mt-1 rounded bg-red-50 px-2 py-1 text-xs text-red-700">
      <span className="font-semibold">정상 응답 아님</span> — 고정 모델을 확인하지 못해 중단된 실행의 기록입니다. 이 실행의 결과는
      사용하지 마세요.
    </p>
  );
}

// 질문의 화면 문맥 표시(공개 글이면 읽기 범위). 서버가 접수한 값이며, 보내는 중에는 보낸 페이지 종류만 먼저 보인다.
function ScopeBadge({ page }: { page: { kind: string; title?: string } }) {
  return (
    <span
      className="max-w-full break-words rounded bg-white/80 px-1.5 py-0.5 font-normal text-stone-500 ring-1 ring-stone-200"
      title={kindText(page.kind).can}
    >
      {scopeLabel(page.kind, "sent")}
      {page.title ? ` · ${page.title}` : ""}
    </span>
  );
}

/**
 * 메시지 하나. sending이면 화면이 먼저 그린 내 말풍선(서버 응답 전)이다. 같은 key의 서버 항목으로 바뀌어도
 * 같은 컴포넌트라 다시 그려지지 않고 이어진다(#89).
 */
function Message({
  sessionId,
  item,
  sending,
  live = false,
}: {
  sessionId: string;
  item: Extract<SessionItem, { type: "message" }>;
  sending?: { page?: PageDescriptor };
  // #90 스트림으로 받는 중인 답변(기록 전). 같은 id의 기록 항목으로 바뀌어도 같은 말풍선이다.
  live?: boolean;
}) {
  const [raw, setRaw] = useState(false);
  const mine = item.role === "user";
  // 플랫폼이 보존한 입력: Codex 원본 이력에 기록되지 않았으므로 모델이 본 대화나 성공으로 표시하지 않는다.
  const platform = item.source === "platform";
  const unrecorded = platform && item.status === "not_recorded";
  const scope = item.scope?.page ?? (sending?.page ? { kind: sending.page.kind } : undefined);
  return (
    // 내 질문은 오른쪽의 옅은 말풍선, Codex 답변은 테두리 없이 본문처럼 보여 답변을 중심에 둔다.
    <li
      data-role={item.role}
      data-sending={sending ? "" : undefined}
      className={`min-w-0 ${mine ? "ml-auto w-fit max-w-[90%] rounded-2xl bg-stone-100 px-3 py-2" : "py-1"} ${
        unrecorded ? "border border-dashed border-amber-400" : ""
      }`}
    >
      <p className="mb-1 flex flex-wrap items-center gap-1.5 text-[13px] font-medium text-stone-500">
        {mine ? "나" : "Codex"}
        {mine && scope && <ScopeBadge page={scope} />}
        {unrecorded && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">{NOT_RECORDED_LABEL}</span>}
        {!mine && item.turnStatus === "interrupted" && (
          <span className="rounded-full bg-stone-100 px-2 py-0.5 font-normal text-stone-600">{item.partial ? "중지됨(부분)" : "중지됨"}</span>
        )}
      </p>
      {mine ? (
        <p className="whitespace-pre-wrap text-sm text-stone-900 [overflow-wrap:anywhere]">{item.text}</p>
      ) : live ? (
        <StreamingMarkdown content={item.text} />
      ) : (
        <SafeMarkdown content={item.text} compact />
      )}
      {item.displayOnly && (
        <p className="mt-1 text-xs text-stone-500">
          중지하기 전까지 받은 부분 답변입니다. Codex 대화 기록에는 남지 않아, 이어서 질문해도 Codex는 이 내용을 보지 않습니다.
        </p>
      )}
      {item.modelMismatch && <ModelMismatchNote />}
      {unrecorded && (
        <p className="mt-1 text-xs text-amber-800">
          Codex 대화 기록에 반영되지 않은 입력입니다. 내 대화에는 보존되며, 필요하면 새 메시지로 다시 보내세요.
        </p>
      )}
      <div className="mt-1">
        {sending || live ? (
          // 서버 항목으로 바뀔 때 높이가 달라지지 않게 같은 자리를 비워 둔다.
          <span aria-hidden="true" className="invisible text-[13px]">
            원본 기록
          </span>
        ) : raw ? (
          <RawItem
            sessionId={sessionId}
            itemId={item.id}
            label={platform ? "보존된 입력 기록 (Codex 원본 아님)" : item.displayOnly ? "표시 전용 부분 답변 (Codex 원본 아님)" : undefined}
          />
        ) : (
          <button onClick={() => setRaw(true)} className="text-[13px] text-stone-500 hover:text-stone-700">
            {unrecorded ? "보존된 입력 기록" : "원본 기록"}
          </button>
        )}
      </div>
    </li>
  );
}

// 응답을 기다리는 동안의 "생각 중" 표시. 문구는 e2e가 찾는 기존 안내를 그대로 쓴다.
function Thinking({ slow, accepted }: { slow: boolean; accepted: boolean }) {
  return (
    <li data-chat-thinking="" className="min-w-0 py-1">
      <p className="mb-1 text-[13px] font-medium text-stone-500">Codex</p>
      <div className="flex items-center gap-2 text-sm text-stone-500">
        <span aria-hidden="true" className="flex shrink-0 gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1.5 w-1.5 animate-bounce rounded-full bg-stone-400 motion-reduce:animate-none"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </span>
        <span>
          {slow ? "응답을 기다리거나 실행 결과를 확인 중입니다. 오래 걸려도 자동으로 다시 보내지 않습니다." : "Codex가 응답하는 중입니다."}
        </span>
      </div>
      {accepted && <p className="mt-1 text-xs text-stone-500">페이지를 닫았다가 다시 열어도 이어서 확인할 수 있습니다.</p>}
    </li>
  );
}

// #90 서버 알림 항목. 모르는 code는 code만 보인다.
const NOTICE_TEXT: Record<string, string> = {
  partial_removed: "중지한 답변의 부분 내용이 보존 한도를 넘어 삭제되었습니다.",
};
function NoticeRow({ item }: { item: NoticeItem }) {
  return (
    <li data-notice={item.code} className="text-xs text-stone-500">
      {own(NOTICE_TEXT, item.code) ?? `알림 (${item.code})`}
    </li>
  );
}

// 이 화면이 아직 모르는 기록 항목. 대화 전체는 그대로 보이고 이 항목만 종류를 알린다.
function UnknownRow({ item }: { item: UnknownItem }) {
  return <li className="text-xs text-stone-500">이 화면에서 표시하지 못하는 기록 항목입니다 ({item.originalType}).</li>;
}

// 스크린리더 알림은 상태가 바뀔 때 한 번만, 짧게 한다(대화 목록 자체는 live region이 아니다).
// 화면에 보이는 안내와 같은 문구를 쓰지 않는다(같은 글자가 두 군데 있으면 화면 낭독이 겹치고 e2e 문구 찾기도 모호해진다).
function announcement(prev: ChatStatus, next: ChatStatus, prevText: string): string {
  if (next === "submitted") return "메시지 전송됨, Codex 응답 대기";
  if (next === "waiting") return prev === "submitted" ? prevText : "Codex 응답 대기 중";
  if (next === "streaming") return "Codex 답변 수신 중";
  if (next === "error") return "Codex 응답 실패";
  if (next === "ready" && (prev === "waiting" || prev === "streaming")) return "Codex 답변 도착";
  return "";
}

// 다음 질문과 함께 보낼 화면 문맥. 같은 대화의 이전 내용은 그대로 이어진다. #14부터 소유자 화면은 참고일 뿐 권한을 바꾸지 않고,
// 공개 글만 읽기 범위로 제한된다. 이름은 입력창 옆(NextScope), 설명은 입력창 바로 위 본문 흐름(ScopeHint)에 둔다.
function NextScope({ page }: { page: PageDescriptor }) {
  const text = kindText(page.kind);
  return (
    <p className="min-w-0 flex-1 truncate text-xs text-stone-500" title={text.can} aria-live="polite">
      <span className="font-medium text-stone-900">{scopeLabel(page.kind, "next")}</span>
    </p>
  );
}

function ScopeHint({ page }: { page: PageDescriptor }) {
  return (
    <p className="text-xs leading-relaxed text-stone-500">
      {kindText(page.kind).can} 이전 대화 내용은 이 대화에 계속 이어집니다.
    </p>
  );
}

interface StopControl {
  label: string;
  disabled: boolean;
  note: string | null;
  onStop: () => void;
}

function Composer({
  session,
  accountId,
  page,
  blocked,
  codex,
  awaiting,
  atBottom,
  stop,
  onRecheck,
  onSending,
  onSent,
  onJump,
}: {
  session: SessionDetail;
  accountId: string;
  // 지금 보고 있는 페이지. 보내기를 누른 순간의 값이 그 요청에 고정된다.
  page: PageDescriptor;
  // 서버가 지금 접수할 수 없다고 확인한 상태. 보내기만 잠그고 입력·이전 대화는 그대로 둔다.
  blocked: boolean;
  // 잠금 사유 안내용 상태(실행 환경 상태면 준비 버튼을 함께 보인다).
  codex: FetchResult<CodexStatus> | null;
  // 응답을 기다리는 중(보내는 중·서버 실행 중). 입력·보내기를 잠근다. 보내기 요청이 끝나도 다음 조회가 끝날 때까지 이어진다.
  awaiting: boolean;
  // 대화 바닥이 보이는지(아니면 "최신으로" 버튼).
  atBottom: boolean;
  // #90 중지 버튼(스트림을 지원하는 서버에서 응답을 기다리는 동안만). null이면 보이지 않는다.
  stop: StopControl | null;
  onRecheck: () => void;
  // 보내기 시작. local은 화면이 먼저 그릴 내 말풍선이다(결과 미확인 재전송은 서버에 이미 있을 수 있어 그리지 않는다).
  onSending: (local: LocalSend | null) => void;
  // 보내기 요청이 끝남(접수되면 서버가 돌려준 요약, 아니면 null). 접수가 대화 조회로 확인된 뒤에 끝난다
  // (그사이 입력창이 열렸다 닫히거나 말풍선이 사라지지 않게).
  onSent: (accepted: SessionSummary | null) => Promise<void>;
  onJump: () => void;
}) {
  const { csrfToken, refresh } = useAuth();
  // Composer는 세션을 불러온 뒤 브라우저에서만 그려지므로 sessionStorage를 바로 읽어도 된다.
  const [pending, setPending] = useState<PendingSend | null>(() => loadPendingSend(accountId, session.id));
  // 이 화면이 시작한 보관소 세대와 아는 key들. 로그아웃 뒤 늦게 끝난 전송이 보관소에 다시 쓰지 않게 한다.
  const [guard] = useState(() => ({
    generation: storeGeneration(),
    knownKeys: new Set<string>(pending ? [pending.key] : []),
  }));
  // 작성 중 입력은 대화창을 닫거나 다른 대화로 옮겨도 남도록 계정·대화별로 보관한다.
  // 저장된 초안(비운 입력 포함)은 입력창을 그대로 따라가므로 가장 최근 입력이다. 초안이 없을 때만 결과 미확인 전송의 내용을 복원한다.
  // 미확인 전송의 key·body는 따로 보관되며, "이전 질문 불러오기"로 다시 꺼내 같은 요청으로 재전송할 수 있다(자동 재전송 없음).
  const [text, setTextState] = useState(() => loadDraft(accountId, session.id) ?? pending?.text ?? "");
  // 늦게 끝난 전송이 그 뒤에 쓴 입력을 지우지 않도록, 지금 화면에 있는지와 최신 입력을 따로 둔다.
  const latestText = useRef(text);
  const mounted = useRef(false);
  const setText = (value: string) => {
    setTextState(value);
    latestText.current = value;
    saveDraft(accountId, session.id, value, pending !== null);
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  // Composer는 브라우저에서만 그려지므로 바로 읽는다(대화창은 ssr:false).
  const [enterSends] = useState(() => window.matchMedia("(any-pointer: fine)").matches);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // 보낸 뒤 응답이 끝나면 입력창으로 포커스를 돌려줄지(정밀 포인터 환경만. 모바일 가상 키보드를 갑자기 띄우지 않는다).
  const refocus = useRef(false);
  const wasAwaiting = useRef(awaiting);
  useEffect(() => {
    const before = wasAwaiting.current;
    wasAwaiting.current = awaiting;
    if (!before || awaiting || !refocus.current) return;
    refocus.current = false;
    // 보내기 버튼·입력창이 잠기며 포커스를 잃은 경우에만 돌려준다(그사이 사용자가 옮긴 포커스는 그대로 둔다).
    const active = document.activeElement;
    if (!active || active === document.body) inputRef.current?.focus({ preventScroll: true });
  }, [awaiting]);

  const unresolved = pageUnresolved(page);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const body = text.trim();
    // inFlight: 같은 순간 두 번 들어온 보내기(Enter 연타·키 반복·두 번 누르기)는 busy가 그려지기 전이라도 한 번만 보낸다.
    if (!body || busy || inFlight.current || awaiting || blocked) return;
    // 공개 글의 범위(postId)를 확정하기 전에는 새 질문을 보내지 않는다. 결과 미확인 재전송은 처음 보낸 범위 그대로라 허용한다.
    if (unresolved && !(pending !== null && pending.text === body)) return;
    if (!csrfToken) {
      setFailure({ kind: "http", status: 401, code: "unauthenticated" });
      return;
    }
    // 결과를 모르는 이전 전송과 같은 내용이면 처음 요청을 그대로 다시 보낸다(같은 key·같은 expectedVersion).
    // 서버는 같은 key의 원래 응답을 돌려주므로 모델 turn이 두 번 시작되지 않는다.
    const isRetry = pending !== null && pending.text === body;
    // 새 질문은 지금 페이지로, 재전송은 처음 보낸 body(page 포함)를 그대로 보낸다.
    const request: PendingSend = isRetry ? pending : { key: uuidV4(), text: body, expectedVersion: session.version, page };
    guard.knownKeys.add(request.key);
    savePendingSend(accountId, session.id, request, guard);
    setPending(request);
    inFlight.current = true;
    setBusy(true);
    refocus.current = window.matchMedia("(pointer: fine)").matches;
    // 보낸 내용은 입력창에서 바로 말풍선으로 옮긴다(#89). 저장된 초안은 결과를 알 때까지 그대로 두고, 실패하면 입력을 되돌린다.
    const typed = text;
    setTextState("");
    latestText.current = "";
    onSending(isRetry ? null : { key: `local-${request.key}`, text: request.text, page: request.page, baseline: session.items.map((i) => i.id) });
    const res = await sendSessionMessage(
      session.id,
      { text: request.text, expectedVersion: request.expectedVersion, ...(request.page && { page: request.page }) },
      { csrfToken, idempotencyKey: request.key }
    );
    // 재전송이면 같은 key에 대한 서버 판단(성공 replay·409)만 이전 전송의 결과를 확정한다.
    // 401/CSRF/Origin/정책 거절은 이전 전송이 처리되지 않았다는 증거가 아니므로 보관을 유지한다.
    const settled = isRetry ? settlesRetry(res.ok ? null : res.failure) : res.ok || !isUncertain(res.failure);
    if (settled) {
      clearPendingSend(accountId, session.id, guard);
      setPending(null);
    }
    if (res.ok) {
      // 보낸 내용 그대로인 초안만 지운다. 화면을 떠난 뒤 다른 입력창이 저장한 초안, 로그아웃 이후(보관소 세대 변경)의
      // 보관소는 건드리지 않는다.
      if (storeGeneration() === guard.generation && loadDraft(accountId, session.id)?.trim() === request.text) {
        clearDraft(accountId, session.id);
      }
      setFailure(null);
    } else {
      // 실패하면 입력을 되돌린다(그사이 새로 쓴 입력이 있으면 그대로 둔다). 이전 기록은 서버에 보존된다.
      if (mounted.current && latestText.current === "") {
        setTextState(typed);
        latestText.current = typed;
      }
      setFailure(res.failure);
      if (res.failure.kind === "http" && (res.failure.status === 401 || res.failure.code === "csrf_invalid")) void refresh();
    }
    await onSent(res.ok ? res.data : null);
    inFlight.current = false;
    setBusy(false);
  }

  // Enter = 보내기, Shift+Enter = 줄바꿈(#89). 한글 등 IME 조합 중의 Enter(isComposing, Safari는 조합 끝 직후 keyCode 229)는
  // 조합 확정용이라 보내지 않는다(마지막 글자 누락·두 번 전송 방지). 확정 뒤의 Enter가 한 번 보낸다.
  // 정밀 포인터가 없는 터치 환경은 화면 키보드의 Enter를 줄바꿈으로 두고 보내기 버튼으로 보낸다.
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    if (!enterSends) return;
    e.preventDefault();
    e.currentTarget.form?.requestSubmit();
  }

  const retrying = pending !== null && pending.text === text.trim();

  // 안내는 본문 흐름에 두고, 입력창과 보내기는 대화창 아래에 붙여 긴 대화에서도 바로 쓸 수 있게 한다.
  return (
    <>
      <div className="mt-auto space-y-2 pt-2">
        <ScopeHint page={page} />
        {unresolved && !retrying && (
          <Notice tone="info">
            이 공개 글을 확인한 뒤에 질문할 수 있습니다(글을 불러오는 중이거나 찾을 수 없는 글). 입력은 그대로 있으며 자동으로 보내지 않습니다.
          </Notice>
        )}
        {failure && <ErrorNotice failure={failure} prefix="메시지를 보내지 못했습니다." />}
        {blocked &&
          (runnerState(codex) ? (
            // 실행 환경 상태: 보내기 버튼만 잠그고 입력은 그대로 쓸 수 있다. 준비가 끝나도 자동으로 보내지 않는다.
            <Notice tone="info">
              {runnerState(codex)?.title} — 환경 준비가 확인되면 보낼 수 있습니다. 입력은 계속 쓰고 고칠 수 있으며, 자동으로 보내지 않습니다.{" "}
              <RunnerActionButton result={codex} />
            </Notice>
          ) : (
            <Notice tone="warn">
              지금은 Codex가 요청을 받을 수 없어 보내기가 잠겨 있습니다(위 연결 상태 참고). 입력한 내용과 이전 대화는 그대로 있습니다.{" "}
              <button type="button" onClick={onRecheck} className="font-medium underline hover:no-underline">
                상태 다시 확인
              </button>
            </Notice>
          ))}
        {/* 결과를 정말 확인하지 못한 경우에만 경고한다. 보내는 동안(결과를 기다리는 중)은 정상 흐름이다(#89). */}
        {pending && !busy && (
          <Notice tone="warn">
            이전 전송의 결과를 확인하지 못했습니다. 위 대화 기록에 메시지가 이미 있는지 확인하세요.{" "}
            {retrying ? (
              "같은 내용으로 다시 보내면 이미 처리된 요청은 중복 실행되지 않습니다."
            ) : (
              <>
                지금 입력은 새 요청으로 처리됩니다. 결과를 모르는 이전 질문을 같은 요청으로 다시 보내려면{" "}
                <button type="button" onClick={() => setText(pending.text)} className="font-medium underline hover:no-underline">
                  이전 질문 불러오기
                </button>
                를 누른 뒤 보내세요.
              </>
            )}
            {retrying && !sameDescriptor(pending.page, page) &&
              ` 다시 보내기는 지금 페이지가 아니라 처음 보낸 화면(${kindText(pending.page?.kind ?? "general").label}) 기준으로 처리됩니다.`}
          </Notice>
        )}
      </div>
      <form onSubmit={submit} className="sticky bottom-0 z-10 -mx-4 space-y-2 border-t border-stone-100 bg-white px-4 pb-3 pt-2">
        {!atBottom && (
          // 위로 스크롤해 읽는 중이면 따라가지 않고 이 버튼으로 최신 내용으로 돌아간다.
          <button
            type="button"
            onClick={onJump}
            className="absolute bottom-full left-1/2 mb-2 flex min-h-9 -translate-x-1/2 items-center gap-1 rounded-full bg-white px-3 text-xs font-medium text-stone-700 shadow-md ring-1 ring-stone-200 hover:bg-stone-50"
          >
            <svg aria-hidden="true" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14m-6-6 6 6 6-6" />
            </svg>
            최신으로
          </button>
        )}
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={3}
          disabled={awaiting}
          placeholder={
            awaiting ? "Codex가 응답하는 중입니다..." : enterSends ? "이어서 질문하기 (Enter 보내기 · Shift+Enter 줄바꿈)" : "이어서 질문하기"
          }
          aria-label="메시지"
          className="block max-h-[40vh] w-full resize-y rounded-lg border border-stone-200 px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 disabled:bg-stone-50"
        />
        {stop?.note && (
          <p className="text-xs text-amber-800" role="status">
            {stop.note}
          </p>
        )}
        <div className="flex items-center gap-2">
          <NextScope page={page} />
          {stop && (
            <button
              type="button"
              onClick={stop.onStop}
              disabled={stop.disabled}
              className="min-h-11 shrink-0 rounded-lg border border-stone-300 bg-white px-4 text-sm font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-50 md:min-h-9"
            >
              {stop.label}
            </button>
          )}
          <button
            type="submit"
            disabled={busy || awaiting || blocked || text.trim() === "" || (unresolved && !retrying)}
            className="min-h-11 shrink-0 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 md:min-h-9"
          >
            {busy ? "보내는 중..." : retrying ? "다시 보내기" : "보내기"}
          </button>
        </div>
      </form>
    </>
  );
}

/** 대화 하나. 공통 대화창이 그리며 상세 조회·진행 중 확인·Codex 상태 확인은 여기 한 곳에서만 한다. */
function subscribeVisibility(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

interface StopState {
  requestId: string;
  // sending: 요청 중 / requested: 중지가 걸렸거나 확정 신호를 기다림 / awaitStart: runner가 아직 턴을 받기 전이라 시작되면 한 번 더 /
  // failed: 확인하지 못함(다시 누를 수 있음)
  phase: "sending" | "requested" | "awaitStart" | "failed";
  resent: boolean;
}

export default function SessionView({ id, onArchived }: { id: string; onArchived: (id: string) => void }) {
  const { user, csrfToken, refresh: refreshAuth } = useAuth();
  const { result, refreshFailure, reload, reloadResult } = useLoad(`session:${id}`, () => getSession(id));
  const { run, busy } = useMutation();
  const [archiveFailure, setArchiveFailure] = useState<FetchFailure | null>(null);
  const running = result?.ok === true && result.data.state === "running";
  const chat = useChat();
  const { push: pushStatus } = useCodexRunner();
  const visible = useSyncExternalStore(subscribeVisibility, () => document.visibilityState === "visible", () => true);

  // #89 보내는 중(접수 확인 전) 상태. #90 중지 상태와 마지막 접수 requestId(202 응답).
  const [send, setSend] = useState<{ local: LocalSend | null } | null>(null);
  const [stopping, setStopping] = useState<StopState | null>(null);
  const [stopUnsupported, setStopUnsupported] = useState(false);
  const [lastAccepted, setLastAccepted] = useState<string | null>(null);

  // 화면 밖(언마운트) 뒤의 늦은 결과는 반영하지 않는다.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // #90 중지 요청. 결과는 스트림의 확정 신호(turn.completed{interrupted})나 기록으로 확인한다. 자동으로 다시 보내지 않는다
  // (단, runner가 아직 턴을 받기 전이라 not_running이면 턴이 시작될 때 같은 requestId로 한 번만 더 보낸다 — 계약 §2).
  const requestStop = async (requestId: string, resent: boolean) => {
    if (!csrfToken) return;
    setStopping({ requestId, phase: "sending", resent });
    const res = await interruptSession(id, requestId, csrfToken);
    if (!alive.current) return;
    if (res.ok) {
      setStopping({ requestId, phase: res.data.state === "not_running" && !resent ? "awaitStart" : "requested", resent });
      return;
    }
    const f = res.failure;
    if (f.kind === "http" && f.status === 409) {
      // 서버 기록에서 그 턴이 이미 끝났다. 기록으로 확정한다.
      setStopping(null);
      void reload();
      return;
    }
    if (f.kind === "http" && f.status === 404) {
      setStopping(null);
      setStopUnsupported(true);
      return;
    }
    if (f.kind === "http" && (f.status === 401 || f.code === "csrf_invalid")) void refreshAuth();
    setStopping({ requestId, phase: "failed", resent });
  };

  // #90 실시간 스트림. 연결된 동안은 대화 상세·Codex 상태 polling을 멈추고, 확정 신호마다 기록을 한 번 다시 불러온다.
  const stream = useChatStream(id, chat.open && visible, {
    onRefresh: () => void reload(),
    onRunner: (raw) => {
      const status = readCodexStatus(raw);
      if (status) pushStatus(status);
    },
    // 기록은 실행 중인데 스트림이 진행 중인 턴이 없다고 하면(연결 전에 끝남) 기록으로 확인한다.
    onIdle: () => {
      if (running && send === null) void reload();
    },
    onTurnRunning: (requestId) => {
      if (stopping?.phase === "awaitStart" && stopping.requestId === requestId && !stopping.resent) void requestStop(requestId, true);
    },
  });
  const streamLive = stream.live;
  // 열린 대화창에서만 일시 상태(환경 활성화 중·사용 대기·연결 확인)를 다시 조회한다. 스트림이 연결된 동안은 runner 이벤트가 대신한다.
  const codex = useCodexStatus(chat.open && !streamLive);
  const reloadCodex = codex.reload;
  const pathname = usePathname();
  const search = useSearchParams();
  const page = describePage(pathname, search, useRegisteredPage());

  // turn이 끝날 때마다(성공·실패) 연결 상태를 다시 확인한다. 실행 중 인증 만료·사용 한도·모델 차단이
  // 생겼으면 안내와 보내기 잠금에 바로 반영되고, 첫 실제 응답 뒤의 확인 상태도 갱신된다.
  const loaded = result?.ok === true;
  const settledVersion = result?.ok === true && result.data.state !== "running" ? result.data.version : null;
  const seenSettled = useRef<{ version: number | null } | null>(null);
  useEffect(() => {
    if (!loaded) return;
    const prev = seenSettled.current;
    seenSettled.current = { version: settledVersion };
    // 처음 불러온 대화의 상태는 useCodexStatus가 이미 조회 중이므로 다시 묻지 않는다. 스트림이 연결돼 있으면 runner 이벤트로 받는다.
    if (prev !== null && settledVersion !== null && settledVersion !== prev.version && !streamLive) void reloadCodex();
  }, [loaded, settledVersion, reloadCodex, streamLive]);

  // 서버가 알려 준 접수 종료 시각이 지나면 상태를 다시 확인한다. 잠금 판정은 브라우저 시계가 아니라 서버 응답으로 한다.
  // 브라우저 시계가 서버보다 빨라 일찍 물었거나 조회가 일시 실패해 여전히 이용 가능으로 남아 있으면,
  // 서버가 접수 불가(또는 새 기한)를 알려 줄 때까지 간격을 늘려 가며 다시 묻는다. 상태 조회만 하고 전송은 하지 않는다.
  const expiresAt = codex.result?.ok === true && codex.result.data.available ? codex.result.data.accessExpiresAt : undefined;
  useEffect(() => {
    if (!expiresAt) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retry = EXPIRY_RECHECK_MIN_MS;
    const schedule = (delay: number) => {
      timer = setTimeout(async () => {
        await reloadCodex();
        if (cancelled) return;
        schedule(retry);
        retry = Math.min(retry * 2, EXPIRY_RECHECK_MAX_MS);
      }, delay);
    };
    schedule(Math.min(Math.max(Date.parse(expiresAt) - Date.now(), 0) + 1000, 2_147_483_647));
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [expiresAt, reloadCodex]);

  // 진행 중인 turn은 상세를 다시 조회해 확인한다. 재접속해도 같은 방식으로 이어진다. 스트림이 연결된 동안은 멈춘다(#90).
  useEffect(() => {
    if (!running || streamLive) return;
    const timer = setTimeout(() => void reload(), POLL_MS);
    return () => clearTimeout(timer);
    // 일시적 조회 실패(refreshFailure)에도 마지막 기록을 유지한 채 계속 확인한다.
  }, [running, streamLive, result, refreshFailure, reload]);

  // 이 화면에서 실행 중을 본 지 30초가 지나면 중립 안내를 보인다. 경과 시간은 실패·유실의 근거가 아니며 재전송을 열지 않는다.
  const [slowFor, setSlowFor] = useState<string | null>(null);
  if (!running && slowFor !== null) setSlowFor(null);
  useEffect(() => {
    if (!running) return;
    const timer = setTimeout(() => setSlowFor(id), SLOW_RUNNING_MS);
    return () => clearTimeout(timer);
  }, [running, id]);
  const slow = running && slowFor === id;

  // #90 스트림으로 받는 턴. 진행 중이거나, 끝났어도 기록이 아직 그 턴을 실행 중으로 보일 때(완료 뒤 GET 전)만 보인다.
  // 기록이 실행 중인 다른 턴(pending-<requestId>)을 가리키면 이전 턴의 스트림은 보이지 않는다.
  const items = result?.ok === true ? result.data.items : null;
  const recordRequestId = items?.find(isPendingInput)?.id.replace(/^pending-/, "") ?? null;
  const liveTurn = stream.turn;
  const showLive = liveTurn !== null && (liveTurn.running || (running && (recordRequestId === null || recordRequestId === liveTurn.requestId)));

  // #89 화면 상태: 보내는 중(submitted) → 서버 실행 중(waiting, "생각 중") → #90 받는 중(streaming) → 완료(ready) / 오류(error).
  // 한 번에 하나다. 보내기 요청이 끝나도 다음 조회가 끝날 때까지 submitted로 둔다(입력창이 잠깐 열렸다 닫히지 않게).
  // 스트림이 턴 끝(turn.completed)을 알렸지만 기록 GET이 아직이면 받는 중으로 둔다("생각 중"이 잠깐 다시 보이지 않게).
  const status = chatStatus(send !== null, result?.ok === true ? result.data.state : "idle", showLive && (liveTurn.running || liveTurn.outcome !== null));
  const awaiting = awaitingReply(status);
  if (stopping && !awaiting) setStopping(null);

  // 서버 기록이 바뀌어도(보낸 말풍선 → 접수 항목 → 기록 항목) 같은 말풍선으로 잇는다. 화면에 있는 동안의 key 별칭을 둔다.
  const local = send?.local ?? null;
  const [view, setView] = useState<{ items: SessionItem[] | null; local: LocalSend | null; continuity: Continuity; entries: TranscriptEntry[] }>(
    { items: null, local: null, continuity: EMPTY_CONTINUITY, entries: [] }
  );
  let entries = view.entries;
  if (items && (view.items !== items || view.local !== local)) {
    const next = reconcile(view.continuity, items, local);
    entries = next.entries;
    setView({ items, local, continuity: next.next, entries });
  }

  // #90 스트림 항목: 기록에 같은 id가 이미 있으면 기록 항목을 쓴다(같은 key라 remount 없음). 빈 답변 조각은 "생각 중"으로 둔다.
  const recordIds = new Set(items?.map((i) => i.id) ?? []);
  const liveItems = showLive ? liveTurn.items.filter((i) => !recordIds.has(i.itemId) && !(i.kind === "agentMessage" && i.text === "")) : [];
  const lastLive = liveItems.at(-1);
  // "생각 중"은 답변 조각을 받는 동안과, 끝난 턴의 기록을 기다리는 동안에는 보이지 않는다.
  const answering = showLive && (liveTurn.outcome !== null || (!!lastLive && lastLive.kind === "agentMessage" && lastLive.status !== "completed"));

  // #90 중지 대상: 스트림의 진행 중 턴 → 기록의 실행 중 턴 → 막 접수한 턴(202 응답).
  const stopRequestId =
    (showLive && liveTurn.running ? liveTurn.requestId : null) ?? (running ? recordRequestId : null) ?? (send !== null ? lastAccepted : null);
  const stop: StopControl | null =
    stream.supported && !stopUnsupported && awaiting && stopRequestId && !(showLive && liveTurn.outcome)
      ? {
          label: stopping?.phase === "sending" ? "중지 요청 중..." : stopping && stopping.phase !== "failed" ? "중지하는 중..." : "중지",
          disabled: !!stopping && stopping.phase !== "failed",
          note: stopping?.phase === "failed" ? "중지를 확인하지 못했습니다. 응답은 계속 진행 중일 수 있습니다. 다시 눌러 주세요." : null,
          onStop: () => void requestStop(stopRequestId, false),
        }
      : null;

  // 자동 스크롤: 바닥에 있을 때만 따라간다. 위로 올려 읽는 중이면 "최신으로" 버튼을 보인다.
  const contentRef = useRef<HTMLDivElement>(null);
  // 완료 때 "생각 중" 하나가 답변 하나로 바뀌면 항목 수는 같으므로, 마지막 항목과 대기 여부도 함께 본다(#89 review).
  const scrollSignal = `${entries.length}|${entries.at(-1)?.key ?? ""}|${awaiting ? 1 : 0}|${liveItems.length}|${lastLive?.itemId ?? ""}`;
  const { atBottom, stick } = useStickToBottom(contentRef, { enabled: loaded, live: awaiting, signal: scrollSignal });

  // 접수(202)된 전송은 대화 조회가 그 버전 이상을 보여 줄 때까지 보내는 중으로 둔다. 조회가 일시 실패(네트워크·5xx)하면
  // 이전 idle 기록이 남아 polling도 시작되지 않으므로, 조회만 늘려 가는 간격으로 다시 한다(POST는 다시 보내지 않는다).
  // 확정 실패(401·403·404 등)는 useLoad가 그대로 보이므로 멈춘다. 화면을 떠나면 멈춘다.
  const confirmAccepted = async (version: number) => {
    for (let attempt = 0; alive.current; attempt++) {
      const r = await reloadResult();
      if (r && (r.ok ? r.data.version >= version : !isUncertain(r.failure))) return;
      await new Promise((resolve) => setTimeout(resolve, Math.min(CONFIRM_RETRY_MS * 2 ** attempt, CONFIRM_RETRY_MAX_MS)));
    }
  };

  const [announced, setAnnounced] = useState<{ status: ChatStatus; text: string }>({ status, text: "" });
  if (announced.status !== status) setAnnounced({ status, text: announcement(announced.status, status, announced.text) });

  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  // 개인 세션 조회 실패. 공개 문서 조회와는 별개다.
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="대화를 불러오지 못했습니다." />;
  const s = result.data;

  async function archive() {
    if (!window.confirm("이 대화를 보관할까요? 목록에서 숨겨지지만 기록은 보존됩니다.")) return;
    const res = await run(`session:archive:${s.id}:${s.version}`, (ctx) => archiveSession(s.id, s.version, ctx));
    if (res.ok) onArchived(s.id);
    else setArchiveFailure(res.failure);
  }

  return (
    <div ref={contentRef} className="flex flex-1 flex-col gap-3">
      <p className="sr-only" role="status" aria-live="polite">
        {announced.text}
      </p>
      <header className="flex items-start gap-2">
        <div className="min-w-0 flex-1 space-y-0.5">
          <h3 className="break-words text-base font-semibold text-stone-900">{s.title || "(제목 없음)"}</h3>
          <p className="text-xs text-stone-500">
            {SESSION_STATE_LABELS[s.state]} · 시작 {formatDateTime(s.createdAt)} · 최근 {formatDateTime(s.updatedAt)}
          </p>
          {s.context && (
            <p className="text-xs text-stone-500">
              공개 글에서 시작:{" "}
              <Link href={`/public/document/?id=${encodeURIComponent(s.context.documentId)}`} className="text-indigo-600 hover:text-indigo-800">
                {s.context.title || "(제목 없음)"}
              </Link>{" "}
              — 질문마다 읽을 수 있는 자료는 보낼 때 보고 있는 페이지 기준입니다. 이 글을 다시 읽으며 질문하려면 그 공개 글 페이지에서 보내세요.
            </p>
          )}
        </div>
        <button
          onClick={() => void archive()}
          disabled={busy || awaiting}
          className="min-h-11 shrink-0 rounded-md px-2 text-xs text-stone-500 hover:bg-stone-100 hover:text-red-700 disabled:opacity-50 md:min-h-8"
        >
          보관
        </button>
      </header>
      {archiveFailure && <ErrorNotice failure={archiveFailure} prefix="보관하지 못했습니다." />}

      <CodexStatusView result={codex.result} />
      {codex.refreshFailure && (
        <ErrorNotice failure={codex.refreshFailure} prefix="Codex 상태를 다시 확인하지 못했습니다. 마지막으로 받은 상태를 표시합니다." />
      )}

      {entries.length === 0 && !awaiting ? (
        <p className="text-sm text-stone-500">아직 주고받은 메시지가 없습니다.</p>
      ) : (
        <ol className="space-y-4">
          {/* 기록 항목·스트림 항목·"생각 중"은 한 목록(같은 key 공간)이어야 스트림 항목이 기록 항목으로 바뀔 때 remount되지 않는다. */}
          {[
            ...entries.map((entry) =>
              entry.kind === "local" ? (
                <Message
                  key={entry.key}
                  sessionId={s.id}
                  item={{ id: entry.key, type: "message", role: "user", text: entry.text }}
                  sending={{ page: entry.page }}
                />
              ) : entry.item.type === "message" ? (
                <Message key={entry.key} sessionId={s.id} item={entry.item} />
              ) : entry.item.type === "tool_call" ? (
                <ToolCall key={entry.key} sessionId={s.id} item={entry.item} />
              ) : entry.item.type === "notice" ? (
                <NoticeRow key={entry.key} item={entry.item} />
              ) : (
                <UnknownRow key={entry.key} item={entry.item} />
              )
            ),
            ...liveItems.map((item) =>
              item.kind === "agentMessage" ? (
                <Message key={item.itemId} sessionId={s.id} item={{ id: item.itemId, type: "message", role: "assistant", text: item.text }} live />
              ) : (
                <ToolCall key={item.itemId} sessionId={s.id} item={liveToolItem(item)} live />
              )
            ),
            showLive && liveTurn.stopped ? (
              <li key="stream-stopped" className="text-xs text-stone-500">
                실시간 표시를 멈췄습니다. 답변이 끝나면 전체 기록으로 보입니다.
              </li>
            ) : null,
            awaiting && !answering ? <Thinking key="thinking" slow={slow} accepted={status !== "submitted"} /> : null,
          ]}
        </ol>
      )}

      {refreshFailure && (
        <ErrorNotice failure={refreshFailure} prefix="최신 대화 상태를 불러오지 못했습니다. 마지막으로 받은 기록을 표시합니다." />
      )}
      {s.state === "failed" && !awaiting && (
        <Notice tone="error">
          마지막 요청이 실패했습니다.{" "}
          {/* 서버 오류 문구를 그대로 옮기지 않는다(외부 provider 원문·식별자 노출 방지). 아는 코드만 안내하고 나머지는 코드만 표시. */}
          {s.error &&
            (researchErrorMessage({ kind: "http", status: 0, code: s.error.code }) ??
              `Codex 요청을 처리하지 못했습니다 (${s.error.code}). 다른 모델로 자동 전환하거나 자동으로 다시 실행하지 않습니다.`)}{" "}
          이전 기록은 보존되어 있으며 새 메시지로 이어서 질문할 수 있습니다. 실패한 요청은 자동으로 다시 실행되지 않습니다.
        </Notice>
      )}

      {user && (
        <Composer
          key={user.accountId}
          session={s}
          accountId={user.accountId}
          page={page}
          blocked={codexBlocked(codex.result)}
          codex={codex.result}
          awaiting={awaiting}
          atBottom={atBottom}
          stop={stop}
          onRecheck={() => void reloadCodex()}
          onSending={(next) => {
            setSend({ local: next });
            // 보내면 바닥으로 가서 새 말풍선과 "생각 중"을 보인다.
            stick();
          }}
          onSent={async (accepted) => {
            // 보내지 못했으면 먼저 그린 말풍선을 바로 거둔다(입력은 Composer가 되돌린다). 입력 잠금은 다시 불러올 때까지 둔다.
            if (!accepted) setSend({ local: null });
            if (accepted?.requestId) setLastAccepted(accepted.requestId);
            // 실패면 거절 사유(만료·한도·차단·환경 꺼짐)가, 성공이어도 runner 상태(idle 정지 등)가 바뀌었을 수 있다.
            // 스트림이 연결돼 있으면 runner 이벤트로 받으므로 조회하지 않는다.
            if (!streamLive) void reloadCodex();
            if (accepted) await confirmAccepted(accepted.version);
            else await reload();
            setSend(null);
          }}
          onJump={stick}
        />
      )}
    </div>
  );
}
