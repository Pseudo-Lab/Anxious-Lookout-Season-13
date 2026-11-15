"use client";

import type { ReactNode } from "react";
import { useAuth } from "@/hooks/useAuth";
import { isAdmin } from "@/lib/auth/api";

// #94: 서비스 상태(연결·배포 버전·로그인 상태)는 관리자에게만 보인다. 주소로 직접 들어온 일반 사용자에게는 이동시키지 않고
// 짧은 안내만 보인다(어디로 왜 옮겨졌는지 헷갈리지 않게). 실제 API 권한은 서버가 판단한다.
export default function StatusGate({ children }: { children: ReactNode }) {
  const { status, user } = useAuth();
  if (status === "loading") return <p className="text-sm text-stone-500">확인하는 중...</p>;
  if (status !== "authenticated" || !isAdmin(user)) {
    return <p className="text-sm text-stone-500">서비스 상태는 관리자만 볼 수 있습니다.</p>;
  }
  return <>{children}</>;
}
