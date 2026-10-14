"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { FetchFailure, FetchResult } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { canPrepareRunner, canUseResearch } from "@/lib/auth/api";
import { API_ENABLED } from "@/lib/constants";
import { getCodexStatus, prepareCodexRunner } from "@/lib/research/api";
import { isUncertain, uuidV4 } from "@/lib/research/idempotency";
import { own } from "@/lib/research/own";
import type { CodexStatus } from "@/lib/research/types";

// Codex 연결·실행 환경 상태(api-contracts/issue10-user-runners-interface-v1.md). 모든 페이지에서 한 곳만 관리한다.
// - 진입 준비: 준비 요청 대상 계정(canPrepareRunner)이 이 탭에서 확인될 때마다(첫 로드·새로고침·로그인·계정 전환·
//   자격 false→true) 한 번 보낸다. 대화창 열기·페이지 이동·조회·탭 표시 변화·시간 경과로는 보내지 않고, 실패해도
//   자동으로 다시 보내지 않는다(상태 조회만). "대화 준비"·"다시 시도"·"환경 다시 켜기"는 새 key의 새 요청이다.
// - 상태 조회는 읽기 전용이다. 일시 상태(환경 활성화 중·사용 대기·연결 확인)일 때만, 대화창이 열려 있고 탭이 보일
//   때만 다시 조회한다. 조회는 환경을 만들거나 깨우지 않는다.
// - 모든 결과는 시작 당시의 계정·자격 세대(epoch)에 묶는다. 로그아웃·계정 전환·자격 변화 뒤 늦게 온 결과는 버린다.
//   준비 자격만 꺼지고 연구 권한이 남으면 기존(시험·정적) 상태 조회·대화창은 그대로 쓴다.
// - 질문은 어떤 상태에서도 자동으로 보내지 않는다(전송은 Composer가 사용자 동작으로만).

/** 다시 조회하는 일시 상태와 서버 안내가 없을 때의 기본 간격(초). */
const TRANSIENT: Record<string, number> = { provisioning: 2, capacity_wait: 15, runner_unreachable: 5 };
const MAX_DELAY_S = 60;

interface StatusState {
  epoch: number;
  result: FetchResult<CodexStatus> | null;
  refreshFailure: FetchFailure | null;
  // 연속으로 연결 확인 중이거나 조회에 실패한 횟수(간격을 늘린다).
  misses: number;
}

interface CodexStatusContextValue {
  result: FetchResult<CodexStatus> | null;
  refreshFailure: FetchFailure | null;
  /** 상태 조회(읽기 전용). */
  reload: () => Promise<void>;
  /** 사용자가 누른 준비 요청(새 key). */
  prepare: () => Promise<void>;
  preparing: boolean;
  /** 이 계정이 준비 요청을 보낼 수 있는지(버튼 표시용, 판정은 서버). */
  canPrepare: boolean;
  /** 화면에서 상태를 보이는 동안 등록(새 세대에서 다시 조회하고, 열린 대화창만 일시 상태를 다시 조회한다). */
  register: (poll: boolean) => () => void;
}

const noop = async () => {};
const CodexStatusContext = createContext<CodexStatusContextValue>({
  result: null,
  refreshFailure: null,
  reload: noop,
  prepare: noop,
  preparing: false,
  canPrepare: false,
  register: () => () => {},
});

function subscribeVisibility(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

export function CodexStatusProvider({ children }: { children: ReactNode }) {
  const { status, user, csrfToken, refresh } = useAuth();
  const account = status === "authenticated" && user ? user.accountId : null;
  const canPrepare = account !== null && canPrepareRunner(user);
  const research = account !== null && canUseResearch(user);

  // 계정·자격이 바뀔 때마다 세대를 올린다(같은 값의 me 재조회는 그대로). A→B→A도 새 세대다.
  const scopeKey = account === null ? "-" : `${account}|${canPrepare ? 1 : 0}|${research ? 1 : 0}`;
  const [scope, setScope] = useState({ key: scopeKey, epoch: 0 });
  let epoch = scope.epoch;
  if (scope.key !== scopeKey) {
    epoch = scope.epoch + 1;
    setScope({ key: scopeKey, epoch });
  }
  const epochRef = useRef(epoch);
  const tokenRef = useRef(csrfToken);
  const researchRef = useRef(research);
  useLayoutEffect(() => {
    epochRef.current = epoch;
    tokenRef.current = csrfToken;
    researchRef.current = research;
  });

  const [data, setData] = useState<StatusState>({ epoch: 0, result: null, refreshFailure: null, misses: 0 });
  const current: StatusState = data.epoch === epoch ? data : { epoch, result: null, refreshFailure: null, misses: 0 };
  // 조회 순서. 마지막 조회 응답만 반영하고, 준비 요청 응답이 오면 진행 중인 조회 응답은 버린다.
  const seq = useRef(0);

  const apply = useCallback((forEpoch: number, result: FetchResult<CodexStatus>) => {
    setData((prev) => {
      const base = prev.epoch === forEpoch ? prev : { epoch: forEpoch, result: null, refreshFailure: null, misses: 0 };
      // 일시적 조회 실패는 마지막 상태를 유지하고 따로 알린다(확정 응답은 그대로 반영).
      if (!result.ok && isUncertain(result.failure) && base.result?.ok) {
        return { ...base, refreshFailure: result.failure, misses: base.misses + 1 };
      }
      const unreachable = result.ok && result.data.reason === "runner_unreachable";
      return { epoch: forEpoch, result, refreshFailure: null, misses: unreachable || !result.ok ? base.misses + 1 : 0 };
    });
  }, []);

  const reload = useCallback(async () => {
    // 연구 권한이 없는 계정·비로그인은 조회하지 않는다(기존 권한 흐름).
    if (!researchRef.current) return;
    const started = epochRef.current;
    const mine = ++seq.current;
    const result = await getCodexStatus();
    if (started !== epochRef.current || mine !== seq.current) return;
    apply(started, result);
  }, [apply]);

  // 준비 요청 하나. 응답·실패 모두 시작 세대가 지금 세대일 때만 반영한다. 자동으로 다시 보내지 않는다.
  const sendPrepare = useCallback(
    async (started: number, token: string) => {
      const res = await prepareCodexRunner({ csrfToken: token, idempotencyKey: uuidV4() });
      if (started !== epochRef.current) return;
      if (res.ok) {
        seq.current++;
        apply(started, res);
        return;
      }
      const f = res.failure;
      if (f.kind === "http" && (f.status === 401 || f.status === 403)) {
        // 로그인·session·자격이 바뀌었을 수 있다. me를 다시 확인한다(같은 계정·자격이면 세대가 그대로라 다시 보내지 않음).
        void refresh();
        if (f.status === 401 || f.code === "csrf_invalid") return;
      }
      // 403(서버 거절)·404/405(준비 경로가 없는 이전 API)·네트워크·5xx: 상태 조회로 화면을 정한다.
      await reload();
    },
    [apply, refresh, reload]
  );

  // 진입 준비: 이 세대에서 준비 대상이면 한 번만.
  const entrySent = useRef<number | null>(null);
  useEffect(() => {
    if (!API_ENABLED || !canPrepare || !csrfToken) return;
    if (entrySent.current === epoch) return;
    entrySent.current = epoch;
    void sendPrepare(epoch, csrfToken);
  }, [canPrepare, csrfToken, epoch, sendPrepare]);

  const [preparing, setPreparing] = useState<number | null>(null);
  const prepare = useCallback(async () => {
    const started = epochRef.current;
    const token = tokenRef.current;
    if (!token) return;
    setPreparing(started);
    try {
      await sendPrepare(started, token);
    } finally {
      setPreparing((p) => (p === started ? null : p));
    }
  }, [sendPrepare]);

  // 상태를 보이는 화면 수와 그중 다시 조회를 허용한 화면(열린 대화창) 수.
  const [watchers, setWatchers] = useState({ views: 0, pollers: 0 });
  const register = useCallback((poll: boolean) => {
    setWatchers((w) => ({ views: w.views + 1, pollers: w.pollers + (poll ? 1 : 0) }));
    return () => setWatchers((w) => ({ views: w.views - 1, pollers: w.pollers - (poll ? 1 : 0) }));
  }, []);

  // 상태를 보고 있는 동안 세대가 바뀌면(계정·자격 변화) 새 세대로 한 번 조회한다. 화면이 처음 보일 때의 조회는
  // useCodexStatus가 한다. 준비 자격만 꺼지고 연구 권한이 남으면 이 조회로 기존 시험·정적 상태를 다시 보인다.
  const watching = watchers.views > 0;
  const seenEpoch = useRef<number | null>(null);
  useEffect(() => {
    if (!watching) {
      seenEpoch.current = null;
      return;
    }
    if (seenEpoch.current === null) {
      seenEpoch.current = epoch;
      return;
    }
    if (seenEpoch.current !== epoch) {
      seenEpoch.current = epoch;
      void reload();
    }
  }, [watching, epoch, reload]);

  // 일시 상태 다시 조회: 열린 대화창이 있고 탭이 보일 때만, 안내 간격(없으면 기본) ±15% 흔들림, 연결 문제·조회 실패는 늘려 가며 최대 60초.
  const visible = useSyncExternalStore(subscribeVisibility, () => document.visibilityState === "visible", () => true);
  const shown = current.result?.ok ? current.result.data : null;
  // 직접 가진 키만 일시 상태로 본다(`__proto__` 등 상속 속성·모르는 reason은 다시 조회하지 않음).
  const fallbackDelay = shown && !shown.available ? own(TRANSIENT, shown.reason) : undefined;
  const transientReason = fallbackDelay !== undefined && shown ? shown.reason : null;
  const polling = watchers.pollers > 0 && visible && transientReason !== null;
  useEffect(() => {
    if (!polling || !transientReason || !shown || fallbackDelay === undefined) return;
    const hinted = shown.retryAfterSeconds ?? fallbackDelay;
    const base = Number.isFinite(hinted) && hinted >= 1 ? hinted : fallbackDelay;
    const backoff = Math.min(MAX_DELAY_S, base * 2 ** Math.min(current.misses, 4));
    const delay = Math.max(1, Math.min(MAX_DELAY_S, backoff)) * (0.85 + Math.random() * 0.3) * 1000;
    const timer = setTimeout(() => void reload(), delay);
    return () => clearTimeout(timer);
  }, [polling, transientReason, fallbackDelay, shown, current.misses, current.refreshFailure, reload]);

  // 숨었던 탭이 다시 보이면(열린 대화창이 있을 때) 한 번 조회한다. 조회는 준비를 시작하지 않는다.
  const wasVisible = useRef(visible);
  useEffect(() => {
    const before = wasVisible.current;
    wasVisible.current = visible;
    if (!before && visible && watchers.pollers > 0) void reload();
  }, [visible, watchers.pollers, reload]);

  const value = useMemo<CodexStatusContextValue>(
    () => ({
      result: current.result,
      refreshFailure: current.refreshFailure,
      reload,
      prepare,
      preparing: preparing === epoch,
      canPrepare,
      register,
    }),
    [current.result, current.refreshFailure, reload, prepare, preparing, epoch, canPrepare, register]
  );
  return <CodexStatusContext.Provider value={value}>{children}</CodexStatusContext.Provider>;
}

/** 상태를 보이는 화면용: 마운트할 때 한 번 조회하고(기존 동작), poll이면 일시 상태를 다시 조회한다. */
export function useCodexStatus(poll = false) {
  const ctx = useContext(CodexStatusContext);
  const { reload, register } = ctx;
  useEffect(() => register(poll), [register, poll]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return ctx;
}

/** 상태를 직접 보이지 않고 준비 버튼 등만 쓰는 화면용(조회·등록하지 않음). */
export function useCodexRunner() {
  return useContext(CodexStatusContext);
}
