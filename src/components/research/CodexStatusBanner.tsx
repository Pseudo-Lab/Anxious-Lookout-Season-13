"use client";

import { getCodexStatus } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

// Codex 연결 상태. 서버가 보고한 그대로 표시하고, 확인되지 않은 연결을 정상으로 보이게 하지 않는다.
export default function CodexStatusBanner() {
  const { result } = useLoad("codex-status", getCodexStatus);
  if (!result) return null;
  if (!result.ok) return <ErrorNotice failure={result.failure} prefix="Codex 상태를 확인하지 못했습니다." />;
  const s = result.data;
  if (!s.available) {
    return (
      <Notice tone="warn">
        Codex에 연결되어 있지 않아 새 대화를 진행할 수 없습니다
        {s.reason === "not_configured" ? " (설정되지 않음)" : ""}. 이전 대화 기록은 계속 볼 수 있습니다.
      </Notice>
    );
  }
  if (s.verification === "fixture") {
    return <Notice tone="warn">테스트용 모의 Codex에 연결되어 있습니다. 실제 모델 응답이 아닙니다.</Notice>;
  }
  if (s.verification === "unverified") {
    return <Notice tone="info">Codex 연결이 설정되어 있으나 실제 응답은 아직 확인되지 않았습니다.</Notice>;
  }
  return null;
}
