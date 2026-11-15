"use client";

import type { ReactNode } from "react";
import { useAuth } from "@/hooks/useAuth";
import { isAdmin } from "@/lib/auth/api";

/** 승인된 관리자에게만 그린다(#94: 서비스 상태 진입점). 로그인 확인 전·비로그인·정적 배포(API 없음)에서는 그리지 않는다. */
export default function AdminOnly({ children, fallback = null }: { children: ReactNode; fallback?: ReactNode }) {
  const { status, user } = useAuth();
  if (status !== "authenticated" || !isAdmin(user)) return <>{fallback}</>;
  return <>{children}</>;
}
