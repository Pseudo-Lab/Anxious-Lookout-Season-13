// 공통 대화창의 계정별 상태(열림·선택한 대화·패널 폭)와 대화별 작성 중 입력(이 탭의 sessionStorage).
// 새로고침이나 같은 탭의 로그인 이동 뒤에도 이어지게 한다. 키에 accountId를 넣어 다른 계정에는 보이지 않으며,
// pendingStore와 같은 접두어라 명시적 로그아웃 때 함께 지워진다. 인증 정보는 담지 않는다.

import { PREFIX } from "@/lib/research/pendingStore";

export interface ChatUiState {
  open: boolean;
  sessionId: string | null;
  /** 사용자가 고른 넓은 화면 패널 폭(px). null이면 기본 폭. 화면에 맞춘 실제 폭은 ChatDock이 정한다. */
  width: number | null;
}

export const CLOSED_CHAT: ChatUiState = { open: false, sessionId: null, width: null };

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
        width: typeof v.width === "number" && Number.isFinite(v.width) && v.width > 0 ? Math.round(v.width) : null,
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

/** 저장된 초안. 없으면 null(빈 문자열은 사용자가 입력을 비운 초안이다). */
export function loadDraft(accountId: string, sessionId: string): string | null {
  try {
    return sessionStorage.getItem(draftKey(accountId, sessionId));
  } catch {
    return null;
  }
}

/**
 * 초안 저장. 빈 입력은 keepEmpty일 때만 빈 초안으로 남긴다(결과 미확인 전송이 있을 때 그 내용 대신 빈 입력을 복원하도록).
 * 그 밖의 빈 입력은 초안이 없는 것과 같으므로 지운다.
 */
export function saveDraft(accountId: string, sessionId: string, text: string, keepEmpty = false): void {
  try {
    if (text || keepEmpty) sessionStorage.setItem(draftKey(accountId, sessionId), text);
    else sessionStorage.removeItem(draftKey(accountId, sessionId));
  } catch {}
}

export function clearDraft(accountId: string, sessionId: string): void {
  try {
    sessionStorage.removeItem(draftKey(accountId, sessionId));
  } catch {}
}
