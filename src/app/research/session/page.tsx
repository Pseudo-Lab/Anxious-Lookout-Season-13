"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
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
import { useAuth } from "@/hooks/useAuth";
import type { SessionDetail, SessionItem } from "@/lib/research/types";
import { formatDateTime, toDisplayText } from "@/lib/research/format";
import AuthGate from "@/components/research/AuthGate";
import CodexStatusBanner from "@/components/research/CodexStatusBanner";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import SafeMarkdown from "@/components/research/SafeMarkdown";
import { SESSION_STATE_LABELS } from "@/components/research/ItemLists";

const POLL_MS = 2000;

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

function ToolCall({ sessionId, item }: { sessionId: string; item: Extract<SessionItem, { type: "tool_call" }> }) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState(false);
  return (
    <li className="rounded-lg bg-stone-50 ring-1 ring-stone-200">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm"
      >
        <span>
          <span className="mr-2 text-xs text-stone-500">도구</span>
          <span className="font-mono text-stone-800">{item.name}</span>
        </span>
        <span className="text-xs text-stone-500">{item.status}</span>
      </button>
      {open && (
        <div className="space-y-3 px-4 pb-4">
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

function Message({ sessionId, item }: { sessionId: string; item: Extract<SessionItem, { type: "message" }> }) {
  const [raw, setRaw] = useState(false);
  const mine = item.role === "user";
  // 플랫폼이 보존한 입력: Codex 원본 이력에 기록되지 않았으므로 모델이 본 대화나 성공으로 표시하지 않는다.
  const platform = item.source === "platform";
  return (
    <li
      className={`rounded-lg px-4 py-3 ${mine ? "bg-indigo-50" : "bg-white ring-1 ring-stone-200"} ${
        platform ? "border border-dashed border-amber-400" : ""
      }`}
    >
      <p className="mb-1 flex flex-wrap items-center gap-2 text-xs font-medium text-stone-500">
        {mine ? "나" : "Codex"}
        {platform && item.status && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">{PLATFORM_STATUS_LABELS[item.status]}</span>
        )}
      </p>
      {mine ? <p className="whitespace-pre-wrap text-sm text-stone-800">{item.text}</p> : <SafeMarkdown content={item.text} />}
      {platform && (
        <p className="mt-1 text-xs text-amber-800">
          {item.status === "pending"
            ? "Codex 대화 기록에 반영되었는지 아직 확인되지 않았습니다."
            : "Codex 대화 기록에 반영되지 않은 입력입니다. 내 대화에는 보존되며, 필요하면 새 메시지로 다시 보내세요."}
        </p>
      )}
      <div className="mt-2">
        {raw ? (
          <RawItem sessionId={sessionId} itemId={item.id} label={platform ? "보존된 입력 기록 (Codex 원본 아님)" : undefined} />
        ) : (
          <button onClick={() => setRaw(true)} className="text-xs text-stone-400 hover:text-stone-700">
            {platform ? "보존된 입력 기록" : "원본 기록"}
          </button>
        )}
      </div>
    </li>
  );
}

function Composer({ session, accountId, onSent }: { session: SessionDetail; accountId: string; onSent: () => void }) {
  const { csrfToken, refresh } = useAuth();
  // Composer는 세션을 불러온 뒤 브라우저에서만 그려지므로 sessionStorage를 바로 읽어도 된다.
  const [pending, setPending] = useState<PendingSend | null>(() => loadPendingSend(accountId, session.id));
  const [text, setText] = useState(() => pending?.text ?? "");
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [busy, setBusy] = useState(false);
  const running = session.state === "running";

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body = text.trim();
    if (!body || busy) return;
    if (!csrfToken) {
      setFailure({ kind: "http", status: 401, code: "unauthenticated" });
      return;
    }
    // 결과를 모르는 이전 전송과 같은 내용이면 처음 요청을 그대로 다시 보낸다(같은 key·같은 expectedVersion).
    // 서버는 같은 key의 원래 응답을 돌려주므로 모델 turn이 두 번 시작되지 않는다.
    const isRetry = pending !== null && pending.text === body;
    const request: PendingSend = isRetry ? pending : { key: uuidV4(), text: body, expectedVersion: session.version };
    savePendingSend(accountId, session.id, request);
    setPending(request);
    setBusy(true);
    const res = await sendSessionMessage(session.id, request.text, request.expectedVersion, {
      csrfToken,
      idempotencyKey: request.key,
    });
    setBusy(false);
    // 재전송이면 같은 key에 대한 서버 판단(성공 replay·409)만 이전 전송의 결과를 확정한다.
    // 401/CSRF/Origin/정책 거절은 이전 전송이 처리되지 않았다는 증거가 아니므로 보관을 유지한다.
    const settled = isRetry ? settlesRetry(res.ok ? null : res.failure) : res.ok || !isUncertain(res.failure);
    if (settled) {
      clearPendingSend(accountId, session.id);
      setPending(null);
    }
    if (res.ok) {
      setText("");
      setFailure(null);
    } else {
      // 실패해도 입력은 남겨 둔다. 이전 기록은 서버에 보존된다.
      setFailure(res.failure);
      if (res.failure.kind === "http" && (res.failure.status === 401 || res.failure.code === "csrf_invalid")) void refresh();
    }
    onSent();
  }

  const retrying = pending !== null && pending.text === text.trim();

  return (
    <form onSubmit={submit} className="space-y-2">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        disabled={running}
        placeholder={running ? "Codex가 응답하는 중입니다..." : "이어서 질문하기"}
        aria-label="메시지"
        className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm disabled:bg-stone-50"
      />
      {failure && <ErrorNotice failure={failure} prefix="메시지를 보내지 못했습니다." />}
      {pending && (
        <Notice tone="warn">
          이전 전송의 결과를 확인하지 못했습니다. 위 대화 기록에 메시지가 이미 있는지 확인하세요.{" "}
          {retrying
            ? "같은 내용으로 다시 보내면 이미 처리된 요청은 중복 실행되지 않습니다."
            : "내용을 바꿔 보내면 새 요청으로 처리됩니다."}
        </Notice>
      )}
      <button
        type="submit"
        disabled={busy || running || text.trim() === ""}
        className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
      >
        {busy ? "보내는 중..." : retrying ? "다시 보내기" : "보내기"}
      </button>
    </form>
  );
}

function SessionView({ id }: { id: string }) {
  const router = useRouter();
  const { user } = useAuth();
  const { result, refreshFailure, reload } = useLoad(`session:${id}`, () => getSession(id));
  const { run, busy } = useMutation();
  const [archiveFailure, setArchiveFailure] = useState<FetchFailure | null>(null);
  const running = result?.ok === true && result.data.state === "running";

  // 진행 중인 turn은 상세를 다시 조회해 확인한다. 재접속해도 같은 방식으로 이어진다.
  useEffect(() => {
    if (!running) return;
    const timer = setTimeout(() => void reload(), POLL_MS);
    return () => clearTimeout(timer);
    // 일시적 조회 실패(refreshFailure)에도 마지막 기록을 유지한 채 계속 확인한다.
  }, [running, result, refreshFailure, reload]);

  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  // 개인 세션 조회 실패. 공개 문서 조회와는 별개다.
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="대화를 불러오지 못했습니다." />;
  const s = result.data;

  async function archive() {
    if (!window.confirm("이 대화를 보관할까요? 목록에서 숨겨지지만 기록은 보존됩니다.")) return;
    const res = await run(`session:archive:${s.id}:${s.version}`, (ctx) => archiveSession(s.id, s.version, ctx));
    if (res.ok) router.push("/research/?tab=sessions");
    else setArchiveFailure(res.failure);
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-stone-900">{s.title || "(제목 없음)"}</h1>
        <p className="text-xs text-stone-500">
          {SESSION_STATE_LABELS[s.state]} · 시작 {formatDateTime(s.createdAt)} · 최근 {formatDateTime(s.updatedAt)}
        </p>
        <button onClick={() => void archive()} disabled={busy || running} className="text-sm text-stone-500 hover:text-red-700 disabled:opacity-50">
          보관
        </button>
        {archiveFailure && <ErrorNotice failure={archiveFailure} prefix="보관하지 못했습니다." />}
      </header>

      <CodexStatusBanner />

      {s.items.length === 0 ? (
        <p className="text-sm text-stone-500">아직 주고받은 메시지가 없습니다.</p>
      ) : (
        <ol className="space-y-3">
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
      {running && <Notice tone="info">Codex가 응답하는 중입니다. 페이지를 닫았다가 다시 열어도 이어서 확인할 수 있습니다.</Notice>}
      {s.state === "failed" && (
        <Notice tone="error">
          마지막 요청이 실패했습니다.{" "}
          {s.error &&
            (researchErrorMessage({ kind: "http", status: 0, code: s.error.code }) ?? `${s.error.message} (${s.error.code})`)}{" "}
          이전 기록은 보존되어 있으며 새 메시지로 이어서 질문할 수 있습니다. 실패한 요청은 자동으로 다시 실행되지 않습니다.
        </Notice>
      )}

      {user && <Composer key={user.accountId} session={s} accountId={user.accountId} onSent={() => void reload()} />}
    </div>
  );
}

function SessionPageInner() {
  const id = useSearchParams().get("id");
  if (!id) return <Notice tone="error">대화 ID가 없습니다.</Notice>;
  return <SessionView key={id} id={id} />;
}

export default function SessionPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <Link href="/research/?tab=sessions" className="mb-4 inline-block text-sm text-stone-500 hover:text-stone-800">
        ← 대화 목록
      </Link>
      <AuthGate>
        <Suspense>
          <SessionPageInner />
        </Suspense>
      </AuthGate>
    </div>
  );
}
