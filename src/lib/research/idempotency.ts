"use client";

import { useCallback, useRef } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { loadRequest, removeRequest, storeGeneration, writeRequest } from "@/lib/research/pendingStore";

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
    // 5xx라도 정책 대기·Codex 선접수 실패(codex_*)는 서버가 요청을 예약하기 전에 거절한 확정 응답이다.
    (failure.kind === "http" &&
      failure.status >= 500 &&
      failure.code !== "policy_pending" &&
      !(failure.code ?? "").startsWith("codex_"))
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
  const accountId = persist?.accountId ?? null;
  const scope = persist?.scope ?? null;
  const owner = accountId && scope ? `${accountId}\n${scope}` : null;
  const state = useRef<{
    owner: string | null;
    generation: number;
    knownKeys: Set<string>;
    pending: { fingerprint: string; key: string; unconfirmed: boolean; body?: unknown } | null;
  } | null>(null);

  // 소유자(계정·범위)가 바뀌면 이전 소유자의 key·입력을 재사용하지 않고 새로 불러온다.
  const current = useCallback(() => {
    if (!state.current || state.current.owner !== owner) {
      const stored = accountId && scope ? loadRequest(accountId, scope) : null;
      state.current = {
        owner,
        generation: storeGeneration(),
        knownKeys: new Set(stored ? [stored.key] : []),
        pending: stored ? { fingerprint: stored.fingerprint, key: stored.key, unconfirmed: true, body: stored.body } : null,
      };
    }
    return state.current;
  }, [owner, accountId, scope]);

  const keyFor = useCallback(
    (fingerprint: string, body?: unknown): string => {
      const s = current();
      if (s.pending?.fingerprint === fingerprint) return s.pending.key;
      const key = uuidV4();
      s.knownKeys.add(key);
      s.pending = { fingerprint, key, unconfirmed: false, body };
      return key;
    },
    [current]
  );

  // 응답을 받은 뒤 그 요청의 key로 호출한다. 그 사이 다른 요청·소유자로 바뀌었으면 아무것도 하지 않는다.
  const settle = useCallback((key: string, failure: FetchFailure | null) => {
    const s = state.current;
    const p = s?.pending;
    if (!s || !p || p.key !== key) return;
    const done = p.unconfirmed ? settlesRetry(failure) : failure === null || !isUncertain(failure);
    const [acct, sc] = s.owner ? s.owner.split("\n") : [null, null];
    if (done) {
      s.pending = null;
      if (acct && sc) removeRequest(acct, sc, s);
      return;
    }
    s.pending = { ...p, unconfirmed: true };
    if (acct && sc) writeRequest(acct, sc, { fingerprint: p.fingerprint, key: p.key, body: p.body }, s);
  }, []);

  return { keyFor, settle };
}
