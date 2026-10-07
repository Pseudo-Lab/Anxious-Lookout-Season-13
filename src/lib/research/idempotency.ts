"use client";

import { useCallback, useRef } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { clearRequest, loadRequest, saveRequest } from "@/lib/research/pendingStore";

// UUID v4. 공인 IP HTTP처럼 secure context가 아닌 origin에서는 crypto.randomUUID가 없으므로
// 어디서나 쓸 수 있는 crypto.getRandomValues로 만든다.
export function uuidV4(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** 결과를 알 수 없는 실패(서버에 반영됐을 수 있음): 같은 내용의 재시도는 같은 key를 써야 한다. */
export function isUncertain(failure: FetchFailure): boolean {
  return (
    failure.kind === "network" ||
    failure.kind === "timeout" ||
    failure.kind === "invalid" ||
    (failure.kind === "http" && failure.status >= 500 && failure.code !== "policy_pending" && failure.code !== "codex_unavailable")
  );
}

/**
 * 이전 시도의 결과를 모르는 요청을 다시 보낸 뒤, 그 응답이 이전 시도의 결과를 확정하는지.
 * 인증·Origin·CSRF·정책 거절(401/403/503 policy_pending 등)은 서버가 key를 확인하기 전 단계라
 * 이전 시도가 처리되지 않았다는 증거가 아니다. 같은 key로 서버가 판단한 결과(성공 replay, 409)만 확정이다.
 */
export function settlesRetry(failure: FetchFailure | null): boolean {
  if (failure === null) return true;
  return failure.kind === "http" && failure.status === 409;
}

/**
 * 요청 하나(같은 대상·같은 내용)에 Idempotency-Key 하나를 대응시킨다.
 * 결과가 불확실한 실패 뒤 같은 내용을 다시 보내면 같은 key를 재사용해 서버가 중복 처리하지 않게 한다.
 * 불확실한 시도가 있었던 key는 같은 key에 대한 서버 판단(성공·409)이 올 때까지 유지한다.
 * 내용이 바뀌면 새 요청으로 보고 새 key를 쓴다.
 *
 * persist를 주면 불확실한 요청을 계정별 보관소에도 둔다. 401로 화면이 바뀌었다가 같은 계정으로 돌아와도
 * 같은 요청(같은 key·본문)을 복구할 수 있다. 버전 조건(expectedVersion)이 없는 생성 요청에 쓴다.
 */
export function useIdempotencyKey(persist?: { accountId: string; scope: string } | null) {
  const pending = useRef<{ fingerprint: string; key: string; unconfirmed: boolean; body?: unknown } | null>(null);
  const loaded = useRef(false);
  const accountId = persist?.accountId;
  const scope = persist?.scope;

  const current = useCallback(() => {
    if (!loaded.current) {
      loaded.current = true;
      const stored = accountId && scope ? loadRequest(accountId, scope) : null;
      if (stored) pending.current = { fingerprint: stored.fingerprint, key: stored.key, unconfirmed: true, body: stored.body };
    }
    return pending.current;
  }, [accountId, scope]);

  const keyFor = useCallback(
    (fingerprint: string, body?: unknown): string => {
      const p = current();
      if (p?.fingerprint === fingerprint) return p.key;
      pending.current = { fingerprint, key: uuidV4(), unconfirmed: false, body };
      return pending.current.key;
    },
    [current]
  );

  // 응답을 받은 뒤 호출. 결과가 확정되면 key를 버린다.
  const settle = useCallback(
    (failure: FetchFailure | null) => {
      const p = pending.current;
      if (!p) return;
      const done = p.unconfirmed ? settlesRetry(failure) : failure === null || !isUncertain(failure);
      if (done) {
        pending.current = null;
        if (accountId && scope) clearRequest(accountId, scope);
        return;
      }
      p.unconfirmed = true;
      if (accountId && scope) saveRequest(accountId, scope, { fingerprint: p.fingerprint, key: p.key, body: p.body });
    },
    [accountId, scope]
  );

  return { keyFor, settle };
}
