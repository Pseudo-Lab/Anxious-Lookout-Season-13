"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { archiveSession, getSession, getSessionItemRaw, researchErrorMessage, sendSessionMessage } from "@/lib/research/api";
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
import type { AcceptedScope, PageDescriptor, SessionDetail, SessionItem, ToolCallItem } from "@/lib/research/types";
import { describePage, kindText, sameDescriptor, scopeLabel } from "@/lib/research/pageContext";
import { own } from "@/lib/research/own";
import { formatDateTime, toDisplayText } from "@/lib/research/format";
import {
  CodexStatusView,
  RunnerActionButton,
  codexBlocked,
  runnerState,
  useCodexStatus,
} from "@/components/research/CodexStatusBanner";
import { useChat } from "@/components/chat/ChatProvider";
import type { FetchResult } from "@/lib/api/client";
import type { CodexStatus } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import { SESSION_STATE_LABELS } from "@/components/research/ItemLists";

const POLL_MS = 2000;
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
      <p className="text-xs font-medium text-stone-600">{label}</p>
      <Inert value={result.data.raw} />
    </div>
  );
}

const TOOL_STATUS_LABELS: Record<string, string> = { rejected: "거부됨" };

function ToolCall({ sessionId, item }: { sessionId: string; item: ToolCallItem }) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState(false);
  return (
    <li className="rounded-md bg-stone-50 text-xs">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left hover:bg-stone-100"
      >
        <span className="min-w-0 break-words">
          <span className="mr-2 text-xs text-stone-500">도구</span>
          <span className="font-mono text-stone-800">{item.name}</span>
        </span>
        <span className={`text-xs ${item.status === "rejected" ? "font-medium text-amber-700" : "text-stone-500"}`}>
          {own(TOOL_STATUS_LABELS, item.status) ?? item.status}
        </span>
      </button>
      {item.status === "rejected" && (
        <p className="px-3 pb-2 text-xs text-amber-800">
          {item.error?.code === "tool_not_allowed"
            ? "이 질문을 보낸 범위(공개 글 읽기 등)에서 허용되지 않는 도구라 서버가 실행하지 않았습니다."
            : "서버가 이 도구 호출을 실행하지 않았습니다."}
          {item.error && ` (${item.error.code})`}
        </p>
      )}
      {item.modelMismatch && (
        <div className="px-3 pb-2">
          <ModelMismatchNote />
        </div>
      )}
      {open && (
        <div className="space-y-3 px-3 pb-3">
          <div className="space-y-1">
            <p className="text-xs font-medium text-stone-600">입력</p>
            <Inert value={item.input} />
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-stone-600">출력</p>
            <Inert value={item.output} />
          </div>
          {raw ? (
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

const PLATFORM_STATUS_LABELS = { pending: "전달 확인 중", not_recorded: "미기록 입력" } as const;

// 고정 모델 확인 실패로 중단된 실행의 항목 표시(다른 모델명·정책 사유는 표시하지 않는다).
function ModelMismatchNote() {
  return (
    <p className="mt-1 rounded bg-red-50 px-2 py-1 text-xs text-red-700">
      <span className="font-semibold">정상 응답 아님</span> — 고정 모델을 확인하지 못해 중단된 실행의 기록입니다. 이 실행의 결과는
      사용하지 마세요.
    </p>
  );
}

// 서버가 접수한 질문의 화면 문맥(공개 글이면 읽기 범위). 보낸 뒤 페이지를 옮겨도 바뀌지 않는다.
function ScopeBadge({ scope }: { scope: AcceptedScope }) {
  return (
    <span
      className="max-w-full break-words rounded bg-white/80 px-1.5 py-0.5 font-normal text-stone-500 ring-1 ring-stone-200"
      title={kindText(scope.page.kind).can}
    >
      {scopeLabel(scope.page.kind, "sent")}
      {scope.page.title ? ` · ${scope.page.title}` : ""}
    </span>
  );
}

function Message({ sessionId, item }: { sessionId: string; item: Extract<SessionItem, { type: "message" }> }) {
  const [raw, setRaw] = useState(false);
  const mine = item.role === "user";
  // 플랫폼이 보존한 입력: Codex 원본 이력에 기록되지 않았으므로 모델이 본 대화나 성공으로 표시하지 않는다.
  const platform = item.source === "platform";
  return (
    // 내 질문은 오른쪽의 옅은 말풍선, Codex 답변은 테두리 없이 본문처럼 보여 답변을 중심에 둔다.
    <li
      data-role={item.role}
      className={`min-w-0 ${mine ? "ml-auto w-fit max-w-[90%] rounded-2xl bg-stone-100 px-3 py-2" : "py-1"} ${
        platform ? "border border-dashed border-amber-400" : ""
      }`}
    >
      <p className="mb-1 flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-stone-400">
        {mine ? "나" : "Codex"}
        {mine && item.scope && <ScopeBadge scope={item.scope} />}
        {platform && item.status && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">{PLATFORM_STATUS_LABELS[item.status]}</span>
        )}
      </p>
      {mine ? (
        <p className="whitespace-pre-wrap text-sm text-stone-800 [overflow-wrap:anywhere]">{item.text}</p>
      ) : (
        <SafeMarkdown content={item.text} compact />
      )}
      {item.modelMismatch && <ModelMismatchNote />}
      {platform && (
        <p className="mt-1 text-xs text-amber-800">
          {item.status === "pending"
            ? "Codex 대화 기록에 반영되었는지 아직 확인되지 않았습니다."
            : "Codex 대화 기록에 반영되지 않은 입력입니다. 내 대화에는 보존되며, 필요하면 새 메시지로 다시 보내세요."}
        </p>
      )}
      <div className="mt-1">
        {raw ? (
          <RawItem sessionId={sessionId} itemId={item.id} label={platform ? "보존된 입력 기록 (Codex 원본 아님)" : undefined} />
        ) : (
          <button onClick={() => setRaw(true)} className="text-[11px] text-stone-400 hover:text-stone-700">
            {platform ? "보존된 입력 기록" : "원본 기록"}
          </button>
        )}
      </div>
    </li>
  );
}

// 다음 질문과 함께 보낼 화면 문맥. 같은 대화의 이전 내용은 그대로 이어진다. #14부터 소유자 화면은 참고일 뿐 권한을 바꾸지 않고,
// 공개 글만 읽기 범위로 제한된다. 이름은 입력창 옆(NextScope), 설명은 입력창 바로 위 본문 흐름(ScopeHint)에 둔다.
function NextScope({ page }: { page: PageDescriptor }) {
  const text = kindText(page.kind);
  return (
    <p className="min-w-0 flex-1 truncate text-xs text-stone-500" title={text.can} aria-live="polite">
      <span className="font-medium text-stone-700">{scopeLabel(page.kind, "next")}</span>
    </p>
  );
}

function ScopeHint({ page }: { page: PageDescriptor }) {
  return (
    <p className="text-xs leading-relaxed text-stone-400">
      {kindText(page.kind).can} 이전 대화 내용은 이 대화에 계속 이어집니다.
    </p>
  );
}

function Composer({
  session,
  accountId,
  page,
  blocked,
  codex,
  onRecheck,
  onSent,
}: {
  session: SessionDetail;
  accountId: string;
  // 지금 보고 있는 페이지. 보내기를 누른 순간의 값이 그 요청에 고정된다.
  page: PageDescriptor;
  // 서버가 지금 접수할 수 없다고 확인한 상태. 보내기만 잠그고 입력·이전 대화는 그대로 둔다.
  blocked: boolean;
  // 잠금 사유 안내용 상태(실행 환경 상태면 준비 버튼을 함께 보인다).
  codex: FetchResult<CodexStatus> | null;
  onRecheck: () => void;
  onSent: (ok: boolean) => void;
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
  const running = session.state === "running";

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body = text.trim();
    if (!body || busy || blocked) return;
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
    setBusy(true);
    const res = await sendSessionMessage(
      session.id,
      { text: request.text, expectedVersion: request.expectedVersion, ...(request.page && { page: request.page }) },
      { csrfToken, idempotencyKey: request.key }
    );
    setBusy(false);
    // 재전송이면 같은 key에 대한 서버 판단(성공 replay·409)만 이전 전송의 결과를 확정한다.
    // 401/CSRF/Origin/정책 거절은 이전 전송이 처리되지 않았다는 증거가 아니므로 보관을 유지한다.
    const settled = isRetry ? settlesRetry(res.ok ? null : res.failure) : res.ok || !isUncertain(res.failure);
    if (settled) {
      clearPendingSend(accountId, session.id, guard);
      setPending(null);
    }
    if (res.ok) {
      // 보낸 내용 그대로인 입력만 비운다. 응답을 기다리는 동안 새로 쓴 입력, 화면을 떠난 뒤 다른 입력창이
      // 저장한 초안, 로그아웃 이후(보관소 세대 변경)의 보관소는 건드리지 않는다.
      if (mounted.current) {
        if (latestText.current.trim() === request.text) {
          setTextState("");
          latestText.current = "";
          clearDraft(accountId, session.id);
        }
      } else if (storeGeneration() === guard.generation && loadDraft(accountId, session.id)?.trim() === request.text) {
        clearDraft(accountId, session.id);
      }
      setFailure(null);
    } else {
      // 실패해도 입력은 남겨 둔다. 이전 기록은 서버에 보존된다.
      setFailure(res.failure);
      if (res.failure.kind === "http" && (res.failure.status === 401 || res.failure.code === "csrf_invalid")) void refresh();
    }
    onSent(res.ok);
  }

  const retrying = pending !== null && pending.text === text.trim();

  // 안내는 본문 흐름에 두고, 입력창과 보내기는 대화창 아래에 붙여 긴 대화에서도 바로 쓸 수 있게 한다.
  return (
    <>
      <div className="mt-auto space-y-2 pt-2">
        <ScopeHint page={page} />
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
        {pending && (
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
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          disabled={running}
          placeholder={running ? "Codex가 응답하는 중입니다..." : "이어서 질문하기"}
          aria-label="메시지"
          className="block max-h-[40vh] w-full resize-y rounded-lg border border-stone-200 px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 disabled:bg-stone-50"
        />
        <div className="flex items-center gap-2">
          <NextScope page={page} />
          <button
            type="submit"
            disabled={busy || running || blocked || text.trim() === ""}
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
export default function SessionView({ id, onArchived }: { id: string; onArchived: (id: string) => void }) {
  const { user } = useAuth();
  const { result, refreshFailure, reload } = useLoad(`session:${id}`, () => getSession(id));
  const { run, busy } = useMutation();
  const [archiveFailure, setArchiveFailure] = useState<FetchFailure | null>(null);
  const running = result?.ok === true && result.data.state === "running";
  const chat = useChat();
  // 열린 대화창에서만 일시 상태(환경 활성화 중·사용 대기·연결 확인)를 다시 조회한다.
  const codex = useCodexStatus(chat.open);
  const reloadCodex = codex.reload;
  const pathname = usePathname();
  const search = useSearchParams();
  const page = describePage(pathname, search);

  // turn이 끝날 때마다(성공·실패) 연결 상태를 다시 확인한다. 실행 중 인증 만료·사용 한도·모델 차단이
  // 생겼으면 안내와 보내기 잠금에 바로 반영되고, 첫 실제 응답 뒤의 확인 상태도 갱신된다.
  const loaded = result?.ok === true;
  const settledVersion = result?.ok === true && result.data.state !== "running" ? result.data.version : null;
  const seenSettled = useRef<{ version: number | null } | null>(null);
  useEffect(() => {
    if (!loaded) return;
    const prev = seenSettled.current;
    seenSettled.current = { version: settledVersion };
    // 처음 불러온 대화의 상태는 useCodexStatus가 이미 조회 중이므로 다시 묻지 않는다.
    if (prev !== null && settledVersion !== null && settledVersion !== prev.version) void reloadCodex();
  }, [loaded, settledVersion, reloadCodex]);

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

  // 진행 중인 turn은 상세를 다시 조회해 확인한다. 재접속해도 같은 방식으로 이어진다.
  useEffect(() => {
    if (!running) return;
    const timer = setTimeout(() => void reload(), POLL_MS);
    return () => clearTimeout(timer);
    // 일시적 조회 실패(refreshFailure)에도 마지막 기록을 유지한 채 계속 확인한다.
  }, [running, result, refreshFailure, reload]);

  // 이 화면에서 실행 중을 본 지 30초가 지나면 중립 안내를 보인다. 경과 시간은 실패·유실의 근거가 아니며 재전송을 열지 않는다.
  const [slowFor, setSlowFor] = useState<string | null>(null);
  if (!running && slowFor !== null) setSlowFor(null);
  useEffect(() => {
    if (!running) return;
    const timer = setTimeout(() => setSlowFor(id), SLOW_RUNNING_MS);
    return () => clearTimeout(timer);
  }, [running, id]);
  const slow = running && slowFor === id;

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
    <div className="flex flex-1 flex-col gap-3">
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
          disabled={busy || running}
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

      {s.items.length === 0 ? (
        <p className="text-sm text-stone-500">아직 주고받은 메시지가 없습니다.</p>
      ) : (
        <ol className="space-y-4">
          {s.items.map((item) =>
            item.type === "message" ? (
              <Message key={item.id} sessionId={s.id} item={item} />
            ) : (
              <ToolCall key={item.id} sessionId={s.id} item={item} />
            )
          )}
        </ol>
      )}

      {refreshFailure && (
        <ErrorNotice failure={refreshFailure} prefix="최신 대화 상태를 불러오지 못했습니다. 마지막으로 받은 기록을 표시합니다." />
      )}
      {running && (
        <Notice tone="info">
          {slow
            ? "응답을 기다리거나 실행 결과를 확인 중입니다. 오래 걸려도 자동으로 다시 보내지 않습니다."
            : "Codex가 응답하는 중입니다."}{" "}
          페이지를 닫았다가 다시 열어도 이어서 확인할 수 있습니다.
        </Notice>
      )}
      {s.state === "failed" && (
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
          onRecheck={() => void reloadCodex()}
          onSent={(ok) => {
            void reload();
            // 거절 사유(만료·한도·차단)가 생겼을 수 있으므로 상태도 다시 확인한다.
            if (!ok) void reloadCodex();
          }}
        />
      )}
    </div>
  );
}
