"use client";

import type { FetchResult } from "@/lib/api/client";
import type { CodexStatus } from "@/lib/research/types";
import { useCodexRunner, useCodexStatus } from "@/components/research/CodexStatusProvider";
import { isUncertain } from "@/lib/research/idempotency";
import { own } from "@/lib/research/own";
import { formatDateTime } from "@/lib/research/format";
import Notice from "@/components/research/Notice";

export { useCodexStatus };

interface RunnerState {
  title: string;
  action?: "prepare" | "resume";
  warn?: boolean;
}
const RUNNER_STATES: Record<string, RunnerState> = {
  not_prepared: { title: "환경 준비 필요", action: "prepare" },
  provisioning: { title: "환경 준비 중" },
  capacity_wait: { title: "사용 대기 중" },
  runner_unreachable: { title: "연결 확인 중" },
  stopping: { title: "환경 정리 중" },
  runner_failed: { title: "운영자 확인 필요", warn: true },
  idle_stopped: { title: "쉬는 중", action: "resume" },
};

export function runnerState(result: FetchResult<CodexStatus> | null): RunnerState | null {
  if (!result?.ok || result.data.available || !result.data.reason) return null;
  return own(RUNNER_STATES, result.data.reason) ?? null;
}

/** 실제 준비 요청은 기존 canPrepare와 명시적 클릭으로만 보낸다. */
export function RunnerActionButton({ result }: { result: FetchResult<CodexStatus> | null }) {
  const runner = useCodexRunner();
  const action = runnerState(result)?.action;
  if (!action) return null;
  return <button type="button" onClick={() => void (runner.canPrepare ? runner.prepare() : runner.reload())}
    disabled={runner.preparing} className="font-medium underline hover:no-underline disabled:opacity-50">
    {runner.preparing ? "요청 중" : !runner.canPrepare ? "다시 시도" : action === "resume" ? "다시 켜기" : "준비하기"}
  </button>;
}

/** 일시 조회 실패는 기존 접수 정책대로 보내기를 잠그지 않는다. */
export function codexBlocked(result: FetchResult<CodexStatus> | null): boolean {
  if (!result) return false;
  return result.ok ? !result.data.available : !isUncertain(result.failure);
}

/** 관리자 전용 상태 패널. 모델명·자격 정보는 표시하지 않는다. */
export function CodexStatusView({ result }: { result: FetchResult<CodexStatus> | null }) {
  const runner = useCodexRunner();
  if (!result) return null;
  if (!result.ok) return <Notice tone="warn">연결 확인 실패 · <button type="button" onClick={() => void runner.reload()} className="underline">다시 시도</button></Notice>;
  const s = result.data;
  const state = runnerState(result);
  if (!s.available) return <Notice tone={state?.warn ? "warn" : "info"}>
    {state?.title ?? "연결 안 됨"} · {state?.action ? <RunnerActionButton result={result} /> : <button type="button" onClick={() => void runner.reload()} className="underline">다시 시도</button>}
    {s.reason && <span className="ml-2 text-xs">({s.reason})</span>}
  </Notice>;
  return <Notice tone={s.verification === "fixture" ? "warn" : "info"}>
    {s.verification === "fixture" ? "테스트 연결" : s.verification === "unverified" ? "연결됨 · 응답 미확인" : "연결됨"}
    {/* #119 N2: 상태 뜻 */}
    {s.verification === "unverified" && (
      <span className="block text-xs">실행 환경에 연결되었지만 아직 실제 답변으로 확인하지 않았습니다. 질문을 보내면 확인됩니다.</span>
    )}
    {s.accessExpiresAt && <span className="block text-xs">기한: {formatDateTime(s.accessExpiresAt)}까지</span>}
    {s.remainingAdmissions !== undefined && <span className="block text-xs">남은 요청: {s.remainingAdmissions}</span>}
  </Notice>;
}

export default function CodexStatusBanner({ poll = false }: { poll?: boolean }) {
  const { result } = useCodexStatus(poll);
  return <CodexStatusView result={result} />;
}
