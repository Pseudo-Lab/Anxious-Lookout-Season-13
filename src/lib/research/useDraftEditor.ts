"use client";

// #22 문서 서버 초안 자동 저장(api-contracts/issue22-writing-editor.md). 공통 동작은 useServerDraft.
// - 입력이 멈추고 AUTOSAVE_IDLE_MS 뒤, 길어도 AUTOSAVE_MAX_MS 안에 저장한다. 요청은 한 번에 하나이고 응답 revision으로 다음을 보낸다.
// - 결과를 모르는 요청(네트워크·timeout·5xx)은 같은 내용·같은 key로 먼저 다시 보낸 뒤 이후 입력을 보낸다(중복 초안·자기 충돌 방지).
// - 다른 탭·기기 변경(409 draft_revision / active_draft_exists)은 조용히 덮어쓰지 않고 사용자가 고르게 한다.

import { createDraft, getDocumentDraft, getDraft, saveDraft, type DraftInput } from "@/lib/research/api";
import type { Draft } from "@/lib/research/types";
import { useServerDraft, type DraftAdapter, type SaveStatus as AnySaveStatus } from "@/lib/research/useServerDraft";

export { AUTOSAVE_IDLE_MS, AUTOSAVE_MAX_MS } from "@/lib/research/useServerDraft";

export type SaveStatus = AnySaveStatus<Draft>;

export interface DraftBase {
  documentId: string;
  baseDocumentVersion: number;
  baseVersionId: string;
}

const documentDrafts: DraftAdapter<DraftInput, Draft, DraftBase> = {
  scope: "draft",
  same: (a, b) => a.title === b.title && a.content === b.content,
  fingerprint: (v) => `${v.title}\u0000${v.content}`,
  fromDraft: (d) => ({ title: d.title, content: d.content ?? "" }),
  target: (base) => base?.documentId ?? "new",
  create: (v, base, ctx) => createDraft(base ? { ...v, ...base } : v, ctx),
  save: (d, v, ctx) => saveDraft(d.id, v, d.revision, ctx),
  async reread(reason, draft, base) {
    if (reason === "draft_revision" && draft) {
      const r = await getDraft(draft.id);
      return r.ok ? r.data : null;
    }
    if (base) {
      const r = await getDocumentDraft(base.documentId);
      return r.ok ? r.data.draft : null;
    }
    return null;
  },
};

export function useDraftEditor(opts: {
  // 초안이 없을 때의 시작 내용(기존 문서 본문 또는 빈 새 문서)
  initial: DraftInput;
  // 이어 쓸 active 초안
  draft: Draft | null;
  // 기존 문서면 초안을 만들 때의 base. 새 문서면 null
  base: DraftBase | null;
  // 서버 초안이 만들어지거나 바뀔 때마다
  onDraft?: (draft: Draft) => void;
}) {
  return useServerDraft({ adapter: documentDrafts, ...opts });
}
