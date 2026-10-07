"use client";

import { useCallback, useRef } from "react";
import type { FetchFailure } from "@/lib/api/client";

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
 * 요청 하나(같은 대상·같은 내용)에 Idempotency-Key 하나를 대응시킨다.
 * 결과가 불확실한 실패 뒤 같은 내용을 다시 보내면 같은 key를 재사용해 서버가 중복 처리하지 않게 하고,
 * 내용이 바뀌었거나 결과가 확정되면 새 key를 쓴다.
 */
export function useIdempotencyKey() {
  const pending = useRef<{ fingerprint: string; key: string } | null>(null);

  const keyFor = useCallback((fingerprint: string): string => {
    if (pending.current?.fingerprint !== fingerprint) {
      pending.current = { fingerprint, key: uuidV4() };
    }
    return pending.current.key;
  }, []);

  // 응답을 받은 뒤 호출. 확정된 결과(성공·거절)면 key를 버린다.
  const settle = useCallback((failure: FetchFailure | null) => {
    if (failure === null || !isUncertain(failure)) pending.current = null;
  }, []);

  return { keyFor, settle };
}
