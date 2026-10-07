// 결과를 확인하지 못한 세션 메시지 전송(응답 유실·timeout·5xx).
// 같은 의도의 재전송이 새 모델 turn을 만들지 않도록 처음 요청의 Idempotency-Key와 본문(expectedVersion 포함)을
// 그대로 다시 보낸다. 페이지를 새로 열거나 같은 계정으로 다시 로그인해도 이어지도록 계정별 보관소(pendingStore)에 둔다.

import { clearRequest, loadRequest, saveRequest } from "@/lib/research/pendingStore";

export interface PendingSend {
  key: string;
  text: string;
  expectedVersion: number;
}

const scope = (sessionId: string) => `session-send:${sessionId}`;

export function loadPendingSend(accountId: string, sessionId: string): PendingSend | null {
  const r = loadRequest<{ text?: unknown; expectedVersion?: unknown }>(accountId, scope(sessionId));
  if (!r || typeof r.body?.text !== "string" || !Number.isInteger(r.body.expectedVersion)) return null;
  return { key: r.key, text: r.body.text, expectedVersion: r.body.expectedVersion as number };
}

export function savePendingSend(accountId: string, sessionId: string, pending: PendingSend): void {
  saveRequest(accountId, scope(sessionId), {
    fingerprint: pending.text,
    key: pending.key,
    body: { text: pending.text, expectedVersion: pending.expectedVersion },
  });
}

export function clearPendingSend(accountId: string, sessionId: string): void {
  clearRequest(accountId, scope(sessionId));
}
