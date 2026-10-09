"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FetchFailure, FetchResult } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { isUncertain, useIdempotencyKey } from "@/lib/research/idempotency";
import type { MutationContext } from "@/lib/research/api";
import { loadRequest } from "@/lib/research/pendingStore";

interface LoadState<T> {
  key: string;
  result: FetchResult<T>;
  // 성공 데이터를 가진 상태에서 다시 불러오기가 실패한 경우. 마지막 데이터(와 그 위의 입력)는 유지한다.
  refreshFailure: FetchFailure | null;
}

/**
 * 조회 결과 상태. key가 바뀌면 다시 불러오고, 늦게 도착한 이전 응답은 버린다.
 * result가 null이면 불러오는 중.
 * reload가 일시적으로 실패해도 이미 받은 성공 데이터를 실패로 바꾸지 않고 refreshFailure로 따로 알린다.
 * (계정이 바뀌면 AuthGate가 화면 전체를 새로 그려 이전 계정 상태를 버린다.)
 */
export function useLoad<T>(key: string, loader: () => Promise<FetchResult<T>>) {
  const [state, setState] = useState<LoadState<T> | null>(null);
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
      if (active) setState({ key, result, refreshFailure: null });
    });
    return () => {
      active = false;
    };
  }, [key]);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    const forKey = keyRef.current;
    const result = await loaderRef.current();
    if (mine !== seq.current || forKey !== keyRef.current) return;
    setState((prev) => {
      // 일시적 실패(네트워크·timeout·비JSON·5xx)만 마지막 데이터를 유지한다.
      // 401/403/404 같은 확정 응답은 그대로 반영해 권한이 없어진 데이터를 계속 보여주지 않는다.
      if (!result.ok && isUncertain(result.failure) && prev && prev.key === forKey && prev.result.ok) {
        return { ...prev, refreshFailure: result.failure };
      }
      return { key: forKey, result, refreshFailure: null };
    });
  }, []);

  const setResult = useCallback((result: FetchResult<T>) => {
    // 진행 중인 reload 응답이 이 결과를 덮어쓰지 않게 한다.
    seq.current++;
    setState({ key: keyRef.current, result, refreshFailure: null });
  }, []);

  // 다른 key의 결과는 보여주지 않는다.
  const current = state && state.key === key ? state : null;
  return { result: current?.result ?? null, refreshFailure: current?.refreshFailure ?? null, reload, setResult };
}

/**
 * 변경 요청 실행기. 현재 session의 CSRF nonce와 요청 내용별 Idempotency-Key를 붙인다.
 * fingerprint는 "같은 요청"을 판별하는 문자열(대상·내용)이다.
 * persistScope를 주면 결과 미확인 요청을 계정별로 보관해 재인증 뒤에도 같은 요청으로 복구한다
 * (버전 조건이 없는 생성 요청용. body는 복구할 입력값).
 */
export function useMutation(persistScope?: string) {
  const { csrfToken, user, refresh } = useAuth();
  const { keyFor, settle } = useIdempotencyKey(
    persistScope && user ? { accountId: user.accountId, scope: persistScope } : null
  );
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async <T,>(
      fingerprint: string,
      fn: (ctx: MutationContext) => Promise<FetchResult<T>>,
      body?: unknown
    ): Promise<FetchResult<T>> => {
      if (!csrfToken) {
        return { ok: false, failure: { kind: "http", status: 401, code: "unauthenticated" } };
      }
      setBusy(true);
      try {
        const key = keyFor(fingerprint, body);
        const result = await fn({ csrfToken, idempotencyKey: key });
        settle(key, result.ok ? null : result.failure);
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

/** 결과 미확인으로 보관된 생성 요청의 입력값(같은 계정만). 폼 초기값으로 복구한다. */
export function useRestoredRequest<T>(scope: string): T | null {
  const { user } = useAuth();
  const [restored] = useState<T | null>(() => (user ? (loadRequest<T>(user.accountId, scope)?.body ?? null) : null));
  return restored;
}
