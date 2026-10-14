"use client";

import { useAuth } from "@/hooks/useAuth";
import { canUseResearch } from "@/lib/auth/api";
import AuthGate from "@/components/research/AuthGate";
import CodexStatusBanner from "@/components/research/CodexStatusBanner";
import { SessionList } from "@/components/research/ItemLists";
import NewSessionForm from "@/components/research/NewSessionForm";
import Notice from "@/components/research/Notice";
import SessionView from "@/components/research/session/SessionView";
import { useChat } from "@/components/chat/ChatProvider";

// 대화창 기본 화면에서 새 대화 제목 기본값: 지금 보고 있는 페이지 제목.
function pageTitle(): string {
  if (typeof document === "undefined") return "";
  return document.title.split(" | ")[0].trim().slice(0, 280);
}

function PanelContent() {
  const { user } = useAuth();
  const chat = useChat();

  // 메뉴·안내용 판단이며 실제 허용 여부는 각 API가 다시 판단한다.
  if (!canUseResearch(user)) {
    return (
      <Notice tone="info">
        Codex 질문은 관리자가 승인한 편집자·관리자만 이용할 수 있습니다. 승인 상태가 바뀌었다면 다시 로그인해 주세요.
      </Notice>
    );
  }

  if (chat.sessionId) {
    // 대화 본문은 위에서 아래로 이어지고 입력창은 대화창 아래에 붙는다(SessionView의 Composer).
    return (
      <div className="flex min-h-full flex-col">
        <button
          type="button"
          onClick={() => chat.openSession(null)}
          className="-ml-2 min-h-11 self-start rounded-md px-2 text-xs text-stone-500 hover:bg-stone-100 hover:text-stone-800 md:min-h-8"
        >
          ← 대화 목록
        </button>
        <SessionView key={chat.sessionId} id={chat.sessionId} onArchived={chat.leaveSession} />
      </div>
    );
  }

  return (
    <div className="py-3">
      <SessionList
        compact
        action={
          <div className="space-y-3">
            <CodexStatusBanner poll={chat.open} />
            <NewSessionForm defaultTitle={pageTitle()} />
          </div>
        }
      />
    </div>
  );
}

/** 대화창 내용: 로그인·권한 안내, 대화 목록·새 대화, 선택한 대화. */
export default function ChatPanel() {
  return (
    <div className="flex min-h-full flex-col [&>p]:my-3">
      <AuthGate>
        <PanelContent />
      </AuthGate>
    </div>
  );
}
