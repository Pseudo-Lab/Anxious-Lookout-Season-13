"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FetchResult } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { useIdempotencyKey } from "@/lib/research/idempotency";
import type { MutationContext } from "@/lib/research/api";

/**
 * 조회 결과 상태. key가 바뀌면 다시 불러오고, 늦게 도착한 이전 응답은 버린다.
 * result가 null이면 불러오는 중.
 */
export function useLoad<T>(key: string, loader: () => Promise<FetchResult<T>>) {
  const [state, setState] = useState<{ key: string; result: FetchResult<T> } | null>(null);
  const loaderRef = useRef(loader);
  const keyRef = useRef(key);
  // reload 요청 순서. 마지막 요청의 응답만 반영한다.
  const seq = useRef(0);

  useEffect(() => {
    loaderRef.current = loader;
    keyRef.current = key;
  });

  useEffect(() => {
    let active = true;
    void loaderRef.current().then((result) => {
      if (active) setState({ key, result });
    });
    return () => {
      active = false;
    };
  }, [key]);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    const forKey = keyRef.current;
    const result = await loaderRef.current();
    if (mine === seq.current && forKey === keyRef.current) setState({ key: forKey, result });
  }, []);

  const setResult = useCallback((result: FetchResult<T>) => {
    // 진행 중인 reload 응답이 이 결과를 덮어쓰지 않게 한다.
    seq.current++;
    setState({ key: keyRef.current, result });
  }, []);

  // 다른 key의 결과는 보여주지 않는다.
  const result = state && state.key === key ? state.result : null;
  return { result, reload, setResult };
}

/**
 * 변경 요청 실행기. 현재 session의 CSRF nonce와 요청 내용별 Idempotency-Key를 붙인다.
 * fingerprint는 "같은 요청"을 판별하는 문자열(대상·내용)이다.
 */
export function useMutation() {
  const { csrfToken, refresh } = useAuth();
  const { keyFor, settle } = useIdempotencyKey();
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async <T,>(fingerprint: string, fn: (ctx: MutationContext) => Promise<FetchResult<T>>): Promise<FetchResult<T>> => {
      if (!csrfToken) {
        return { ok: false, failure: { kind: "http", status: 401, code: "unauthenticated" } };
      }
      setBusy(true);
      try {
        const result = await fn({ csrfToken, idempotencyKey: keyFor(fingerprint) });
        settle(result.ok ? null : result.failure);
        if (!result.ok && result.failure.kind === "http" && (result.failure.status === 401 || result.failure.code === "csrf_invalid")) {
          // session이 바뀌었을 수 있다. 상태만 다시 확인하고 자동 재시도는 하지 않는다.
          void refresh();
        }
        return result;
      } finally {
        setBusy(false);
      }
    },
    [csrfToken, keyFor, settle, refresh]
  );

  return { run, busy };
}
