import { apiUrl, fetchJson, type FetchFailure } from "@/lib/api/client";

// api-contracts/m2-api-v1.md 기준. 토큰은 다루지 않고 서버 session cookie만 사용한다.

export type UserRole = "admin" | "editor" | "commenter";

export interface AuthUser {
  // 서비스 내부 계정 UUID. 서비스 데이터의 사용자 식별은 이 값을 기준으로 한다.
  accountId: string;
  // GitHub 외부 신원 식별자. 서비스 내부 사용자 ID나 소유권 키로 쓰지 않는다.
  githubId: string;
  login: string;
  role: UserRole;
  isApproved: boolean;
}

export type MeResult =
  | { state: "authenticated"; user: AuthUser; csrfToken: string }
  | { state: "unauthenticated" }
  | { state: "error"; failure: FetchFailure };

const ROLES: readonly string[] = ["admin", "editor", "commenter"];

function parseMe(body: unknown): { user: AuthUser; csrfToken: string } | null {
  const b = body as { user?: Record<string, unknown>; csrfToken?: unknown } | null;
  const u = b?.user;
  if (
    !u ||
    typeof u.accountId !== "string" ||
    u.accountId === "" ||
    typeof u.githubId !== "string" ||
    typeof u.login !== "string" ||
    typeof u.role !== "string" ||
    !ROLES.includes(u.role) ||
    typeof u.isApproved !== "boolean" ||
    typeof b.csrfToken !== "string" ||
    b.csrfToken === ""
  ) {
    return null;
  }
  return {
    user: {
      accountId: u.accountId,
      githubId: u.githubId,
      login: u.login,
      role: u.role as UserRole,
      isApproved: u.isApproved,
    },
    csrfToken: b.csrfToken,
  };
}

export async function getMe(): Promise<MeResult> {
  const res = await fetchJson(apiUrl("/auth/me"), parseMe);
  if (res.ok) return { state: "authenticated", ...res.data };
  // 401만 비로그인이다. 5xx(not_ready 등)·비JSON·네트워크 실패는 확인 실패로 남긴다.
  if (res.failure.kind === "http" && res.failure.status === 401) return { state: "unauthenticated" };
  return { state: "error", failure: res.failure };
}

export type LogoutResult = { ok: true } | { ok: false; failure: FetchFailure };

export async function logout(csrfToken: string): Promise<LogoutResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const res = await fetch(apiUrl("/auth/logout"), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-CSRF-Token": csrfToken },
      signal: controller.signal,
    });
    if (res.status === 204) return { ok: true };
    let code: string | undefined;
    if ((res.headers.get("content-type") ?? "").includes("application/json")) {
      const body = (await res.json().catch(() => null)) as { error?: { code?: unknown } } | null;
      if (typeof body?.error?.code === "string") code = body.error.code;
    }
    return { ok: false, failure: { kind: "http", status: res.status, code } };
  } catch {
    return { ok: false, failure: { kind: controller.signal.aborted ? "timeout" : "network" } };
  } finally {
    clearTimeout(timer);
  }
}

// 로그인 시작은 전체 페이지 이동이다(next/link 클라이언트 이동·prefetch 대상이 아님).
export const GITHUB_LOGIN_URL = apiUrl("/auth/github/start");

// callback 실패 시 /auth/login/?auth_error=<code>. allowlist 밖의 값은 일반 실패로 표시한다.
const AUTH_ERROR_MESSAGES: Record<string, string> = {
  access_denied: "GitHub 로그인 동의가 취소되었습니다.",
  invalid_state: "로그인 요청을 확인할 수 없습니다. 처음부터 다시 시도해 주세요.",
  expired: "로그인 요청이 만료되었습니다. 다시 시도해 주세요.",
  github_error: "GitHub와 통신하는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  server_error: "서버에서 로그인을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
};

export function authErrorMessage(code: string): string {
  return Object.hasOwn(AUTH_ERROR_MESSAGES, code)
    ? AUTH_ERROR_MESSAGES[code]
    : "로그인에 실패했습니다. 다시 시도해 주세요.";
}

export const ROLE_LABELS: Record<UserRole, string> = {
  admin: "관리자",
  editor: "편집자",
  commenter: "일반 회원",
};
