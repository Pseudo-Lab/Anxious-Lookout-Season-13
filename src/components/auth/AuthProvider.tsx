"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getMe, logout, type AuthUser, type LogoutResult, type MeResult } from "@/lib/auth/api";
import type { FetchFailure } from "@/lib/api/client";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error";

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
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  // CSRF nonce는 메모리에만 둔다(cookie/localStorage에 저장하지 않음).
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [failure, setFailure] = useState<FetchFailure | null>(null);

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
    applyMe(await getMe());
  }, [applyMe]);

  useEffect(() => {
    // 마운트 시 1회 서버 session 상태를 조회한다.
    let active = true;
    void getMe().then((me) => {
      if (active) applyMe(me);
    });
    return () => {
      active = false;
    };
  }, [applyMe]);

  const signOut = useCallback(async (): Promise<LogoutResult> => {
    if (!csrfToken) return { ok: false, failure: { kind: "http", status: 401, code: "unauthenticated" } };
    const result = await logout(csrfToken);
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
