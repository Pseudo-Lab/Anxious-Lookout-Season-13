"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useAuth } from "@/hooks/useAuth";
import { describeFailure } from "@/lib/api/client";
import { GITHUB_LOGIN_URL } from "@/lib/auth/api";
import Notice from "@/components/research/Notice";

// 개인 자료 화면의 공통 진입 조건. 로그인 여부만 확인하고,
// 승인·역할에 따른 이용 가능 여부는 추정하지 않고 각 API의 401/403 응답으로 표시한다.
export default function AuthGate({ children }: { children: ReactNode }) {
  const { status, failure, refresh } = useAuth();

  if (status === "disabled") {
    return <Notice tone="info">이 정적 사이트에는 API 서버가 없어 개인 자료 기능을 제공하지 않습니다.</Notice>;
  }
  if (status === "loading") {
    return <Notice tone="info">로그인 상태를 확인하는 중...</Notice>;
  }
  if (status === "error") {
    return (
      <Notice tone="warn">
        로그인 상태를 확인하지 못했습니다. {failure && describeFailure(failure)}{" "}
        <button onClick={() => void refresh()} className="underline hover:text-amber-900">
          다시 시도
        </button>
      </Notice>
    );
  }
  if (status === "unauthenticated") {
    return (
      <Notice tone="info">
        로그인이 필요합니다.{" "}
        {/* 로그인 시작은 전체 페이지 이동 */}
        <a href={GITHUB_LOGIN_URL} className="font-medium underline">
          GitHub로 로그인
        </a>{" "}
        · <Link href="/auth/login/" className="underline">로그인 화면</Link>
      </Notice>
    );
  }
  return <>{children}</>;
}
