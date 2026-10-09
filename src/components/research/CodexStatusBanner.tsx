"use client";

import { getCodexStatus } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

// 사용할 수 없는 사유. 계약에 새 코드가 생기면 여기에 문구를 더하고, 모르는 코드는 일반 문구로 표시한다.
const UNAVAILABLE_REASONS: Record<string, string> = {
  not_configured: "서버에 Codex 연결이 설정되어 있지 않습니다.",
  not_enabled_for_account: "이 계정에는 아직 Codex가 열려 있지 않습니다. 지정된 시험 계정에서만 사용할 수 있습니다.",
  auth_expired: "서버의 Codex 인증이 만료되었습니다. 운영자가 다시 인증해야 합니다.",
  auth_revoked: "서버의 Codex 인증이 회수되었습니다. 운영자 조치가 필요합니다.",
  model_unavailable: "고정된 모델을 확인하지 못해 Codex 실행이 중단되었습니다. 운영자가 확인한 뒤 다시 사용할 수 있습니다.",
  policy_refused: "정책에 따라 Codex 사용이 거부되었습니다.",
  budget_exhausted: "Codex 사용 한도에 도달했습니다.",
  unavailable: "Codex에 일시적으로 연결할 수 없습니다.",
};

// Codex 연결 상태. 서버가 보고한 그대로 표시하고, 확인되지 않은 연결을 정상으로 보이게 하지 않는다.
// 모델은 서버가 고정하므로 선택하지 않고 표시만 한다. 인증은 서버가 다루며 이 화면에서 로그인·키 입력을 받지 않는다.
export default function CodexStatusBanner() {
  const { result } = useLoad("codex-status", getCodexStatus);
  if (!result) return null;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="Codex 상태를 확인하지 못했습니다." />;
  const s = result.data;
  const model = s.model ? <span className="block text-xs">모델: {s.model} (서버에서 고정)</span> : null;
  if (!s.available) {
    const why = (s.reason && UNAVAILABLE_REASONS[s.reason]) ?? "Codex를 사용할 수 없습니다.";
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
  return model ? <Notice tone="info">{model}</Notice> : null;
}
