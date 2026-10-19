// #32 리뷰 M1·M3: 계약을 넘어 저장하지 못한 본문(편집기 버퍼)을 이 탭의 메모리에만 보관한다.
// 서버 초안에는 마지막 유효 본문만 있으므로, 화면 이동(Link)·뒤로가기로 편집기가 사라져도 입력을 잃지 않게
// 같은 초안을 다시 열면 이 버퍼를 경고와 함께 되살린다. 본문이 다시 유효해지거나 초안을 버리면 지운다.
// - 저장소(localStorage 등)에 본문을 평문으로 남기지 않는다. 새로고침·탭 닫기는 떠날 때 확인(beforeunload)이 막는다.
// - 버퍼는 지금 로그인한 계정의 것만 둔다. 명시적 로그아웃이나 다른 계정 로그인 때 모두 지우고(AuthProvider),
//   그 뒤 늦게 끝난 편집기 동작이 이전 계정 버퍼를 다시 만들지 못하게 계정이 다르면 저장하지 않는다.

import type { EditorNode } from "@/lib/blocks/adapter";

export interface BodyBuffer {
  // 버퍼를 만들 때의 서버 초안 revision(초안이 없으면 null). 다시 열 때 같아야 되살린다.
  revision: number | null;
  editor: EditorNode;
}

let owner: string | null = null;
const buffers = new Map<string, BodyBuffer>();

/** 로그인 확인 결과의 계정. 다른 계정이면 이전 버퍼를 모두 지운다(같은 계정의 재인증은 그대로 둔다). */
export function setBodyBufferOwner(accountId: string) {
  if (owner !== accountId) buffers.clear();
  owner = accountId;
}

/** 명시적 로그아웃: 모두 지우고 다시 로그인하기 전까지 저장하지 않는다. */
export function clearAllBodyBuffers() {
  buffers.clear();
  owner = null;
}

/** 버퍼 대상: 초안 ID, 초안 없는 기존 글은 post:ID, 초안 없는 새 글은 new. */
export const bufferTarget = (draftId: string | null | undefined, postId: string | null | undefined) =>
  draftId ? draftId : postId ? `post:${postId}` : "new";

export function loadBodyBuffer(accountId: string, target: string): BodyBuffer | null {
  return accountId === owner ? (buffers.get(target) ?? null) : null;
}

export function saveBodyBuffer(accountId: string, target: string, buffer: BodyBuffer) {
  if (accountId === owner) buffers.set(target, buffer);
}

export function clearBodyBuffer(accountId: string, target: string) {
  if (accountId === owner) buffers.delete(target);
}
