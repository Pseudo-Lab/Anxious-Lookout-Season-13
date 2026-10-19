// #32 리뷰 M1: 계약을 넘어 저장하지 못한 본문(편집기 버퍼)을 이 브라우저에 보관한다.
// 서버 초안에는 마지막 유효 본문만 있으므로, 화면 이동(Link)·뒤로가기·새로고침으로 편집기가 사라져도 입력을 잃지 않게
// 같은 초안을 다시 열면 이 버퍼를 경고와 함께 되살린다. 본문이 다시 유효해지거나 초안을 버리면 지운다.
// 저장소를 쓸 수 없는 환경(사생활 보호 모드 등)에서는 조용히 아무것도 하지 않는다(그때는 떠날 때 확인만 남는다).

import type { EditorNode } from "@/lib/blocks/adapter";

const PREFIX = "post-body-buffer:v1:";

export interface BodyBuffer {
  // 버퍼를 만들 때의 서버 초안 revision(초안이 없으면 null). 다시 열 때 같아야 되살린다.
  revision: number | null;
  editor: EditorNode;
  savedAt: string;
}

const keyOf = (accountId: string, target: string) => `${PREFIX}${accountId}:${target}`;

/** 버퍼 대상: 초안 ID, 초안 없는 기존 글은 post:ID, 초안 없는 새 글은 new. */
export const bufferTarget = (draftId: string | null | undefined, postId: string | null | undefined) =>
  draftId ? draftId : postId ? `post:${postId}` : "new";

export function loadBodyBuffer(accountId: string, target: string): BodyBuffer | null {
  try {
    const raw = window.localStorage.getItem(keyOf(accountId, target));
    if (!raw) return null;
    const v = JSON.parse(raw) as BodyBuffer;
    return v && typeof v === "object" && v.editor && typeof v.editor === "object" && (v.revision === null || Number.isInteger(v.revision)) ? v : null;
  } catch {
    return null;
  }
}

export function saveBodyBuffer(accountId: string, target: string, buffer: BodyBuffer) {
  try {
    window.localStorage.setItem(keyOf(accountId, target), JSON.stringify(buffer));
  } catch {
    // 용량 초과·저장소 차단: 보관하지 못해도 화면 입력과 떠날 때 확인은 그대로다
  }
}

export function clearBodyBuffer(accountId: string, target: string) {
  try {
    window.localStorage.removeItem(keyOf(accountId, target));
  } catch {
    // 위와 같음
  }
}
