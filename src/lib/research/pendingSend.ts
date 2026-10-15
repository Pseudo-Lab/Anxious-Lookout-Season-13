// 결과를 확인하지 못한 세션 메시지 전송(응답 유실·timeout·5xx).
// 같은 의도의 재전송이 새 모델 turn을 만들지 않도록 처음 요청의 Idempotency-Key와 본문(expectedVersion 포함)을
// 그대로 다시 보낸다. 페이지를 새로 열거나 같은 계정으로 다시 로그인해도 이어지도록 계정별 보관소(pendingStore)에 둔다.

import { loadRequest, removeRequest, writeRequest, type WriteGuard } from "@/lib/research/pendingStore";
import type { PageDescriptor } from "@/lib/research/types";

export interface PendingSend {
  key: string;
  text: string;
  expectedVersion: number;
  // 보낼 때의 페이지. 재전송은 지금 페이지가 아니라 이 값을 그대로 보낸다(같은 key·같은 body). 이전 보관분에는 없다.
  page?: PageDescriptor;
}

const scope = (sessionId: string) => `session-send:${sessionId}`;

export function loadPendingSend(accountId: string, sessionId: string): PendingSend | null {
  const r = loadRequest<{ text?: unknown; expectedVersion?: unknown; page?: unknown }>(accountId, scope(sessionId));
  if (!r || typeof r.body?.text !== "string" || !Number.isInteger(r.body.expectedVersion)) return null;
  const out: PendingSend = { key: r.key, text: r.body.text, expectedVersion: r.body.expectedVersion as number };
  const page = r.body.page as { kind?: unknown; resourceId?: unknown } | undefined;
  if (page !== undefined) {
    // 보관한 body와 다르게 보내면 같은 key에서 409가 되므로, 알아볼 수 없는 보관분은 복구하지 않는다.
    if (typeof page !== "object" || page === null || typeof page.kind !== "string") return null;
    out.page = { kind: page.kind as PageDescriptor["kind"], ...(typeof page.resourceId === "string" && { resourceId: page.resourceId }) };
  }
  return out;
}

// guard: 로그아웃 이후(세대 변경)나 이 화면이 모르는 더 새 전송이 있으면 쓰거나 지우지 않는다.
export function savePendingSend(accountId: string, sessionId: string, pending: PendingSend, guard: WriteGuard): void {
  writeRequest(
    accountId,
    scope(sessionId),
    {
      fingerprint: pending.text,
      key: pending.key,
      body: { text: pending.text, expectedVersion: pending.expectedVersion, ...(pending.page && { page: pending.page }) },
    },
    guard
  );
}

export function clearPendingSend(accountId: string, sessionId: string, guard: WriteGuard): void {
  removeRequest(accountId, scope(sessionId), guard);
}
