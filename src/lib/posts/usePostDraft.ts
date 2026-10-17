"use client";

// #28 글 서버 초안 자동 저장. 동작은 문서(#22)와 같고(useServerDraft), 저장 내용이 PostText 전체다.

import { createPostDraft, getPostDraft, getPostDraftOf, savePostDraft } from "@/lib/posts/api";
import type { PostDraft, PostText } from "@/lib/posts/types";
import { useServerDraft, type DraftAdapter, type SaveStatus } from "@/lib/research/useServerDraft";

export type PostSaveStatus = SaveStatus<PostDraft>;

export interface PostDraftBase {
  postId: string;
  basePostVersion: number;
  baseVersionId: string;
}

export const sameText = (a: PostText, b: PostText) =>
  a.title === b.title &&
  a.content === b.content &&
  a.description === b.description &&
  a.slug === b.slug &&
  a.coverImageId === b.coverImageId &&
  a.tags.length === b.tags.length &&
  a.tags.every((t, i) => t === b.tags[i]) &&
  a.documentIds.length === b.documentIds.length &&
  a.documentIds.every((d, i) => d === b.documentIds[i]);

export const textOf = (v: PostText & { content?: string }): PostText => ({
  title: v.title,
  content: v.content ?? "",
  description: v.description,
  slug: v.slug,
  tags: [...v.tags],
  coverImageId: v.coverImageId,
  documentIds: [...v.documentIds],
});

export const EMPTY_TEXT: PostText = { title: "", content: "", description: "", slug: "", tags: [], coverImageId: null, documentIds: [] };

const postDrafts: DraftAdapter<PostText, PostDraft, PostDraftBase> = {
  scope: "post-draft",
  same: sameText,
  fingerprint: (v) => JSON.stringify(v),
  fromDraft: (d) => textOf({ ...d, content: d.content ?? "" }),
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
}) {
  return useServerDraft({ adapter: postDrafts, ...opts });
}
