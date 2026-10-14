"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useAuth } from "@/hooks/useAuth";
import { CLOSED_CHAT, loadChatUi, saveChatUi, type ChatUiState } from "@/lib/research/chatState";

// 모든 페이지 공통 대화창 상태. layout에 있어 페이지를 옮겨도 유지된다.
// 상태는 계정에 묶인다. 계정이 바뀌면 그 계정의 저장 상태로 바꿔 이전 계정의 대화 선택이 보이지 않게 한다
// (대화창 내용은 ChatDock이 계정별로 새로 그린다). 비로그인 상태는 저장하지 않는다.

interface ChatContextValue extends ChatUiState {
  setOpen: (open: boolean) => void;
  /** 대화를 대화창에 열고 창을 연다. null이면 대화 목록. */
  openSession: (sessionId: string | null) => void;
  /** 그 대화가 아직 열려 있을 때만 대화 목록으로 돌아간다(보관 완료 등 늦은 완료가 다른 대화를 닫지 않게). */
  leaveSession: (sessionId: string) => void;
  /** 넓은 화면 패널 폭(px). null이면 기본 폭으로 되돌린다. */
  setWidth: (width: number | null) => void;
}

const ChatContext = createContext<ChatContextValue>({
  ...CLOSED_CHAT,
  setOpen: () => {},
  openSession: () => {},
  leaveSession: () => {},
  setWidth: () => {},
});

export function ChatProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const accountId = user?.accountId ?? null;
  // changed: 사용자가 대화창을 조작한 뒤에만 저장한다(열어 본 적 없으면 아무것도 남기지 않는다).
  const [stored, setStored] = useState<{ accountId: string | null; ui: ChatUiState; changed: boolean }>({
    accountId: null,
    ui: CLOSED_CHAT,
    changed: false,
  });

  // 계정이 바뀐 렌더에서 바로 그 계정 상태로 바꾼다(이전 계정 상태를 한 번도 그리지 않게).
  let current = stored;
  if (stored.accountId !== accountId) {
    // 로그아웃·인증 만료로 비로그인이 되면 열려 있던 창은 열어 둔 채 로그인 안내를 보여 준다(대화 선택은 버린다).
    current = {
      accountId,
      ui: accountId ? loadChatUi(accountId) : { ...CLOSED_CHAT, open: stored.ui.open },
      changed: false,
    };
    setStored(current);
  }
  const ui = current.ui;

  useEffect(() => {
    if (stored.accountId && stored.changed) saveChatUi(stored.accountId, stored.ui);
  }, [stored]);

  // 조작 함수는 그 함수를 받은 화면의 계정에 묶는다. 계정이 바뀐 뒤 이전 계정 화면의 늦은 완료(대화 생성·보관 등)가
  // 부르는 조작은 무시해 새 계정의 대화창 상태를 바꾸지 못하게 한다.
  const update = useCallback(
    (fn: (s: ChatUiState) => ChatUiState) =>
      setStored((prev) => (prev.accountId !== accountId ? prev : { accountId: prev.accountId, ui: fn(prev.ui), changed: true })),
    [accountId]
  );
  const setOpen = useCallback((open: boolean) => update((s) => ({ ...s, open })), [update]);
  const openSession = useCallback((sessionId: string | null) => update((s) => ({ ...s, open: true, sessionId })), [update]);
  const leaveSession = useCallback(
    (sessionId: string) => update((s) => (s.sessionId === sessionId ? { ...s, sessionId: null } : s)),
    [update]
  );
  const setWidth = useCallback(
    (width: number | null) => update((s) => (s.width === width ? s : { ...s, width })),
    [update]
  );

  const value = useMemo(
    () => ({ ...ui, setOpen, openSession, leaveSession, setWidth }),
    [ui, setOpen, openSession, leaveSession, setWidth]
  );
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  return useContext(ChatContext);
}
