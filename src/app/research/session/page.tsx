"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import AuthGate from "@/components/research/AuthGate";
import Notice from "@/components/research/Notice";
import { useChat } from "@/components/chat/ChatProvider";

// 기존 대화 링크(/research/session/?id=) 호환 진입점. 대화는 모든 페이지 공통 대화창에서 연다.
// 같은 대화의 입력창·진행 중 확인이 두 곳에서 돌지 않도록 이 페이지는 대화를 직접 그리지 않는다.
function OpenInChat({ id }: { id: string }) {
  const { open, sessionId, openSession, setOpen } = useChat();

  useEffect(() => {
    openSession(id);
  }, [id, openSession]);

  return (
    <Notice tone="info">
      {open && sessionId === id ? "이 대화를 대화창에서 열었습니다. 다른 페이지로 옮겨도 대화창에서 이어서 질문할 수 있습니다." : "대화창이 닫혀 있습니다."}{" "}
      {!(open && sessionId === id) && (
        <button
          type="button"
          onClick={() => (sessionId === id ? setOpen(true) : openSession(id))}
          className="font-medium underline hover:no-underline"
        >
          이 대화 열기
        </button>
      )}
    </Notice>
  );
}

function SessionPageInner() {
  const id = useSearchParams().get("id");
  if (!id) return <Notice tone="error">대화 ID가 없습니다.</Notice>;
  return <OpenInChat key={id} id={id} />;
}

export default function SessionPage() {
  return (
    <div className="mx-auto max-w-3xl">
      {/* #44: 대화 목록 탭이 없어졌다(대화 목록은 공통 대화창에 있다) */}
      <Link href="/research/" className="mb-4 inline-block text-sm text-stone-500 hover:text-stone-800">
        ← 내 연구
      </Link>
      <AuthGate>
        <Suspense>
          <SessionPageInner />
        </Suspense>
      </AuthGate>
    </div>
  );
}
