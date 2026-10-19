"use client";

// #28 글 서버 초안 자동 저장. 동작은 문서(#22)와 같고(useServerDraft), 저장 내용이 PostText 전체다.

import { createPostDraft, getPostDraft, getPostDraftOf, savePostDraft } from "@/lib/posts/api";
import type { PostDraft, PostText } from "@/lib/posts/types";
import { canonicalJson } from "@/lib/blocks/document";
import { emptyDocument } from "@/lib/blocks/types";
import type { BlockDocument } from "@/lib/blocks/types";
import { useServerDraft, type DraftAdapter, type SaveStatus } from "@/lib/research/useServerDraft";

export type PostSaveStatus = SaveStatus<PostDraft>;

export interface PostDraftBase {
  postId: string;
  basePostVersion: number;
  baseVersionId: string;
}

const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

export const sameText = (a: PostText, b: PostText) =>
  a.title === b.title &&
  canonicalJson(a.content) === canonicalJson(b.content) &&
  a.description === b.description &&
  a.coverImageId === b.coverImageId &&
  sameList(a.tags, b.tags) &&
  sameList(a.documentIds, b.documentIds) &&
  sameList(a.materialIds, b.materialIds);

/** 응답(초안·버전·제안·미리보기)에서 입력 모양만 고른다. 주소(slug)·자료 출처는 입력이 아니다. */
export const textOf = (v: Omit<PostText, "content"> & { content?: BlockDocument }): PostText => ({
  title: v.title,
  // 본문이 없는 응답(목록)에서 만들 일은 없다. 그래도 빈 문서로 둔다.
  content: v.content ?? EMPTY_TEXT.content,
  description: v.description,
  tags: [...v.tags],
  coverImageId: v.coverImageId,
  documentIds: [...v.documentIds],
  materialIds: [...v.materialIds],
});

export const EMPTY_TEXT: PostText = { title: "", content: emptyDocument(), description: "", tags: [], coverImageId: null, documentIds: [], materialIds: [] };

const postDrafts: DraftAdapter<PostText, PostDraft, PostDraftBase> = {
  scope: "post-draft",
  same: sameText,
  fingerprint: (v) => JSON.stringify(v),
  fromDraft: (d) => textOf(d),
  target: (base) => base?.postId ?? "new",
  create: (v, base, ctx) => createPostDraft(base ? { ...v, ...base } : v, ctx),
  save: (d, v, ctx) => savePostDraft(d.id, v, d.revision, ctx),
  async reread(reason, draft, base) {
    if (reason === "draft_revision" && draft) {
      const r = await getPostDraft(draft.id);
      return r.ok ? r.data : null;
    }
    if (base) {
      const r = await getPostDraftOf(base.postId);
      return r.ok ? r.data.draft : null;
    }
    return null;
  },
};

export function usePostDraft(opts: {
  initial: PostText;
  draft: PostDraft | null;
  base: PostDraftBase | null;
  onDraft?: (draft: PostDraft) => void;
  start?: PostText | null;
  onSaved?: (draft: PostDraft, saved: PostText) => void;
}) {
  return useServerDraft({ adapter: postDrafts, ...opts });
}
