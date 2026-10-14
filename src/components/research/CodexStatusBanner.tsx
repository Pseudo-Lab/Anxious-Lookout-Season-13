"use client";

import type { FetchResult } from "@/lib/api/client";
import type { CodexStatus } from "@/lib/research/types";
import { useCodexRunner, useCodexStatus } from "@/components/research/CodexStatusProvider";
import { isUncertain } from "@/lib/research/idempotency";
import { own } from "@/lib/research/own";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

// 사용할 수 없는 사유. 계약에 새 코드가 생기면 여기에 문구를 더하고, 모르는 코드는 일반 문구로 표시한다.
const UNAVAILABLE_REASONS: Record<string, string> = {
  not_configured: "서버에 Codex 연결이 설정되어 있지 않습니다.",
  not_enabled_for_account: "이 계정에는 아직 Codex가 열려 있지 않습니다. 지정된 시험 계정에서만 사용할 수 있습니다.",
  auth_expired: "서버의 Codex 인증이 만료되어 재연결이 필요합니다. 운영자가 다시 연결하면 이 대화에서 이어서 질문할 수 있습니다.",
  auth_revoked: "서버의 Codex 인증이 더 이상 유효하지 않습니다. 운영자가 다시 연결해야 합니다.",
  model_unavailable: "고정된 모델을 확인하지 못해 Codex 실행이 중단되었습니다. 운영자가 확인한 뒤 다시 사용할 수 있습니다.",
  policy_refused: "정책에 따라 Codex 사용이 거부되었습니다.",
  budget_exhausted: "Codex 사용 한도에 도달했습니다.",
  provider_rate_limited: "Codex 제공자의 사용 한도에 걸려 지금은 사용할 수 없습니다.",
  unavailable: "Codex에 일시적으로 연결할 수 없습니다.",
};

export { useCodexStatus };

// #10 실행 환경 상태(api-contracts/issue10-user-runners-interface-v1.md). 완료 시점은 약속하지 않는다.
type RunnerAction = "prepare" | "retry" | "resume";
interface RunnerState {
  title: string;
  body: string;
  action?: RunnerAction;
  progress?: boolean;
}
const RUNNER_STATES: Record<string, RunnerState> = {
  not_prepared: { title: "대화 환경이 아직 준비되지 않았습니다", body: "‘대화 준비’를 누르면 내 환경을 준비합니다.", action: "prepare" },
  provisioning: { title: "환경 활성화 중", body: "질문할 수 있도록 환경을 준비하고 있습니다.", progress: true },
  capacity_wait: {
    title: "사용 대기 중",
    body: "지금은 이용자가 많아 환경을 켤 자리를 기다리고 있습니다. 자리가 나면 서버가 접수된 준비 요청을 이어서 처리합니다.",
  },
  runner_unreachable: { title: "환경 연결 확인 중", body: "환경에 잠시 연결하지 못해 상태를 다시 확인하고 있습니다.", progress: true },
  runner_failed: { title: "환경을 준비하지 못했습니다", body: "‘다시 시도’를 누르면 다시 준비합니다.", action: "retry" },
  idle_stopped: {
    title: "환경이 쉬는 중",
    body: "30분 동안 사용하지 않아 환경을 잠시 껐습니다. 대화와 자료는 그대로입니다.",
    action: "resume",
  },
};
const ACTION_LABELS: Record<RunnerAction, string> = { prepare: "대화 준비", retry: "다시 시도", resume: "환경 다시 켜기" };

/** 실행 환경 상태(아는 reason)면 그 안내. */
export function runnerState(result: FetchResult<CodexStatus> | null): RunnerState | null {
  if (!result?.ok || result.data.available || !result.data.reason) return null;
  return own(RUNNER_STATES, result.data.reason) ?? null;
}

/** 실행 환경 준비 버튼(대화 준비·다시 시도·환경 다시 켜기). 누를 때마다 새 준비 요청이다. 준비 대상 계정에만 보인다. */
export function RunnerActionButton({ result }: { result: FetchResult<CodexStatus> | null }) {
  const runner = useCodexRunner();
  const action = runnerState(result)?.action;
  if (!action || !runner.canPrepare) return null;
  return (
    <button
      type="button"
      onClick={() => void runner.prepare()}
      disabled={runner.preparing}
      className="font-medium underline hover:no-underline disabled:opacity-50"
    >
      {runner.preparing ? "요청하는 중..." : ACTION_LABELS[action]}
    </button>
  );
}

/**
 * 보내기를 잠글지. 서버가 접수할 수 없다고 확인한 상태(available:false)와 확정 조회 거절(401/403 등, 이전 API의
 * 권한 없음 포함)은 잠근다. 일시적 조회 실패(네트워크·timeout·5xx)만으로는 잠그지 않는다(서버가 선접수에서 다시 거른다).
 */
export function codexBlocked(result: FetchResult<CodexStatus> | null): boolean {
  if (!result) return false;
  if (!result.ok) return !isUncertain(result.failure);
  return !result.data.available;
}

// Codex 연결 상태. 서버가 보고한 그대로 표시하고, 확인되지 않은 연결을 정상으로 보이게 하지 않는다.
// 모델은 서버가 고정하므로 선택하지 않고 표시만 한다. 인증은 서버가 다루며 이 화면에서 로그인·키 입력을 받지 않는다.
export function CodexStatusView({ result }: { result: FetchResult<CodexStatus> | null }) {
  if (!result) return null;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="Codex 상태를 확인하지 못했습니다." />;
  const s = result.data;
  const model = (
    <>
      {s.model && <span className="block text-xs">모델: {s.model} (서버에서 고정)</span>}
      {s.available && s.accessExpiresAt && (
        <span className="block text-xs">이용 가능 기한: {formatDateTime(s.accessExpiresAt)}까지</span>
      )}
      {s.remainingAdmissions !== undefined && <span className="block text-xs">남은 요청 횟수: {s.remainingAdmissions}</span>}
    </>
  );
  const runner = s.available ? undefined : own(RUNNER_STATES, s.reason);
  if (runner) {
    return (
      <Notice tone={runner.action === "retry" ? "warn" : "info"}>
        <span className="flex items-start gap-2">
          {runner.progress && (
            <span
              aria-hidden="true"
              className="mt-1 inline-block h-3 w-3 shrink-0 rounded-full border-2 border-current border-t-transparent motion-safe:animate-spin"
            />
          )}
          <span>
            <span className="font-semibold">{runner.title}</span> — {runner.body} 써 둔 입력과 이전 대화는 그대로이며, 질문은 자동으로 보내지
            않습니다. <RunnerActionButton result={result} />
            {model}
          </span>
        </span>
      </Notice>
    );
  }
  if (!s.available) {
    const why = own(UNAVAILABLE_REASONS, s.reason) ?? "Codex를 사용할 수 없습니다.";
    return (
      <Notice tone="warn">
        {why} 새 대화를 진행할 수 없지만 이전 대화 기록은 계속 볼 수 있습니다. 다른 모델로 자동 전환하지 않습니다.
        {model}
      </Notice>
    );
  }
  if (s.verification === "fixture") {
    return (
      <Notice tone="warn">
        테스트용 모의 Codex에 연결되어 있습니다. 실제 모델 응답이 아닙니다.
        {model}
      </Notice>
    );
  }
  if (s.verification === "unverified") {
    // available은 접수 가능 상태일 뿐 고정 모델의 실제 응답 성공(entitlement)을 뜻하지 않는다.
    return (
      <Notice tone="info">
        Codex 연결이 설정되어 있으나 실제 응답은 아직 확인되지 않았습니다.
        {model}
      </Notice>
    );
  }
  return (
    <Notice tone="info">
      Codex에 연결되어 있습니다.
      {model}
    </Notice>
  );
}

/** poll: 열린 대화창에서만 true(일시 상태를 다시 조회). 내 연구 화면 등에서는 조회 1회만. */
export default function CodexStatusBanner({ poll = false }: { poll?: boolean }) {
  const { result } = useCodexStatus(poll);
  return <CodexStatusView result={result} />;
}
