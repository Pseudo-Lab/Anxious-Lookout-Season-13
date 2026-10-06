"use client";

import { useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { describeFailure } from "@/lib/api/client";
import { GITHUB_LOGIN_URL } from "@/lib/auth/api";
import GitHubIcon from "./GitHubIcon";

export default function LoginButton() {
  const { status, user, refresh, signOut } = useAuth();
  const [pending, setPending] = useState(false);
  const [logoutError, setLogoutError] = useState("");

  if (status === "loading") return null;

  if (status === "error") {
    return (
      <button
        onClick={() => void refresh()}
        className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1 text-sm text-amber-700 transition-colors hover:bg-amber-100"
      >
        로그인 상태 확인 실패 · 다시 시도
      </button>
    );
  }

  if (status === "unauthenticated" || !user) {
    // 백엔드 경로로의 전체 페이지 이동이므로 next/link가 아닌 <a>를 쓴다.
    return (
      <a
        href={GITHUB_LOGIN_URL}
        className="flex items-center gap-2 rounded-lg bg-stone-900 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-stone-800"
      >
        <GitHubIcon className="h-4 w-4" />
        GitHub 로그인
      </a>
    );
  }

  async function handleSignOut() {
    setPending(true);
    setLogoutError("");
    const result = await signOut();
    if (!result.ok) setLogoutError(`로그아웃 실패: ${describeFailure(result.failure)}`);
    setPending(false);
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-sm font-medium text-stone-600">{user.login}</span>
      {!user.isApproved && (
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
          승인 대기
        </span>
      )}
      <button
        onClick={() => void handleSignOut()}
        disabled={pending}
        className="rounded-lg border border-stone-300 px-3 py-1 text-sm text-stone-500 transition-colors hover:bg-stone-100 disabled:opacity-50"
      >
        {pending ? "로그아웃 중..." : "로그아웃"}
      </button>
      {logoutError && (
        <span role="alert" className="w-full text-xs text-red-600">
          {logoutError}
        </span>
      )}
    </div>
  );
}
