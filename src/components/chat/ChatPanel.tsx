"use client";

import { useAuth } from "@/hooks/useAuth";
import { canUseResearch, isAdmin } from "@/lib/auth/api";
import AuthGate from "@/components/research/AuthGate";
import CodexStatusBanner from "@/components/research/CodexStatusBanner";
import { SessionList } from "@/components/research/ItemLists";
import NewSessionForm from "@/components/research/NewSessionForm";
import Notice from "@/components/research/Notice";
import SessionView from "@/components/research/session/SessionView";
import { useChat } from "@/components/chat/ChatProvider";

function PanelContent() {
  const { user } = useAuth();
  const chat = useChat();
  const admin = isAdmin(user);

  // 메뉴·안내용 판단이며 실제 허용 여부는 각 API가 다시 판단한다.
  if (!canUseResearch(user)) {
    return (
      <Notice tone="info">
        대화 기능은 관리자가 승인한 편집자·관리자만 이용할 수 있습니다. 승인 상태가 바뀌었다면 다시 로그인해 주세요.
      </Notice>
    );
  }

  if (chat.sessionId) {
    // 대화 본문은 위에서 아래로 이어지고 입력창은 대화창 아래에 붙는다(SessionView의 Composer).
    // '대화 목록' 버튼은 SessionView 상단 줄에 붙어 있다(#94: 스크롤해도 보인다).
    return (
      <div className="flex min-h-full flex-col">
        <SessionView key={chat.sessionId} id={chat.sessionId} onArchived={chat.leaveSession} onBack={() => chat.openSession(null)} />
      </div>
    );
  }

  return (
    <div className="py-3">
      <SessionList
        compact
        action={
          <div className="space-y-3">
            {/* #94: 연결·실행 환경 상태는 admin에게만 보인다 */}
            {admin && <CodexStatusBanner poll={chat.open} />}
            {/* #119 N1: 제목은 페이지 이름이 아니라 첫 질문에서 만든다 */}
            <NewSessionForm />
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
