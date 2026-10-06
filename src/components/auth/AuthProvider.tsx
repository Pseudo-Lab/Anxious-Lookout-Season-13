"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { getMe, logout, type AuthUser, type LogoutResult, type MeResult } from "@/lib/auth/api";
import type { FetchFailure } from "@/lib/api/client";
import { API_ENABLED } from "@/lib/constants";

// "disabled": API가 없는 배포(GitHub Pages 정적 export). 인증 조회를 하지 않는다.
export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error" | "disabled";

interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  // status가 "error"일 때의 원인(5xx·비JSON·네트워크). 비로그인으로 취급하지 않는다.
  failure: FetchFailure | null;
  refresh: () => Promise<void>;
  signOut: () => Promise<LogoutResult>;
}

const AuthContext = createContext<AuthContextValue>({
  status: "loading",
  user: null,
  failure: null,
  refresh: async () => {},
  signOut: async () => ({ ok: false, failure: { kind: "network" } }),
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(API_ENABLED ? "loading" : "disabled");
  const [user, setUser] = useState<AuthUser | null>(null);
  // CSRF nonce는 메모리에만 둔다(cookie/localStorage에 저장하지 않음).
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  // me 조회 세대. 로그아웃이 시작·완료될 때 올려서, 그 전에 보낸 조회의 늦은 응답이
  // 로그아웃 이후 상태를 덮어쓰지 않게 한다.
  const generation = useRef(0);

  const applyMe = useCallback((me: MeResult) => {
    if (me.state === "authenticated") {
      setUser(me.user);
      setCsrfToken(me.csrfToken);
      setFailure(null);
    } else if (me.state === "unauthenticated") {
      setUser(null);
      setCsrfToken(null);
      setFailure(null);
    } else {
      // 확인 실패: 이전 사용자 정보로 권한을 추정하지 않는다.
      setUser(null);
      setCsrfToken(null);
      setFailure(me.failure);
    }
    setStatus(me.state);
  }, []);

  const refresh = useCallback(async () => {
    if (!API_ENABLED) return;
    const gen = generation.current;
    const me = await getMe();
    if (gen === generation.current) applyMe(me);
  }, [applyMe]);

  useEffect(() => {
    if (!API_ENABLED) return;
    // 마운트 시 1회 서버 session 상태를 조회한다.
    let active = true;
    const gen = generation.current;
    void getMe().then((me) => {
      if (active && gen === generation.current) applyMe(me);
    });
    return () => {
      active = false;
    };
  }, [applyMe]);

  const signOut = useCallback(async (): Promise<LogoutResult> => {
    if (!csrfToken) return { ok: false, failure: { kind: "http", status: 401, code: "unauthenticated" } };
    // 로그아웃 전·중에 보낸 me 응답은 모두 무효로 한다.
    generation.current += 1;
    const result = await logout(csrfToken);
    generation.current += 1;
    if (result.ok) {
      setUser(null);
      setCsrfToken(null);
      setFailure(null);
      setStatus("unauthenticated");
    } else if (result.failure.kind === "http" && result.failure.status === 403) {
      // CSRF/Origin 거절: session 상태가 바뀌었을 수 있으므로 다시 조회한다(자동 재시도는 하지 않음).
      await refresh();
    }
    return result;
  }, [csrfToken, refresh]);

  return (
    <AuthContext.Provider value={{ status, user, failure, refresh, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuthContext() {
  return useContext(AuthContext);
}
