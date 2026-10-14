// 공통 대화창의 계정별 상태(열림·선택한 대화·넓게 보기)와 대화별 작성 중 입력(이 탭의 sessionStorage).
// 새로고침이나 같은 탭의 로그인 이동 뒤에도 이어지게 한다. 키에 accountId를 넣어 다른 계정에는 보이지 않으며,
// pendingStore와 같은 접두어라 명시적 로그아웃 때 함께 지워진다. 인증 정보는 담지 않는다.

import { PREFIX } from "@/lib/research/pendingStore";

export interface ChatUiState {
  open: boolean;
  sessionId: string | null;
  wide: boolean;
}

export const CLOSED_CHAT: ChatUiState = { open: false, sessionId: null, wide: false };

const uiKey = (accountId: string) => `${PREFIX}${accountId}:chat-ui`;
const draftKey = (accountId: string, sessionId: string) => `${PREFIX}${accountId}:chat-draft:${sessionId}`;

export function loadChatUi(accountId: string): ChatUiState {
  try {
    const raw = sessionStorage.getItem(uiKey(accountId));
    if (raw) {
      const v = JSON.parse(raw) as Partial<ChatUiState>;
      return {
        open: v.open === true,
        sessionId: typeof v.sessionId === "string" ? v.sessionId : null,
        wide: v.wide === true,
      };
    }
  } catch {
    // 저장소를 쓸 수 없으면 메모리 상태만 사용한다.
  }
  return CLOSED_CHAT;
}

export function saveChatUi(accountId: string, state: ChatUiState): void {
  try {
    sessionStorage.setItem(uiKey(accountId), JSON.stringify(state));
  } catch {}
}

export function loadDraft(accountId: string, sessionId: string): string {
  try {
    return sessionStorage.getItem(draftKey(accountId, sessionId)) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(accountId: string, sessionId: string, text: string): void {
  try {
    if (text) sessionStorage.setItem(draftKey(accountId, sessionId), text);
    else sessionStorage.removeItem(draftKey(accountId, sessionId));
  } catch {}
}
